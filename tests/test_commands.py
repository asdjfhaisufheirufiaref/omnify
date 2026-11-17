"""Headless tests for commands, shortcut persistence and conflict detection."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.commands import (  # noqa: E402
    Command,
    CommandRegistry,
    ShortcutManager,
    format_combo,
    format_keys,
    normalize_combo,
    normalize_keys,
)
from omnify.core.storage import Storage  # noqa: E402


class ComboTests(unittest.TestCase):
    def test_modifier_order_is_stable(self) -> None:
        self.assertEqual(normalize_combo("Shift+Control+P"), "ctrl+shift+p")
        self.assertEqual(normalize_combo("alt + f4"), "alt+f4")
        self.assertEqual(normalize_combo("Cmd+Enter"), "super+return")

    def test_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_combo("esc"), "escape")
        self.assertEqual(normalize_combo("Ctrl+Del"), "ctrl+delete")
        self.assertEqual(normalize_combo("ctrl+pageup"), "ctrl+page_up")
        self.assertEqual(normalize_combo("ctrl++"), "ctrl+plus")

    def test_invalid_and_empty_combos(self) -> None:
        self.assertEqual(normalize_combo(""), "")
        self.assertEqual(normalize_combo("ctrl"), "")
        self.assertEqual(normalize_combo(None), "")  # type: ignore[arg-type]

    def test_keys_are_deduplicated_and_formatted(self) -> None:
        keys = normalize_keys(["Ctrl+S", "ctrl+s", "Alt+F4", ""])
        self.assertEqual(keys, ("ctrl+s", "alt+f4"))
        self.assertEqual(format_combo("ctrl+shift+p"), "Ctrl+Shift+P")
        self.assertEqual(format_combo("alt+f4"), "Alt+F4")
        self.assertEqual(format_keys(("ctrl+s", "alt+f4")), "Ctrl+S · Alt+F4")


class CommandRegistryTests(unittest.TestCase):
    def test_execute_and_duplicate_ids(self) -> None:
        calls = []
        registry = CommandRegistry()
        registry.register(Command("app.test", "Test", lambda: calls.append(1)))
        self.assertTrue(registry.execute("app.test"))
        self.assertEqual(calls, [1])
        with self.assertRaises(ValueError):
            registry.register(Command("app.test", "Again", lambda: None))

    def test_false_callback_means_not_handled(self) -> None:
        registry = CommandRegistry()
        registry.register(Command("app.test", "Test", lambda: False))
        self.assertFalse(registry.execute("app.test"))

    def test_when_predicate_disables_command(self) -> None:
        calls = []
        enabled = [False]
        registry = CommandRegistry()
        registry.register(
            Command(
                "notes.test",
                "Test",
                lambda: calls.append(1),
                scope="notes",
                when=lambda: enabled[0],
            )
        )
        self.assertFalse(registry.execute("notes.test"))
        enabled[0] = True
        self.assertTrue(registry.execute("notes.test"))
        self.assertEqual(calls, [1])

    def test_exception_is_contained(self) -> None:
        registry = CommandRegistry()
        registry.register(
            Command("app.bad", "Bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        )
        self.assertFalse(registry.execute("app.bad"))


class ShortcutManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.tmp.name)
        self.registry = CommandRegistry()
        self.notes_save = Command(
            "notes.save",
            "Save note",
            lambda: None,
            category="Notes",
            default_keys=("ctrl+s",),
            scope="notes",
        )
        self.timer_start = Command(
            "timer.start",
            "Start timer",
            lambda: None,
            category="Timer",
            default_keys=("ctrl+shift+t",),
            scope="timer",
            global_default=True,
        )
        self.app_palette = Command(
            "app.palette",
            "Command palette",
            lambda: None,
            category="App",
            default_keys=("ctrl+shift+p",),
        )
        self.registry.register_many([self.notes_save, self.timer_start, self.app_palette])
        self.manager = ShortcutManager(self.storage, self.registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_defaults_follow_scope_and_global_flag(self) -> None:
        self.assertEqual(self.manager.keys_for("notes.save"), ("ctrl+s",))
        self.assertTrue(self.manager.is_global("timer.start"))
        self.assertFalse(self.manager.is_global("notes.save"))
        self.assertEqual(
            [b.command.id for b in self.manager.find_bindings("ctrl+s", "notes")],
            ["notes.save"],
        )
        self.assertEqual(self.manager.find_bindings("ctrl+s", "calendar"), [])

    def test_app_commands_are_active_everywhere(self) -> None:
        found = self.manager.find_bindings("ctrl+shift+p", "calendar")
        self.assertEqual([b.command.id for b in found], ["app.palette"])

    def test_override_persists_and_can_be_reset(self) -> None:
        self.manager.set_keys("notes.save", ["ctrl+alt+s"])
        self.assertEqual(self.manager.keys_for("notes.save"), ("ctrl+alt+s",))
        self.assertNotIn("ctrl+s", self.manager.keys_for("notes.save"))

        reloaded = ShortcutManager(self.storage, self.registry)
        self.assertEqual(reloaded.keys_for("notes.save"), ("ctrl+alt+s",))
        reloaded.reset("notes.save")
        self.assertEqual(reloaded.keys_for("notes.save"), ("ctrl+s",))
        self.assertNotIn("notes.save", reloaded._overrides)

    def test_explicit_empty_binding_persists(self) -> None:
        self.manager.set_keys("notes.save", [])
        self.assertEqual(self.manager.keys_for("notes.save"), ())
        reloaded = ShortcutManager(self.storage, self.registry)
        self.assertEqual(reloaded.keys_for("notes.save"), ())
        self.assertIn("notes.save", reloaded._overrides)

    def test_global_toggle_persists(self) -> None:
        self.manager.set_global("notes.save", True)
        self.assertTrue(self.manager.is_global("notes.save"))
        reloaded = ShortcutManager(self.storage, self.registry)
        self.assertTrue(reloaded.is_global("notes.save"))
        self.assertEqual(reloaded.keys_for("notes.save"), ("ctrl+s",))

    def test_conflicts_only_in_overlapping_scopes(self) -> None:
        self.assertEqual(
            [b.command.id for b in self.manager.conflicts("ctrl+s", scope="calendar")],
            [],
        )
        self.assertEqual(
            [b.command.id for b in self.manager.conflicts("ctrl+s", scope="notes")],
            ["notes.save"],
        )
        # app scope collides with a module shortcut
        self.assertEqual(
            [b.command.id for b in self.manager.conflicts("ctrl+s", scope="app")],
            ["notes.save"],
        )

    def test_global_binding_conflicts_across_modules(self) -> None:
        self.manager.set_keys("timer.start", ["ctrl+s"])
        conflicts = self.manager.conflicts("ctrl+s", scope="notes")
        ids = {binding.command.id for binding in conflicts}
        self.assertEqual(ids, {"notes.save", "timer.start"})

    def test_conflict_excludes_the_same_command(self) -> None:
        self.assertEqual(
            self.manager.conflicts("ctrl+s", exclude_command_id="notes.save", scope="notes"),
            [],
        )

    def test_duplicate_bindings_are_detected(self) -> None:
        self.manager.set_keys("app.palette", ["ctrl+s"])
        self.assertTrue(self.manager.has_conflicts())


if __name__ == "__main__":
    unittest.main()
