"""Headless tests for the alerts helpers (SoundPlayer + Notifier, no GTK).

Run with:  python3 tests/test_alerts.py
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.alerts import Notifier, SoundPlayer  # noqa: E402


class FakeProc:
    """A stand-in for subprocess.Popen."""

    def __init__(self) -> None:
        self.terminated = False
        self._done = threading.Event()

    def terminate(self) -> None:
        self.terminated = True
        self._done.set()

    def wait(self, timeout=None):
        if self._done.wait(timeout):
            return 0
        raise subprocess.TimeoutExpired("fake", timeout)


class FakeRunner:
    """Records spawned commands and returns FakeProcs."""

    def __init__(self, fail: bool = False) -> None:
        self.calls = []
        self.procs = []
        self.fail = fail

    def __call__(self, args, **kwargs):
        if self.fail:
            raise OSError("spawn failed")
        proc = FakeProc()
        self.calls.append(list(args))
        self.procs.append(proc)
        return proc


def make_which(paths):
    def which(name):
        return paths.get(name)

    return which


class TestSoundPlayer(unittest.TestCase):
    def test_no_player_installed(self):
        player = SoundPlayer(which=lambda n: None, runner=FakeRunner())
        self.assertFalse(player.available("/tmp/whatever.wav"))
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            self.assertFalse(player.play(handle.name))

    def test_pcm_uses_paplay(self):
        runner = FakeRunner()
        which = make_which(
            {"paplay": "/usr/bin/paplay", "ffplay": "/usr/bin/ffplay"}
        )
        player = SoundPlayer(which=which, runner=runner)
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            self.assertTrue(player.play(handle.name))
        self.assertEqual(runner.calls, [["/usr/bin/paplay", handle.name]])

    def test_compressed_uses_ffplay_headless(self):
        runner = FakeRunner()
        which = make_which(
            {"paplay": "/usr/bin/paplay", "ffplay": "/usr/bin/ffplay"}
        )
        player = SoundPlayer(which=which, runner=runner)
        with tempfile.NamedTemporaryFile(suffix=".oga") as handle:
            self.assertTrue(player.play(handle.name))
        args = runner.calls[0]
        self.assertEqual(args[0], "/usr/bin/ffplay")
        self.assertIn("-nodisp", args)
        self.assertIn("-autoexit", args)
        self.assertNotIn("-stream_loop", args)

    def test_loop_with_duration_terminates_afterwards(self):
        runner = FakeRunner()
        which = make_which({"paplay": "/usr/bin/paplay"})
        player = SoundPlayer(which=which, runner=runner)
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            self.assertTrue(player.play(handle.name, duration=0.05))
        # looping is done by SoundPlayer, not by player-specific flags
        self.assertEqual(runner.calls[0], ["/usr/bin/paplay", handle.name])
        self.assertFalse(runner.procs[0].terminated)
        time.sleep(0.3)  # let the daemon loop thread reach the deadline
        self.assertTrue(runner.procs[0].terminated)

    def test_play_missing_file_fails(self):
        runner = FakeRunner()
        which = make_which({"paplay": "/usr/bin/paplay"})
        player = SoundPlayer(which=which, runner=runner)
        self.assertFalse(player.play("/nonexistent/file.wav"))
        self.assertEqual(runner.calls, [])

    def test_spawn_failure_returns_false(self):
        which = make_which({"paplay": "/usr/bin/paplay"})
        player = SoundPlayer(which=which, runner=FakeRunner(fail=True))
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            self.assertFalse(player.play(handle.name))

    def test_stop_terminates_active(self):
        runner = FakeRunner()
        which = make_which({"paplay": "/usr/bin/paplay"})
        player = SoundPlayer(which=which, runner=runner)
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            self.assertTrue(player.play(handle.name))
        player.stop()
        self.assertTrue(runner.procs[0].terminated)
        # stopping again is a no-op
        player.stop()


class TestNotifier(unittest.TestCase):
    def test_availability(self):
        self.assertTrue(Notifier(which=lambda n: "/usr/bin/notify-send").available)
        self.assertFalse(Notifier(which=lambda n: None).available)

    def test_notify_full_arguments(self):
        runner = FakeRunner()
        notifier = Notifier(which=lambda n: "/usr/bin/notify-send", runner=runner)
        self.assertTrue(
            notifier.notify(
                "Title",
                "Body",
                urgency="low",
                timeout_ms=10000,
                replace_id=7,
                icon="clock",
            )
        )
        self.assertEqual(
            runner.calls[0],
            [
                "/usr/bin/notify-send",
                "--app-name",
                "Omnify",
                "-r",
                "7",
                "-i",
                "clock",
                "-u",
                "low",
                "-t",
                "10000",
                "Title",
                "Body",
            ],
        )

    def test_notify_minimal_arguments(self):
        runner = FakeRunner()
        notifier = Notifier(which=lambda n: "/usr/bin/notify-send", runner=runner)
        self.assertTrue(notifier.notify("Only title"))
        self.assertEqual(
            runner.calls[0],
            [
                "/usr/bin/notify-send",
                "--app-name",
                "Omnify",
                "-t",
                "5000",
                "Only title",
                "",
            ],
        )

    def test_persistent_timeout_is_negative(self):
        runner = FakeRunner()
        notifier = Notifier(which=lambda n: "/usr/bin/notify-send", runner=runner)
        self.assertTrue(notifier.notify("T", "B", timeout_ms=-1))
        self.assertIn("-t", runner.calls[0])
        self.assertEqual(runner.calls[0][runner.calls[0].index("-t") + 1], "-1")

    def test_no_notify_send_returns_false(self):
        runner = FakeRunner()
        notifier = Notifier(which=lambda n: None, runner=runner)
        self.assertFalse(notifier.notify("T"))
        self.assertEqual(runner.calls, [])

    def test_spawn_failure_returns_false(self):
        notifier = Notifier(
            which=lambda n: "/usr/bin/notify-send", runner=FakeRunner(fail=True)
        )
        self.assertFalse(notifier.notify("T"))

    def test_dismiss_sends_empty_update(self):
        runner = FakeRunner()
        notifier = Notifier(which=lambda n: "/usr/bin/notify-send", runner=runner)
        self.assertTrue(notifier.dismiss(42))
        args = runner.calls[0]
        self.assertIn("-r", args)
        self.assertEqual(args[args.index("-r") + 1], "42")
        self.assertEqual(args[-2:], ["", ""])


if __name__ == "__main__":
    unittest.main()
