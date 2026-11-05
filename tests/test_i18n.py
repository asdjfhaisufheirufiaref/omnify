"""Headless tests for the EN/UK translation layer."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core import i18n  # noqa: E402


class TestI18n(unittest.TestCase):
    def setUp(self):
        self._previous = i18n.current_language()
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language(self._previous)

    def test_languages_discovered_from_files(self):
        languages = dict(i18n.available_languages())
        self.assertEqual(languages.get("en"), "English")
        self.assertIn("uk", languages)
        self.assertEqual(languages["uk"], "Українська")
        self.assertEqual(i18n.available_languages()[0][0], "en")

    def test_english_is_identity(self):
        self.assertEqual(i18n.tr("Settings"), "Settings")
        self.assertEqual(i18n.tr("A string that does not exist"), "A string that does not exist")

    def test_ukrainian_lookup_and_reverse_lookup(self):
        self.assertEqual(i18n.set_language("uk"), "uk")
        self.assertEqual(i18n.tr("Settings"), "Налаштування")
        self.assertEqual(i18n.tr("Scientific"), "Науковий")
        self.assertEqual(i18n.source_for("Налаштування"), "Settings")
        # Unknown strings stay usable instead of disappearing.
        self.assertEqual(i18n.tr("Untranslated technical text"), "Untranslated technical text")

    def test_format_placeholders(self):
        i18n.set_language("uk")
        self.assertEqual(
            i18n.tr("Timer will start in {minutes} min", minutes=7),
            "Таймер запуститься через 7 хв",
        )
        self.assertEqual(
            i18n.tr("{count} services", count=3),
            "3 служб",
        )
        # A missing placeholder must not crash or blank a label.
        self.assertEqual(
            i18n.tr("Timer will start in {minutes} min"),
            "Таймер запуститься через {minutes} хв",
        )

    def test_invalid_language_falls_back(self):
        self.assertEqual(i18n.set_language("nope"), "en")
        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(i18n.set_language("uk"), "uk")
        self.assertEqual(i18n.set_language(""), "en")

    def test_catalogues_are_non_empty_and_strings(self):
        i18n.set_language("uk")
        self.assertGreater(len(i18n._catalogues["uk"]), 100)
        for source, target in i18n._catalogues["uk"].items():
            self.assertIsInstance(source, str)
            self.assertIsInstance(target, str)
            self.assertTrue(target.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
