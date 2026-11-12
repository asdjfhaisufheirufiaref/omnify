"""Tests for backup policy, discovery and destructive-operation safety."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from omnify.core.backup import (
    MANIFEST_NAME,
    BackupConfig,
    BackupManager,
)
from omnify.core.module_context import ModuleContext
from omnify.core.storage import Storage
from omnify.modules.notes import NotesModule
from omnify.modules.notes_engine import Note, NoteStore


def _write_archive(
    path: Path,
    *,
    manifest: bool = True,
    markdown: bool = True,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if manifest:
            archive.writestr(
                MANIFEST_NAME,
                json.dumps({"app": "omnify", "kind": "notes", "format": 1}),
            )
        if markdown:
            archive.writestr("note.md", "# Note\n")
    return path


class BackupConfigTests(unittest.TestCase):
    def test_config_roundtrip_and_derived_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(temporary)
            config = BackupConfig(
                interval_value=6,
                interval_unit="hours",
                destination=str(Path(temporary) / "custom"),
                limit_count=3,
                auto_prune=True,
                prune_value=2,
                prune_unit="months",
            )
            config.save(storage)

            loaded = BackupConfig.load(storage)
            self.assertEqual(loaded.interval_minutes, 360)
            self.assertEqual(loaded.prune_months, 2)
            self.assertEqual(loaded.limit_count, 3)
            self.assertTrue(loaded.auto_prune)
            self.assertEqual(
                loaded.destination_path(),
                (Path(temporary) / "custom").resolve(),
            )

    def test_invalid_values_are_normalised(self) -> None:
        config = BackupConfig.from_dict(
            {
                "interval_value": 0,
                "interval_unit": "centuries",
                "limit_count": -4,
                "prune_unit": "days",
                "last_limit_notice_count": "nope",
            }
        )
        self.assertEqual(config.interval_value, 1)
        self.assertEqual(config.interval_unit, "days")
        self.assertEqual(config.limit_count, 1)
        self.assertEqual(config.prune_unit, "years")
        self.assertEqual(config.last_limit_notice_count, 0)


class BackupManagerTests(unittest.TestCase):
    def test_new_backup_is_recognised_and_can_be_starred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = NoteStore(base / "notes")
            store.save(Note(label="Backup me", contents="Important"))
            directory = base / "backups"
            archive = store.create_backup(directory)
            manager = BackupManager(BackupConfig(destination=str(directory)))

            with zipfile.ZipFile(archive) as compressed:
                self.assertIn(MANIFEST_NAME, compressed.namelist())

            records = manager.list_backups()
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].managed)
            self.assertFalse(records[0].legacy)
            self.assertFalse(records[0].starred)

            starred = manager.star(archive)
            self.assertTrue(starred.name.endswith("_STAR.backup"))
            self.assertTrue(starred.is_file())
            records = manager.list_backups()
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].starred)

            unstarred = manager.unstar(starred)
            self.assertEqual(unstarred, archive)
            self.assertTrue(archive.is_file())
            self.assertFalse(manager.list_backups()[0].starred)

    def test_creating_a_backup_does_not_prune_existing_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = base / "backups"
            old = [
                _write_archive(directory / f"notes-202601{i:02d}-000000.zip")
                for i in range(1, 13)
            ]
            store = NoteStore(base / "notes")
            store.save(Note(label="Current", contents="Text"))
            store.create_backup(directory)

            self.assertTrue(all(path.is_file() for path in old))
            self.assertEqual(len(list(directory.glob("notes-*.zip"))), 13)

    def test_two_backups_in_one_second_do_not_overwrite_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = base / "backups"
            store = NoteStore(base / "notes")
            store.save(Note(label="Current", contents="Text"))
            first = store.create_backup(directory)
            second = store.create_backup(directory)

            self.assertNotEqual(first, second)
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())
            manager = BackupManager(BackupConfig(destination=str(directory)))
            self.assertEqual(len(manager.list_backups()), 2)

    def test_delete_refuses_non_omnify_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = base / "backups"
            directory.mkdir()
            other = directory / "other-program.zip"
            other.write_bytes(b"not an omnify backup")
            manager = BackupManager(BackupConfig(destination=str(directory)))

            with self.assertRaises(ValueError):
                manager.delete(other)
            self.assertTrue(other.is_file())
            self.assertEqual(manager.list_backups(), [])

    def test_prune_only_removes_old_unstarred_managed_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "backups"
            old_managed = _write_archive(directory / "notes-20200101-000000.zip")
            old_starred = _write_archive(
                directory / "notes-20200101-000001_STAR.backup"
            )
            old_legacy = _write_archive(
                directory / "notes-20200101-000002.zip", manifest=False
            )
            recent = _write_archive(directory / "notes-20990101-000000.zip")
            unrelated = directory / "other-backup.zip"
            unrelated.write_bytes(b"other")

            manager = BackupManager(
                BackupConfig(
                    destination=str(directory),
                    auto_prune=True,
                    prune_value=1,
                    prune_unit="months",
                )
            )
            removed = manager.prune_old(now=datetime(2026, 1, 1))

            self.assertEqual(removed, 1)
            self.assertFalse(old_managed.exists())
            self.assertTrue(old_starred.exists())
            self.assertTrue(old_legacy.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_limit_notice_is_sent_once_until_count_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "backups"
            first = _write_archive(directory / "notes-20260101-000000.zip")
            second = _write_archive(directory / "notes-20260102-000000.zip")
            config = BackupConfig(destination=str(directory), limit_count=1)
            manager = BackupManager(config)

            self.assertEqual(manager.take_limit_notice(), (2, 1))
            self.assertIsNone(manager.take_limit_notice())

            manager.delete(first)
            self.assertIsNone(manager.take_limit_notice())
            self.assertEqual(config.last_limit_notice_count, 0)
            manager.delete(second)

    def test_notes_module_restore_creates_safety_backup_headlessly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            storage = Storage(base / "data")
            directory = base / "backups"
            BackupConfig(destination=str(directory)).save(storage)
            module = NotesModule(
                ModuleContext(storage, get_module=lambda _module_id: None)
            )
            store = NoteStore(base / "notes")
            module._store = store
            note = Note(label="Restore me", contents="Original")
            store.save(note)
            archive = store.create_backup(directory)
            note.contents = "Changed"
            store.save(note)

            restored = module.restore_backup_archive(archive)

            self.assertGreaterEqual(restored, 1)
            self.assertEqual(store.load()[0].contents, "Original")
            manager = BackupManager(BackupConfig(destination=str(directory)))
            self.assertEqual(len(manager.list_backups()), 2)

    def test_legacy_archives_are_listed_but_never_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "backups"
            legacy = _write_archive(
                directory / "notes-20200101-000000.zip",
                manifest=False,
                markdown=True,
            )
            manager = BackupManager(
                BackupConfig(destination=str(directory), auto_prune=True)
            )
            records = manager.list_backups()
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].legacy)
            self.assertEqual(manager.prune_old(now=datetime(2026, 1, 1)), 0)
            self.assertTrue(legacy.exists())
