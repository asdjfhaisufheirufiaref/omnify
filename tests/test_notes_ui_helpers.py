"""Tests for small, GTK-free helpers used by the Notes editor."""

from __future__ import annotations

import unittest

from omnify.modules.notes import NotesModule


class NotesUiHelperTests(unittest.TestCase):
    def test_count_words_ignores_markdown_markers_and_urls(self) -> None:
        text = "# Title\n\n**hello** [world](https://example.com) ![photo](assets/x.png)"
        self.assertEqual(NotesModule._count_words(text), 3)

    def test_count_words_handles_ukrainian_text(self) -> None:
        text = "Привіт, це тестова нотатка з чеклістом:\n- [ ] зробити"
        self.assertEqual(NotesModule._count_words(text), 7)


if __name__ == "__main__":
    unittest.main()
