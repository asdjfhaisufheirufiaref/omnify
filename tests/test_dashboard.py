"""Tests for the GTK-free dashboard model."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnify.core.backup import BackupConfig
from omnify.core.module_context import ModuleContext
from omnify.core.storage import Storage
from omnify.modules.dashboard import DashboardModule, TOOL_MODULES
from omnify.modules.notes_engine import Note, NoteStore


class DashboardTests(unittest.TestCase):
    def test_tool_tiles_include_notes_and_have_unique_ids(self) -> None:
        ids = [module.id for module in TOOL_MODULES]
        self.assertIn("notes", ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_notes_tile_has_a_description(self) -> None:
        notes = next(module for module in TOOL_MODULES if module.id == "notes")
        self.assertTrue(notes.name)
        self.assertTrue(notes.description)

    def test_backup_glance_shows_count_and_limit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            storage = Storage(base / "data")
            directory = base / "backups"
            BackupConfig(destination=str(directory), limit_count=1).save(storage)
            store = NoteStore(base / "notes")
            store.save(Note(label="Current", contents="Text"))
            store.create_backup(directory)
            store.create_backup(directory)
            module = DashboardModule(ModuleContext(storage))

            text = module._backup_text()

            self.assertIn("2 backups", text)
            self.assertIn("limit exceeded", text)

    def test_backup_glance_is_unlimited_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            storage = Storage(base / "data")
            directory = base / "backups"
            store = NoteStore(base / "notes")
            store.save(Note(label="Current", contents="Text"))
            store.create_backup(directory)
            store.create_backup(directory)
            BackupConfig(destination=str(directory)).save(storage)
            module = DashboardModule(ModuleContext(storage))

            text = module._backup_text()

            self.assertIn("2 backups", text)
            self.assertNotIn("limit exceeded", text)


if __name__ == "__main__":
    unittest.main()
