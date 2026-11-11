"""Tests for Markdown note persistence and image attachments."""

from __future__ import annotations

import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from omnify.modules.notes_engine import Note, NoteStore


class NoteStoreTests(unittest.TestCase):
    def test_save_and_load_markdown_note(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            note = Note(label="Hello world", contents="First line\nSecond line")
            store.save(note)

            self.assertTrue(note.path is not None)
            self.assertTrue(note.path.name.startswith("Hello-world--"))
            self.assertTrue(note.path.name.endswith(".md"))

            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, note.id)
            self.assertEqual(loaded[0].label, "Hello world")
            self.assertEqual(loaded[0].contents, "First line\nSecond line")

    def test_title_change_renames_note_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            note = Note(label="Old title", contents="Keep me")
            store.save(note)
            old_path = note.path

            note.label = "New title"
            store.save(note)

            self.assertFalse(old_path.exists())
            self.assertTrue(note.path is not None)
            self.assertTrue(note.path.exists())
            self.assertTrue(note.path.name.startswith("New-title--"))

            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].label, "New title")
            self.assertEqual(loaded[0].contents, "Keep me")

    def test_image_is_copied_to_assets_and_names_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = NoteStore(root)
            note = Note(label="Pictures")
            store.save(note)

            source = root / "photo.png"
            source.write_bytes(b"not really a png")

            first = store.import_image(note, source)
            second = store.import_image(note, source)

            self.assertEqual(first, f"assets/{note.id}/photo.png")
            self.assertEqual(second, f"assets/{note.id}/photo-1.png")
            self.assertTrue((root / first).is_file())
            self.assertTrue((root / second).is_file())

    def test_resolve_asset_rejects_paths_outside_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            self.assertIsNone(store.resolve_asset("../private.txt"))
            self.assertIsNone(store.resolve_asset("assets/../../private.txt"))
            self.assertEqual(
                store.resolve_asset("assets/photo.png"),
                store.root / "assets" / "photo.png",
            )

    def test_tags_and_folders_roundtrip_through_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            note = Note(
                label="Plan",
                contents="Tasks",
                tags=["work", "work", "#ideas"],
                folder="Work",
            )
            store.save(note)

            self.assertIn("tags:", note.path.read_text(encoding="utf-8"))
            loaded = store.load()[0]
            self.assertEqual(loaded.tags, ["work", "ideas"])
            self.assertEqual(loaded.folder, "Work")
            self.assertEqual(store.folders(), ["Work"])

    def test_pinned_flag_roundtrips_and_sorts_notes_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            older = Note(label="Older", contents="Text", pinned=True)
            store.save(older)
            newer = Note(label="Newer", contents="Text")
            store.save(newer)

            loaded = store.load()
            self.assertTrue(loaded[0].pinned)
            self.assertEqual(loaded[0].id, older.id)
            self.assertFalse(loaded[1].pinned)
            self.assertIn("pinned: true", older.path.read_text(encoding="utf-8"))

    def test_delete_removes_note_and_its_image_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = NoteStore(root)
            note = Note(label="With image", contents="Text")
            store.save(note)
            source = root / "photo.png"
            source.write_bytes(b"png")
            relative = store.import_image(note, source)
            asset = root / relative
            self.assertTrue(asset.is_file())

            store.delete(note)

            self.assertFalse(note.path.exists())
            self.assertFalse(asset.exists())
            self.assertFalse(asset.parent.exists())

    def test_trash_keeps_note_and_assets_out_of_the_active_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = NoteStore(root)
            note = Note(label="Draft", contents="Keep me", folder="Work")
            store.save(note)
            original_path = note.path
            source = root / "photo.png"
            source.write_bytes(b"png")
            asset = root / store.import_image(note, source)

            trash_path = store.trash_note(note)

            self.assertFalse(original_path.exists())
            self.assertTrue(trash_path.is_file())
            self.assertTrue(asset.is_file())
            self.assertEqual(store.load(), [])
            trashed = store.trashed_notes()
            self.assertEqual(len(trashed), 1)
            self.assertEqual(trashed[0].id, note.id)
            self.assertEqual(trashed[0].folder, "Work")
            self.assertEqual(trashed[0].contents, "Keep me")

    def test_trashed_note_can_be_restored_to_its_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = NoteStore(root)
            note = Note(label="Restore me", contents="Text", folder="Work/Sub")
            store.save(note)
            original_path = note.path
            store.trash_note(note)

            trashed = store.trashed_notes()[0]
            restored = store.restore_trashed(trashed)

            self.assertEqual(restored.id, note.id)
            self.assertEqual(restored.folder, "Work/Sub")
            self.assertEqual(restored.path, original_path)
            self.assertTrue(original_path.is_file())
            self.assertEqual(store.trashed_notes(), [])
            self.assertEqual(store.load()[0].contents, "Text")

    def test_empty_trash_removes_files_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = NoteStore(root)
            notes = [
                Note(label="One", contents="1", folder="Work"),
                Note(label="Two", contents="2"),
            ]
            for note in notes:
                store.save(note)
                source = root / f"{note.id}.png"
                source.write_bytes(b"png")
                store.import_image(note, source)
                store.trash_note(note)

            self.assertEqual(len(store.trashed_notes()), 2)
            self.assertEqual(store.empty_trash(), 2)
            self.assertFalse(store.trash_dir.exists())
            self.assertEqual(store.load(), [])
            for note in notes:
                self.assertFalse((store.assets_dir / note.id).exists())

    def test_sync_to_does_not_copy_trash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = NoteStore(base / "notes")
            note = Note(label="Trashed", contents="Hidden")
            store.save(note)
            store.trash_note(note)

            target = store.sync_to(base / "copy")

            self.assertFalse((target / ".trash").exists())
            self.assertEqual(list(target.rglob("*.md")), [])

    def test_rename_folder_moves_notes_and_preserves_nested_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            root_note = Note(label="Root", contents="Root text", folder="Work")
            child_note = Note(label="Child", contents="Child text", folder="Work/Sub")
            other_note = Note(label="Other", contents="Other text", folder="Personal")
            for note in (root_note, child_note, other_note):
                store.save(note)
            old_root_path = root_note.path
            old_child_path = child_note.path

            moved = store.rename_folder("Work", "Projects")

            self.assertEqual(moved, 2)
            self.assertFalse(old_root_path.exists())
            self.assertFalse(old_child_path.exists())
            loaded = {note.label: note for note in store.load()}
            self.assertEqual(loaded["Root"].folder, "Projects")
            self.assertEqual(loaded["Child"].folder, "Projects/Sub")
            self.assertEqual(loaded["Other"].folder, "Personal")
            self.assertEqual(loaded["Root"].id, root_note.id)

    def test_rename_folder_rejects_child_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            store.create_folder("Work")
            with self.assertRaises(ValueError):
                store.rename_folder("Work", "Work/Inside")

    def test_delete_folder_moves_notes_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            note = Note(label="In folder", contents="Text", folder="Ideas")
            store.save(note)
            self.assertEqual(note.folder, "Ideas")

            moved = store.delete_folder("Ideas")

            self.assertEqual(moved, 1)
            self.assertFalse((store.root / "Ideas").exists())
            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].folder, "")
            self.assertEqual(loaded[0].contents, "Text")

    def test_backup_contains_notes_and_can_be_synced_to_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = NoteStore(base / "notes")
            note = Note(label="Backup me", contents="Important", folder="Work")
            store.save(note)
            source = base / "picture.png"
            source.write_bytes(b"png")
            store.import_image(note, source)

            backup = store.create_backup(base / "backups")
            self.assertTrue(backup.is_file())
            with zipfile.ZipFile(backup) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith(".md") for name in names))
            self.assertTrue(any(name.startswith("assets/") for name in names))

            target = base / "sync"
            store.sync_to(target)
            self.assertTrue((target / "Work").is_dir())
            self.assertTrue((target / "assets").is_dir())
            self.assertTrue(any(target.rglob("*.md")))

            # Відновлення повертає файл, який ми видалили після backup.
            store.delete(note)
            self.assertFalse(note.path.exists())
            restored = store.restore_backup(backup)
            self.assertEqual(restored, 1)
            self.assertTrue(any(store.root.rglob("*.md")))
            self.assertTrue(any(store.assets_dir.rglob("*.png")))

    def test_import_markdown_creates_copy_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = NoteStore(base / "notes")
            source = base / "External note.md"
            source.write_text(
                "---\n"
                "id: external-id\n"
                "title: Imported title\n"
                "tags: [\"work\", \"ideas\"]\n"
                "pinned: true\n"
                "---\n"
                "# Body\n\nImported text.",
                encoding="utf-8",
            )

            note = store.import_markdown(source, folder="Inbox")

            self.assertEqual(note.label, "Imported title")
            self.assertEqual(note.tags, ["work", "ideas"])
            self.assertTrue(note.pinned)
            self.assertEqual(note.folder, "Inbox")
            self.assertEqual(note.contents, "# Body\n\nImported text.")
            self.assertTrue(note.path.is_file())
            self.assertEqual(source.read_text(encoding="utf-8")[:3], "---")
            loaded = {item.id: item for item in store.load()}
            self.assertIn(note.id, loaded)

    def test_import_markdown_generates_new_id_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = NoteStore(base / "notes")
            original = Note(id="fixed-id", label="Original", contents="First")
            store.save(original)
            source = base / "copy.md"
            source.write_text(
                "---\nid: fixed-id\ntitle: Copy\n---\nSecond",
                encoding="utf-8",
            )

            imported = store.import_markdown(source)

            self.assertNotEqual(imported.id, original.id)
            self.assertEqual(len(store.load()), 2)

    def test_two_way_sync_imports_and_exports_newer_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            local = NoteStore(base / "local")
            remote = NoteStore(base / "remote")

            shared = Note(label="Shared", contents="old local")
            local.save(shared)
            time.sleep(0.01)
            remote.save(
                Note(
                    id=shared.id,
                    label="Shared",
                    contents="old remote",
                )
            )
            time.sleep(0.01)
            shared.contents = "new local"
            local.save(shared)
            time.sleep(0.01)
            remote.save(Note(label="Remote only", contents="from remote"))
            time.sleep(0.01)
            local.save(Note(label="Local only", contents="from local"))

            imported, updated, exported = local.sync_two_way(remote.root)

            self.assertEqual(imported, 1)
            self.assertEqual(updated, 0)
            self.assertEqual(exported, 2)
            local_notes = {note.label: note for note in local.load()}
            remote_notes = {note.label: note for note in remote.load()}
            self.assertEqual(local_notes["Remote only"].contents, "from remote")
            self.assertEqual(remote_notes["Local only"].contents, "from local")
            self.assertEqual(remote_notes["Shared"].contents, "new local")

    def test_two_way_sync_lets_newer_remote_note_win(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            local = NoteStore(base / "local")
            remote = NoteStore(base / "remote")
            shared = Note(label="Shared", contents="old local")
            local.save(shared)
            time.sleep(0.01)
            remote.save(
                Note(
                    id=shared.id,
                    label="Shared",
                    contents="new remote",
                )
            )

            imported, updated, exported = local.sync_two_way(remote.root)

            self.assertEqual((imported, updated, exported), (0, 1, 0))
            self.assertEqual(local.load()[0].contents, "new remote")
            self.assertEqual(remote.load()[0].contents, "new remote")


if __name__ == "__main__":
    unittest.main()
