"""Tests for small, GTK-free helpers used by the Notes editor."""

from __future__ import annotations

import unittest

from omnify.modules.notes import NotesModule
from omnify.modules.notes_engine import Note


class NotesUiHelperTests(unittest.TestCase):
    def test_count_words_ignores_markdown_markers_and_urls(self) -> None:
        text = "# Title\n\n**hello** [world](https://example.com) ![photo](assets/x.png)"
        self.assertEqual(NotesModule._count_words(text), 3)

    def test_count_words_handles_ukrainian_text(self) -> None:
        text = "Привіт, це тестова нотатка з чеклістом:\n- [ ] зробити"
        self.assertEqual(NotesModule._count_words(text), 7)

    def test_note_snippet_strips_markdown_and_images(self) -> None:
        contents = "![photo](assets/x.png)\n\n## Заголовок\n\nТекст **нотатки**"
        self.assertEqual(NotesModule._note_snippet(contents), "Заголовок")

    def test_note_snippet_marks_checklist(self) -> None:
        self.assertEqual(
            NotesModule._note_snippet("- [ ] купити молоко"),
            "☐ купити молоко",
        )

    def test_builtin_templates_have_titles_and_markdown_bodies(self) -> None:
        templates = NotesModule._template_definitions()
        self.assertGreaterEqual(len(templates), 4)
        for title, contents in templates:
            self.assertTrue(title.strip())
            self.assertTrue(contents.startswith("# "))

    def test_notes_summary_reports_count_and_latest_title(self) -> None:
        module = NotesModule.__new__(NotesModule)
        self.assertEqual(module.summary(), (0, ""))
        module._notes = [Note(label="Latest"), Note(label="Older")]
        self.assertEqual(module.summary(), (2, "Latest"))

    def test_recent_notes_returns_bounded_preview_rows(self) -> None:
        module = NotesModule.__new__(NotesModule)
        self.assertEqual(module.recent_notes(), [])
        module._notes = [
            Note(id="one", label="Latest", folder="Work"),
            Note(id="two", label="Older", folder=""),
        ]
        self.assertEqual(
            module.recent_notes(1),
            [("one", "Latest", "Work")],
        )
        self.assertEqual(module.recent_notes(0), [])

    def test_find_next_offset_is_case_insensitive_and_wraps(self) -> None:
        text = "Alpha beta ALPHA"
        self.assertEqual(NotesModule._find_next_offset(text, "alpha", 0), 0)
        self.assertEqual(NotesModule._find_next_offset(text, "alpha", 1), 11)
        self.assertEqual(NotesModule._find_next_offset(text, "alpha", 12), 0)
        self.assertEqual(NotesModule._find_next_offset(text, "Alpha", 0, True), 0)

    def test_replace_all_handles_case_and_backslashes_literally(self) -> None:
        self.assertEqual(
            NotesModule._replace_all("One one ONE", "one", "two"),
            ("two two two", 3),
        )
        self.assertEqual(
            NotesModule._replace_all("One one", "one", r"\1", True),
            (r"One \1", 1),
        )
        self.assertEqual(
            NotesModule._replace_all("Text", "", "x"),
            ("Text", 0),
        )


if __name__ == "__main__":
    unittest.main()
