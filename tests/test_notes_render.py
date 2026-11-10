"""Tests for the GTK-free Markdown renderer and exporters."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnify.modules.notes_engine import Note, NoteStore
from omnify.modules.notes_render import (
    export_html,
    export_pdf,
    note_to_html,
    parse_markdown,
)


class MarkdownParserTests(unittest.TestCase):
    def test_parse_common_note_blocks(self) -> None:
        text = (
            "# Title\n\n"
            "A **bold** paragraph.\n\n"
            "- [ ] first\n"
            "- [x] second\n\n"
            "| Name | Value |\n"
            "| --- | --- |\n"
            "| one | 1 |\n\n"
            "```python\nprint('hi')\n```\n"
        )
        blocks = parse_markdown(text)
        self.assertEqual(
            [block.kind for block in blocks],
            ["heading", "paragraph", "checklist", "checklist", "table", "code"],
        )
        self.assertTrue(blocks[3].checked)
        self.assertEqual(blocks[4].headers, ["Name", "Value"])
        self.assertEqual(blocks[4].rows, [["one", "1"]])
        self.assertEqual(blocks[5].language, "python")

    def test_html_escapes_text_and_renders_formatting(self) -> None:
        note = Note(
            label="<Unsafe>",
            contents="Use <b>tags</b> and **bold** with `code`.",
            tags=["work"],
        )
        html = note_to_html(note)
        self.assertIn("&lt;Unsafe&gt;", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn("#work", html)
        self.assertNotIn("<b>tags</b>", html)

    def test_html_embeds_local_image_as_data_uri(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = NoteStore(temporary)
            note = Note(label="Image", contents="")
            store.save(note)
            source = Path(temporary) / "photo.png"
            source.write_bytes(b"fake-image")
            relative = store.import_image(note, source)
            # Перевіряємо і картинку окремим блоком, і всередині абзацу.
            note.contents = f"Before ![photo]({relative}) after"
            note.contents += f"\n\n![photo]({relative})"

            html = note_to_html(note, store)
            self.assertIn("data:image/png;base64,", html)
            self.assertIn("after", html)

    def test_export_html_and_pdf_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = NoteStore(root / "notes")
            note = Note(
                label="Export",
                contents="# Header\n\n- [x] done\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
            )
            store.save(note)
            html_path = export_html(note, store, root / "note.html")
            pdf_path = export_pdf(note, store, root / "note.pdf")

            self.assertTrue(html_path.is_file())
            self.assertIn("<table>", html_path.read_text(encoding="utf-8"))
            self.assertTrue(pdf_path.is_file())
            self.assertEqual(pdf_path.read_bytes()[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main()
