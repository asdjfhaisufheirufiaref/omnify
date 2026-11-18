"""Headless tests for browser search bangs and the local reading list."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.storage import Storage  # noqa: E402
from omnify.modules.browser_engine import (  # noqa: E402
    READING_KEY,
    ReadingList,
    normalize_reading_url,
    resolve_bang,
    title_from_url,
    url_from_input,
)


class SearchBangTest(unittest.TestCase):
    def test_resolves_bang(self):
        self.assertEqual(
            resolve_bang("!w Python"),
            "https://en.wikipedia.org/w/index.php?search=Python",
        )
        self.assertIn("youtube.com/results", resolve_bang("!yt lofi beats"))
        self.assertIn("lofi+beats", resolve_bang("!yt lofi beats"))

    def test_bang_is_case_insensitive_and_supports_aliases(self):
        self.assertEqual(resolve_bang("!W Python"), resolve_bang("!w Python"))
        self.assertEqual(resolve_bang("!wiki Python"), resolve_bang("!w Python"))

    def test_non_bang_returns_none(self):
        self.assertIsNone(resolve_bang("cats and dogs"))
        self.assertIsNone(resolve_bang(""))

    def test_unknown_bang_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_bang("!nope query")

    def test_bang_without_query_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_bang("!w")

    def test_url_from_input_uses_bangs(self):
        self.assertEqual(
            url_from_input("!gh python"),
            "https://github.com/search?q=python&type=repositories",
        )
        self.assertEqual(
            url_from_input("!so list index out of range"),
            "https://stackoverflow.com/search?q=list+index+out+of+range",
        )

    def test_regular_search_is_unchanged(self):
        self.assertEqual(
            url_from_input("cats and dogs"),
            "https://duckduckgo.com/?q=cats+and+dogs",
        )
        self.assertEqual(url_from_input("example.com"), "https://example.com")


class ReadingUrlTest(unittest.TestCase):
    def test_bare_domain_gets_https(self):
        self.assertEqual(
            normalize_reading_url("example.com/article"),
            "https://example.com/article",
        )

    def test_http_url_is_kept(self):
        self.assertEqual(
            normalize_reading_url("http://example.com/a"),
            "http://example.com/a",
        )

    def test_empty_url_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_reading_url("  ")

    def test_search_phrase_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_reading_url("cats and dogs")

    def test_unsafe_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_reading_url("javascript:alert(1)")
        with self.assertRaises(ValueError):
            normalize_reading_url("ftp://example.com/file")

    def test_title_from_url(self):
        self.assertEqual(
            title_from_url("https://example.com/guide/getting-started"),
            "getting-started · example.com",
        )
        self.assertEqual(title_from_url("https://example.com/"), "example.com")


class ReadingListTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(root=self._tmp.name)
        self.reading = ReadingList(self.storage)

    def tearDown(self):
        self._tmp.cleanup()

    def test_starts_empty(self):
        self.assertEqual(self.reading.all(), [])

    def test_add_persists_and_puts_newest_first(self):
        first = self.reading.add(
            "example.com/one", now="2026-11-17T10:00:00+00:00"
        )
        second = self.reading.add(
            "example.com/two", "Second", now="2026-11-17T11:00:00+00:00"
        )
        entries = self.reading.all()
        self.assertEqual([entry["id"] for entry in entries], [second["id"], first["id"]])
        self.assertEqual(entries[0]["title"], "Second")
        self.assertEqual(entries[1]["title"], "one · example.com")
        reloaded = ReadingList(self.storage)
        self.assertEqual(reloaded.all(), entries)
        self.assertEqual(first["url"], "https://example.com/one")

    def test_duplicate_url_is_moved_to_the_top(self):
        first = self.reading.add(
            "example.com/one", "Old", now="2026-11-17T10:00:00+00:00"
        )
        self.reading.add(
            "example.com/two", "Two", now="2026-11-17T11:00:00+00:00"
        )
        again = self.reading.add(
            "https://example.com/one",
            now="2026-11-17T12:00:00+00:00",
        )
        entries = self.reading.all()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["id"], first["id"])
        self.assertEqual(entries[0]["title"], "Old")
        self.assertEqual(again["added_at"], "2026-11-17T12:00:00+00:00")

    def test_duplicate_can_update_the_title(self):
        self.reading.add("example.com/one", "Old")
        updated = self.reading.add("example.com/one", "New title")
        self.assertEqual(updated["title"], "New title")
        self.assertEqual(len(self.reading.all()), 1)

    def test_has_normalises_the_url(self):
        self.reading.add("example.com/one")
        self.assertTrue(self.reading.has("https://example.com/one"))
        self.assertFalse(self.reading.has("example.com/two"))
        self.assertFalse(self.reading.has("not a url"))

    def test_remove_and_clear(self):
        entry = self.reading.add("example.com/one")
        other = self.reading.add("example.com/two")
        self.assertTrue(self.reading.remove(entry["id"]))
        self.assertFalse(self.reading.remove("missing"))
        self.assertEqual([item["id"] for item in self.reading.all()], [other["id"]])
        self.reading.clear()
        self.assertEqual(self.reading.all(), [])

    def test_corrupt_storage_falls_back_to_empty(self):
        (self.storage.path / (READING_KEY + ".json")).write_text(
            "{not json", encoding="utf-8"
        )
        self.assertEqual(self.reading.all(), [])

    def test_malformed_entries_are_ignored(self):
        self.storage.save(READING_KEY, [{"url": "x"}, "bad", {"id": "a"}])
        self.assertEqual(self.reading.all(), [])

    def test_invalid_add_is_rejected(self):
        with self.assertRaises(ValueError):
            self.reading.add("javascript:alert(1)")
        self.assertEqual(self.reading.all(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
