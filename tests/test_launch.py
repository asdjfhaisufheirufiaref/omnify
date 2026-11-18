"""Headless tests for launch-option parsing."""

from __future__ import annotations

import unittest

from omnify.core.launch import LaunchRequest, parse_launch_args


class LaunchArgsTests(unittest.TestCase):
    def test_empty_command_line(self) -> None:
        self.assertEqual(parse_launch_args([]), LaunchRequest())

    def test_module_in_two_forms(self) -> None:
        self.assertEqual(
            parse_launch_args(["omnify", "--module", "notes"]).module,
            "notes",
        )
        self.assertEqual(
            parse_launch_args(["omnify", "--module=notes"]).module,
            "notes",
        )

    def test_command_in_two_forms(self) -> None:
        self.assertEqual(
            parse_launch_args(["--command", "timer.start"]).command,
            "timer.start",
        )
        self.assertEqual(
            parse_launch_args(["--command=timer.start"]).command,
            "timer.start",
        )

    def test_window_aliases_enable_standalone(self) -> None:
        self.assertTrue(parse_launch_args(["--window"]).standalone)
        self.assertTrue(parse_launch_args(["--standalone"]).standalone)
        self.assertFalse(parse_launch_args(["omnify"]).standalone)

    def test_all_options_together(self) -> None:
        request = parse_launch_args(
            [
                "omnify",
                "--module",
                "timer",
                "--window",
                "--command",
                "timer.start",
            ]
        )
        self.assertEqual(request.module, "timer")
        self.assertEqual(request.command, "timer.start")
        self.assertTrue(request.standalone)

    def test_last_value_wins(self) -> None:
        request = parse_launch_args(
            [
                "--module",
                "notes",
                "--module=browser",
                "--command=a",
                "--command=b",
            ]
        )
        self.assertEqual(request.module, "browser")
        self.assertEqual(request.command, "b")

    def test_missing_values_are_ignored(self) -> None:
        for args in (["--module"], ["--command"], ["--module", ""]):
            request = parse_launch_args(args)
            self.assertIsNone(request.module)
            self.assertIsNone(request.command)
            self.assertFalse(request.standalone)

    def test_unknown_options_and_positionals_are_ignored(self) -> None:
        request = parse_launch_args(
            ["omnify", "--verbose", "file.md", "--module", "notes"]
        )
        self.assertEqual(request.module, "notes")
        self.assertIsNone(request.command)
        self.assertFalse(request.standalone)


if __name__ == "__main__":
    unittest.main(verbosity=2)
