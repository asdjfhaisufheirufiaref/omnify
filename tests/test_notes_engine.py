"""Tests for the GTK-free Notes data model."""

from __future__ import annotations

import unittest

from omnify.modules.notes_engine import Note


class NoteTests(unittest.TestCase):
    def test_new_note_has_defaults(self) -> None:
        note = Note()
        self.assertTrue(note.id)
        self.assertEqual(note.label, "Untitled")
        self.assertEqual(note.contents, "")

    def test_note_roundtrip_keeps_all_fields(self) -> None:
        note = Note(label="Ideas", contents="First line\nSecond line")
        restored = Note.from_dict(note.to_dict())
        self.assertEqual(restored.id, note.id)
        self.assertEqual(restored.label, note.label)
        self.assertEqual(restored.contents, note.contents)
        self.assertEqual(restored.updated_at, note.updated_at)

    def test_from_dict_fills_missing_fields(self) -> None:
        note = Note.from_dict({})
        self.assertTrue(note.id)
        self.assertEqual(note.label, "Untitled")
        self.assertEqual(note.contents, "")


if __name__ == "__main__":
    unittest.main()
