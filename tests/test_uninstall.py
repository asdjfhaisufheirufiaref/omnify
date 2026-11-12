"""Integration tests for the safe, backup-preserving uninstaller."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class UninstallScriptTests(unittest.TestCase):
    def _fixture(self, base: Path) -> tuple[Path, Path, Path]:
        home = base / "home"
        project = base / "project"
        data = home / ".local" / "share" / "omnify"
        for directory in (
            home / ".local" / "bin",
            home / ".local" / "share" / "applications",
            home / ".config" / "autostart",
            home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps",
            home / ".config" / "omnify",
            home / ".cache" / "omnify",
            project / "notes",
            data / "backups",
            data / "custom-backups",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        source = Path(__file__).resolve().parents[1] / "uninstall.sh"
        shutil.copy2(source, project / "uninstall.sh")
        (project / "pyproject.toml").write_text(
            '[project]\nname = "omnify"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (project / "notes" / "note.md").write_text("# Note\n", encoding="utf-8")

        (data / "settings.json").write_text("settings", encoding="utf-8")
        (data / "backups" / "notes-20260101-000000.zip").write_bytes(b"default")
        (data / "custom-backups" / "keep.zip").write_bytes(b"custom")
        (data / "backup.json").write_text(
            '{"destination": "%s"}\n' % (data / "custom-backups"),
            encoding="utf-8",
        )
        (home / ".config" / "omnify" / "settings.json").write_text(
            "config", encoding="utf-8"
        )
        (home / ".cache" / "omnify" / "cache.dat").write_text(
            "cache", encoding="utf-8"
        )
        (home / ".local" / "bin" / "omnify").write_text("launcher", encoding="utf-8")
        (
            home / ".local" / "share" / "applications" / "omnify.desktop"
        ).write_text("desktop", encoding="utf-8")
        (home / ".config" / "autostart" / "omnify.desktop").write_text(
            "autostart", encoding="utf-8"
        )
        (
            home
            / ".local"
            / "share"
            / "icons"
            / "hicolor"
            / "256x256"
            / "apps"
            / "omnify.png"
        ).write_text("icon", encoding="utf-8")
        return home, project, data

    def _env(self, home: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
        for key in ("OMNIFY_DATA_DIR", "OMNIFY_BACKUP_DIR", "OMNIFY_NOTES_DIR"):
            env.pop(key, None)
        return env

    def test_uninstall_removes_app_data_but_never_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home, project, data = self._fixture(base)
            result = subprocess.run(
                [str(project / "uninstall.sh"), "--yes"],
                cwd=project,
                env=self._env(home),
                text=True,
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Backups remain untouched", result.stdout)
            self.assertTrue((data / "backups" / "notes-20260101-000000.zip").is_file())
            self.assertTrue((data / "custom-backups" / "keep.zip").is_file())
            self.assertFalse((data / "settings.json").exists())
            self.assertFalse((data / "backup.json").exists())
            self.assertFalse((project / "notes").exists())
            self.assertFalse((home / ".config" / "omnify").exists())
            self.assertFalse((home / ".cache" / "omnify").exists())
            self.assertFalse((home / ".local" / "bin" / "omnify").exists())
            self.assertFalse(
                (home / ".local" / "share" / "applications" / "omnify.desktop").exists()
            )
            self.assertFalse(
                (home / ".config" / "autostart" / "omnify.desktop").exists()
            )
            self.assertTrue((project / "uninstall.sh").is_file())
            self.assertTrue((project / "pyproject.toml").is_file())

    def test_remove_project_keeps_backup_inside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home, project, data = self._fixture(base)
            inside = project / "project-backups"
            inside.mkdir()
            backup = inside / "notes-20260101-000000.zip"
            backup.write_bytes(b"protected")
            (data / "backup.json").write_text(
                '{"destination": "%s"}\n' % inside,
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(project / "uninstall.sh"), "--yes", "--remove-project"],
                cwd=project,
                env=self._env(home),
                text=True,
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(backup.is_file())
            self.assertIn("Backups remain untouched", result.stdout)
            self.assertFalse((project / "pyproject.toml").exists())
            self.assertFalse((project / "uninstall.sh").exists())
            self.assertTrue((data / "backups" / "notes-20260101-000000.zip").is_file())

    def test_dry_run_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home, project, data = self._fixture(base)
            result = subprocess.run(
                [str(project / "uninstall.sh"), "--dry-run", "--yes"],
                cwd=project,
                env=self._env(home),
                text=True,
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((data / "settings.json").is_file())
            self.assertTrue((data / "backups" / "notes-20260101-000000.zip").is_file())
            self.assertTrue((project / "notes" / "note.md").is_file())
            self.assertIn("Dry run", result.stdout)
