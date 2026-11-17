"""Headless tests for plugin manifests, trust, loading and commands."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.commands import CommandRegistry  # noqa: E402
from omnify.core.plugins import PluginManager, PLUGIN_API_VERSION  # noqa: E402
from omnify.core.storage import Storage  # noqa: E402


def write_plugin(root: Path, plugin_id: str, *, default_enabled=False, entry="plugin.py", body=None) -> None:
    folder = root / plugin_id
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_id.title(),
                "version": "1.2.3",
                "description": "test plugin",
                "author": "tests",
                "api_version": PLUGIN_API_VERSION,
                "entrypoint": entry,
                "default_enabled": default_enabled,
                "permissions": ["notes"],
            }
        ),
        encoding="utf-8",
    )
    (folder / "plugin.py").write_text(
        body
        or textwrap.dedent(
            """
            def setup(api):
                api.set_setting("loaded", True)
                api.register_command(
                    "hello", "Say hello", lambda: None,
                    default_keys=("ctrl+alt+h",),
                    description="from a plugin",
                )
                api.register_settings_panel("Test panel", lambda: None)

            def teardown(api):
                api.set_setting("torn_down", True)
            """
        ),
        encoding="utf-8",
    )


class PluginManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.builtin = base / "builtin"
        self.storage = Storage(base / "data")
        self.external = self.storage.path / "plugins"
        self.builtin.mkdir()
        self.external.mkdir(parents=True)
        self.commands = CommandRegistry()
        self.manager = PluginManager(
            self.storage,
            self.commands,
            builtin_dir=self.builtin,
        )

    def tearDown(self) -> None:
        for record in self.manager.records():
            self.manager.disable(record.id)
        self.tmp.cleanup()

    def test_builtin_default_enabled_is_loaded(self) -> None:
        write_plugin(self.builtin, "builtin_test", default_enabled=True)
        self.manager.load_all()
        record = self.manager.get("builtin_test")
        self.assertIsNotNone(record)
        self.assertTrue(record.loaded)
        self.assertTrue(record.trusted)
        command = self.commands.get("builtin_test.hello")
        self.assertIsNotNone(command)
        self.assertEqual(command.default_keys, ("ctrl+alt+h",))
        self.assertEqual(
            self.manager._plugin_settings("builtin_test").get("loaded"), True
        )
        panels = self.manager.settings_panels()
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0][1], "Test panel")

    def test_external_plugin_requires_explicit_trust(self) -> None:
        write_plugin(self.external, "external_test")
        self.manager.load_all()
        record = self.manager.get("external_test")
        self.assertIsNotNone(record)
        self.assertFalse(record.loaded)
        self.assertFalse(record.enabled)
        self.assertFalse(self.manager.enable("external_test"))
        self.assertFalse(record.loaded)
        self.assertTrue(self.manager.enable("external_test", trust=True))
        self.assertTrue(record.loaded)
        self.assertIsNotNone(self.commands.get("external_test.hello"))

    def test_disable_unloads_and_unregisters_commands(self) -> None:
        write_plugin(self.builtin, "builtin_test", default_enabled=True)
        self.manager.load_all()
        self.assertTrue(self.manager.disable("builtin_test"))
        record = self.manager.get("builtin_test")
        self.assertFalse(record.loaded)
        self.assertIsNone(self.commands.get("builtin_test.hello"))
        self.assertEqual(
            self.manager._plugin_settings("builtin_test").get("torn_down"), True
        )

    def test_settings_and_trust_persist(self) -> None:
        write_plugin(self.external, "external_test")
        self.manager.load_all()
        self.manager.enable("external_test", trust=True)
        self.manager._set_plugin_setting("external_test", "model", "local")
        reloaded = PluginManager(self.storage, CommandRegistry(), builtin_dir=self.builtin)
        reloaded.scan()
        record = reloaded.get("external_test")
        self.assertTrue(record.trusted)
        self.assertTrue(record.enabled)
        self.assertEqual(
            reloaded._plugin_settings("external_test").get("model"), "local"
        )

    def test_invalid_entrypoint_is_rejected(self) -> None:
        write_plugin(self.external, "bad_test", entry="../escape.py")
        self.manager.scan()
        self.assertIsNone(self.manager.get("bad_test"))
        self.assertTrue(any("inside the plugin folder" in message for _name, message in self.manager.errors()))

    def test_duplicate_external_id_does_not_shadow_builtin(self) -> None:
        write_plugin(self.builtin, "same_id", default_enabled=True)
        write_plugin(self.external, "same_id")
        self.manager.scan()
        record = self.manager.get("same_id")
        self.assertTrue(record.manifest.builtin)
        self.assertTrue(any("duplicate plugin id" in message for _name, message in self.manager.errors()))

    def test_failed_plugin_is_reported_without_breaking_others(self) -> None:
        write_plugin(self.builtin, "broken", default_enabled=True, body="raise RuntimeError('boom')\n")
        write_plugin(self.builtin, "healthy", default_enabled=True)
        self.manager.load_all()
        broken = self.manager.get("broken")
        healthy = self.manager.get("healthy")
        self.assertFalse(broken.loaded)
        self.assertIn("boom", broken.error)
        self.assertTrue(healthy.loaded)


if __name__ == "__main__":
    unittest.main()
