"""Headless tests for Obsidian-style template variables and storage."""

from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.storage import Storage  # noqa: E402
from omnify.core.templates import (  # noqa: E402
    TemplateEngine,
    TemplateStore,
    _moment_format,
)


class TemplateEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.datetime(2026, 11, 17, 14, 5, 9)
        self.engine = TemplateEngine(
            now=lambda: self.now,
            clipboard=lambda: "from clipboard",
            selection=lambda: "selected words",
            prompt=lambda label: f"answer:{label}",
            random_digits=lambda: "123456",
        )

    def test_basic_variables(self) -> None:
        result = self.engine.render(
            "# {{title}}\n{{date}} {{time}}\n{{weekday}}\n"
            "{{yesterday}} / {{tomorrow}}\n{{folder}}\n{{note_id}}\n",
            {"title": "Plan", "folder": "Work", "note_id": "abc"},
        )
        self.assertEqual(
            result.text,
            "# Plan\n2026-11-17 14:05\nTuesday\n"
            "2026-11-16 / 2026-11-18\nWork\nabc\n",
        )
        self.assertIsNone(result.cursor)

    def test_moment_formats(self) -> None:
        self.assertEqual(
            _moment_format("dddd, DD MMMM YYYY · HH:mm", self.now),
            "Tuesday, 17 November 2026 · 14:05",
        )
        self.assertEqual(
            self.engine.render("{{date:YYYY/MM/DD}}").text,
            "2026/11/17",
        )
        self.assertEqual(
            self.engine.render("{{time:HH:mm:ss}}").text,
            "14:05:09",
        )

    def test_special_values_and_prompt(self) -> None:
        result = self.engine.render(
            "{{uuid}}|{{random}}|{{clipboard}}|{{selection}}|{{prompt:Name?}}"
        )
        parts = result.text.split("|")
        self.assertEqual(len(parts[0]), 32)
        self.assertEqual(parts[1], "123456")
        self.assertEqual(parts[2], "from clipboard")
        self.assertEqual(parts[3], "selected words")
        self.assertEqual(parts[4], "answer:Name?")

    def test_cursor_position(self) -> None:
        result = self.engine.render("Hello {{title}}{{cursor}}!\n{{date}}", {"title": "world"})
        self.assertEqual(result.text, "Hello world!\n2026-11-17")
        self.assertEqual(result.cursor, len("Hello world"))

    def test_unknown_variables_stay_visible(self) -> None:
        result = self.engine.render("{{unknown}} {{date}}")
        self.assertEqual(result.text, "{{unknown}} 2026-11-17")

    def test_prompt_without_callback_is_empty(self) -> None:
        engine = TemplateEngine(now=lambda: self.now)
        self.assertEqual(engine.render("A{{prompt:Why?}}B").text, "AB")

    def test_prompt_errors_are_contained(self) -> None:
        engine = TemplateEngine(
            now=lambda: self.now,
            prompt=lambda label: (_ for _ in ()).throw(RuntimeError("no")),
        )
        self.assertEqual(engine.render("A{{prompt:X}}B").text, "AB")


class TemplateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.tmp.name)
        self.store = TemplateStore(self.storage)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_update_delete_roundtrip(self) -> None:
        record = self.store.save("Daily", "# {{date}}")
        self.assertEqual(len(self.store.all()), 1)
        reloaded = TemplateStore(self.storage)
        self.assertEqual(reloaded.get(record.id).body, "# {{date}}")

        updated = reloaded.save("Daily plan", "# Changed", record.id)
        self.assertEqual(updated.id, record.id)
        self.assertEqual(reloaded.get(record.id).name, "Daily plan")

        self.assertTrue(reloaded.delete(record.id))
        self.assertFalse(reloaded.delete(record.id))
        self.assertEqual(reloaded.all(), [])

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.save("  ", "body")

    def test_corrupt_storage_is_tolerated(self) -> None:
        storage = Storage(self.tmp.name)
        storage.save(TemplateStore.KEY, {"templates": [None, {"name": 3}, "bad"]})
        store = TemplateStore(storage)
        self.assertEqual(store.all(), [])


if __name__ == "__main__":
    unittest.main()
