"""Every module contributes palette-ready, correctly scoped commands."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.commands import CommandRegistry  # noqa: E402
from omnify.core.module_context import ModuleContext  # noqa: E402
from omnify.core.storage import Storage  # noqa: E402
from omnify.modules import ALL_MODULES  # noqa: E402


class ModuleCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = ModuleContext(Storage(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_all_module_commands_have_unique_ids(self) -> None:
        registry = CommandRegistry()
        for module_cls in ALL_MODULES:
            module = module_cls(self.ctx)
            for command in module.commands():
                self.assertEqual(command.scope, module.id)
                registry.register(command)
        self.assertGreater(len(registry.all()), 50)
        self.assertIsNotNone(registry.get("notes.save"))
        self.assertIsNotNone(registry.get("calendar.new_event"))
        self.assertIsNotNone(registry.get("timer.start"))
        self.assertIsNotNone(registry.get("stopwatch.toggle"))
        self.assertIsNotNone(registry.get("tasks.refresh"))
        self.assertIsNotNone(registry.get("browser.open"))

    def test_default_timer_shortcuts_can_be_global(self) -> None:
        by_id = {module_cls.id: module_cls for module_cls in ALL_MODULES}
        timer_commands = {
            command.id: command for command in by_id["timer"](self.ctx).commands()
        }
        stopwatch_commands = {
            command.id: command
            for command in by_id["stopwatch"](self.ctx).commands()
        }
        self.assertTrue(timer_commands["timer.start"].global_default)
        self.assertTrue(timer_commands["timer.toggle"].global_default)
        self.assertTrue(stopwatch_commands["stopwatch.toggle"].global_default)

    def test_commands_are_not_available_before_the_view_exists(self) -> None:
        notes = [cls for cls in ALL_MODULES if cls.id == "notes"][0](self.ctx)
        commands = {command.id: command for command in notes.commands()}
        self.assertFalse(commands["notes.save"].available())
        self.assertFalse(commands["calendar.new_event"].available()) if "calendar.new_event" in commands else None


if __name__ == "__main__":
    unittest.main()
