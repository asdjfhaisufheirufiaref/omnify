"""Headless tests for the command-palette fuzzy matcher."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.fuzzy import best_score, fuzzy_score, rank  # noqa: E402


class FuzzyTests(unittest.TestCase):
    def test_exact_and_prefix_win(self) -> None:
        exact = fuzzy_score("new note", "New note")
        prefix = fuzzy_score("new", "New note")
        middle = fuzzy_score("note", "New note")
        self.assertIsNotNone(exact)
        self.assertIsNotNone(prefix)
        self.assertIsNotNone(middle)
        self.assertGreater(exact, middle)
        self.assertGreater(prefix, middle)

    def test_subsequence_matches(self) -> None:
        self.assertIsNotNone(fuzzy_score("nn", "New note"))
        self.assertIsNotNone(fuzzy_score("tst", "Timer: Start"))
        self.assertIsNotNone(fuzzy_score("calnew", "Calendar: New event"))

    def test_non_matches_are_none(self) -> None:
        self.assertIsNone(fuzzy_score("zzz", "New note"))
        self.assertIsNone(fuzzy_score("note", ""))

    def test_empty_query_matches_everything(self) -> None:
        self.assertEqual(fuzzy_score("", "Anything"), 1)
        self.assertEqual(best_score("", ["", "Anything"]), 1)

    def test_rank_orders_best_matches(self) -> None:
        items = [
            ("note", ["New note", "Notes category"]),
            ("timer", ["Start timer", "Timer category"]),
            ("settings", ["Open settings", "Application"]),
        ]
        ranked = rank("note", items)
        self.assertEqual(ranked[0][1], "note")
        self.assertNotIn("timer", [item for _score, item in ranked])

    def test_empty_query_preserves_input(self) -> None:
        items = [("a", ["Alpha"]), ("b", ["Beta"])]
        ranked = rank("", items)
        self.assertEqual([item for _score, item in ranked], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
