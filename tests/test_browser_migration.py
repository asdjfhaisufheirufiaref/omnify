"""Tests for browser profile discovery and migration (no GTK required)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from omnify.modules.browser_migration import (
    BrowserSource,
    MigrationError,
    ProfileBusyError,
    build_import_plan,
    discover_import_sources,
    import_firefox_profile,
    import_summary,
)


class TestProfileDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_firefox_profile(self, folder: str = "abc.default-release") -> Path:
        profile = self.home / ".mozilla" / "firefox" / folder
        profile.mkdir(parents=True)
        ini = self.home / ".mozilla" / "firefox" / "profiles.ini"
        ini.write_text(
            "[Profile0]\n"
            "Name=default-release\n"
            "IsRelative=1\n"
            f"Path={folder}\n"
            "Default=1\n",
            encoding="utf-8",
        )
        return profile

    def test_discovers_firefox_profile_and_marks_default(self) -> None:
        profile = self._make_firefox_profile()
        sources = discover_import_sources(home=self.home)
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertEqual(source.family, "firefox")
        self.assertEqual(source.path, profile.resolve())
        self.assertTrue(source.is_default)

    def test_excludes_target_profile(self) -> None:
        profile = self._make_firefox_profile()
        sources = discover_import_sources(home=self.home, target_profile=profile)
        self.assertEqual(sources, [])

    def test_discovers_chromium_profile(self) -> None:
        folder = self.home / ".config" / "BraveSoftware" / "Brave-Browser" / "Default"
        folder.mkdir(parents=True)
        (folder / "Preferences").write_text(
            json.dumps({"profile": {"name": "Person 1"}}), encoding="utf-8"
        )
        sources = discover_import_sources(home=self.home)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].family, "chromium")
        self.assertEqual(sources[0].profile_name, "Person 1")
        self.assertTrue(sources[0].is_default)


class TestFirefoxImport(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source_path = self.root / "source"
        self.target_path = self.root / "target"
        self.source_path.mkdir()
        self.target_path.mkdir()

        connection = sqlite3.connect(self.source_path / "places.sqlite")
        connection.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT)")
        connection.execute("INSERT INTO moz_places (url) VALUES ('https://example.com')")
        connection.commit()
        connection.close()
        (self.source_path / "logins.json").write_text('{"logins": []}', encoding="utf-8")
        (self.source_path / "prefs.js").write_text(
            'user_pref("browser.startup.homepage", "about:blank");\n',
            encoding="utf-8",
        )
        (self.source_path / "extensions").mkdir()
        (self.source_path / "extensions" / "useful.xpi").write_bytes(b"xpi")
        (self.source_path / "extensions" / "omnify-shields@omnify.xpi").write_bytes(
            b"shield"
        )
        (self.source_path / "sessionstore-backups").mkdir()
        (self.source_path / "sessionstore-backups" / "recovery.jsonlz4").write_bytes(
            b"session"
        )
        self.source = BrowserSource(
            key="firefox",
            name="Firefox",
            family="firefox",
            path=self.source_path,
            profile_name="default",
            is_default=True,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plan_skips_prefs_and_own_shields(self) -> None:
        entries = build_import_plan(self.source, self.target_path)
        names = {entry.source.name for entry in entries}
        self.assertIn("places.sqlite", names)
        self.assertIn("logins.json", names)
        self.assertIn("useful.xpi", names)
        self.assertIn("recovery.jsonlz4", names)
        self.assertNotIn("prefs.js", names)
        self.assertNotIn("omnify-shields@omnify.xpi", names)

    def test_import_copies_data_and_creates_backup(self) -> None:
        existing = self.target_path / "places.sqlite"
        connection = sqlite3.connect(existing)
        connection.execute("CREATE TABLE old (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()

        result = import_firefox_profile(self.source, self.target_path)

        self.assertGreaterEqual(result.copied_count, 4)
        self.assertTrue((self.target_path / "places.sqlite").is_file())
        self.assertTrue((self.target_path / "logins.json").is_file())
        self.assertTrue((self.target_path / "extensions" / "useful.xpi").is_file())
        self.assertFalse(
            (self.target_path / "extensions" / "omnify-shields@omnify.xpi").exists()
        )
        self.assertFalse((self.target_path / "prefs.js").exists())
        self.assertIsNotNone(result.backup_dir)
        self.assertTrue((result.backup_dir / "places.sqlite").is_file())
        self.assertIn("Imported", import_summary(result))

    def test_import_rejects_busy_target(self) -> None:
        (self.target_path / "parent.lock").touch()
        with self.assertRaises(ProfileBusyError):
            import_firefox_profile(self.source, self.target_path)

    def test_chromium_source_cannot_be_direct_copied(self) -> None:
        chromium = BrowserSource(
            key="brave",
            name="Brave",
            family="chromium",
            path=self.source_path,
        )
        with self.assertRaises(MigrationError):
            build_import_plan(chromium, self.target_path)


if __name__ == "__main__":
    unittest.main()
