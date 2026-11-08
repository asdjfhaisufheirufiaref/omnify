"""Tests for Markdown note persistence and image attachments."""

from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
