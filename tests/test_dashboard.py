"""Tests for the GTK-free dashboard model."""

from __future__ import annotations

import unittest

from omnify.modules.dashboard import TOOL_MODULES


class DashboardTests(unittest.TestCase):
    def test_tool_tiles_include_notes_and_have_unique_ids(self) -> None:
        ids = [module.id for module in TOOL_MODULES]
        self.assertIn("notes", ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_notes_tile_has_a_description(self) -> None:
        notes = next(module for module in TOOL_MODULES if module.id == "notes")
        self.assertTrue(notes.name)
        self.assertTrue(notes.description)


if __name__ == "__main__":
    unittest.main()
