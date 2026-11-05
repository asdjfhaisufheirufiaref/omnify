"""Headless tests for the global evdev input monitor (no real devices).

The monitor's device opener is injectable, so the tests feed it crafted
``input_event`` structs through pipes instead of /dev/input.

Run with:  python3 -m unittest tests.test_input_monitor
"""

import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.input_monitor import (  # noqa: E402
    EV_KEY,
    InputMonitor,
    combo_for,
    key_name,
)

_EVENT_FORMAT = "llHHi"


def make_event(etype: int, code: int, value: int) -> bytes:
    """One 24-byte ``input_event`` (the timestamps are ignored)."""
    return struct.pack(_EVENT_FORMAT, 1, 0, etype, code, value)


def wait_for(predicate, timeout: float = 3.0):
    """Poll *predicate* until it is true or *timeout* expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class FakeDevice:
    """A fake /dev/input device: every ``open()`` yields a fresh pipe,
    and writes are broadcast to every currently-open pipe — exactly how
    the kernel delivers events to all readers of a device node.
    """

    def __init__(self) -> None:
        self.pipes = []  # (read_fd, write_fd) per open

    def opener(self, path: str) -> int:
        read_fd, write_fd = os.pipe()
        self.pipes.append((read_fd, write_fd))
        return read_fd

    def write_events(self, *events: bytes) -> None:
        data = b"".join(events)
        for _read_fd, write_fd in list(self.pipes):
            try:
                os.write(write_fd, data)
            except OSError:
                pass  # that reader closed (e.g. after stop())

    def close(self) -> None:
        for read_fd, write_fd in self.pipes:
            for fd in (read_fd, write_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.pipes = []


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.device_dir = Path(self.tmp.name)
        # the discovery step lists this directory for "event*" names
        (self.device_dir / "event0").touch()
        (self.device_dir / "not-a-device").touch()
        self.device = FakeDevice()
        self.monitor = InputMonitor(
            device_dir=str(self.device_dir), opener=self.device.opener
        )
        self.clicks = []
        self.combos = []
        self.monitor.subscribe_click(lambda: self.clicks.append(None))
        self.monitor.subscribe_key_combo(self.combos.append)

    def tearDown(self):
        self.monitor.stop()
        self.device.close()
        self.tmp.cleanup()

    # -- click counting ---------------------------------------------------
    def test_left_press_counts_release_and_other_buttons_do_not(self):
        self.assertTrue(self.monitor.start())
        self.device.write_events(
            make_event(EV_KEY, 0x110, 1),  # BTN_LEFT press  -> counts
            make_event(EV_KEY, 0x110, 0),  # BTN_LEFT release -> ignored
            make_event(EV_KEY, 0x111, 1),  # BTN_RIGHT press -> ignored
            make_event(EV_KEY, 0x110, 2),  # auto-repeat value -> ignored
        )
        self.assertTrue(wait_for(lambda: len(self.clicks) == 1))
        self.assertFalse(wait_for(lambda: len(self.clicks) == 2, timeout=0.4))
        self.assertEqual(self.clicks, [None])

    def test_two_presses_two_clicks(self):
        self.assertTrue(self.monitor.start())
        self.device.write_events(
            make_event(EV_KEY, 0x110, 1),
            make_event(EV_KEY, 0x110, 0),
            make_event(EV_KEY, 0x110, 1),
        )
        self.assertTrue(wait_for(lambda: len(self.clicks) == 2))

    # -- key combos -----------------------------------------------------------
    def test_plain_key(self):
        self.assertTrue(self.monitor.start())
        self.device.write_events(make_event(EV_KEY, 30, 1))  # 'a'
        self.assertTrue(wait_for(lambda: self.combos == ["a"]))

    def test_modifiers_are_tracked_and_combo_renders(self):
        self.assertTrue(self.monitor.start())
        self.device.write_events(
            make_event(EV_KEY, 29, 1),   # ctrl down
            make_event(EV_KEY, 42, 1),   # shift down
            make_event(EV_KEY, 31, 1),   # 's'
            make_event(EV_KEY, 29, 0),   # ctrl up
            make_event(EV_KEY, 42, 0),   # shift up
            make_event(EV_KEY, 31, 1),   # 's' again, alone
        )
        self.assertTrue(wait_for(lambda: len(self.combos) == 2))
        self.assertEqual(self.combos, ["ctrl+shift+s", "s"])

    def test_release_and_auto_repeat_ignored(self):
        self.assertTrue(self.monitor.start())
        self.device.write_events(
            make_event(EV_KEY, 30, 1),  # press -> counts
            make_event(EV_KEY, 30, 2),  # auto-repeat -> ignored
            make_event(EV_KEY, 30, 0),  # release -> ignored
        )
        self.assertTrue(wait_for(lambda: len(self.combos) == 1))
        self.assertFalse(
            wait_for(lambda: len(self.combos) == 2, timeout=0.4)
        )

    def test_partial_reads_are_buffered(self):
        """A read delivering less than one full event must not corrupt
        the next one (the per-fd remainder buffer handles it)."""
        self.assertTrue(self.monitor.start())
        full = make_event(EV_KEY, 30, 1) + make_event(EV_KEY, 31, 1)
        os.write(self.device.pipes[-1][1], full[:30])  # 1.25 events
        time.sleep(0.1)
        os.write(self.device.pipes[-1][1], full[30:])
        self.assertTrue(wait_for(lambda: len(self.combos) == 2))
        self.assertEqual(self.combos, ["a", "s"])  # 30='a', 31='s'

    def test_broken_subscriber_cannot_kill_the_reader(self):
        """A subscriber that raises must not take down the monitor
        thread (a dead reader is the silent failure mode)."""
        self.assertTrue(self.monitor.start())

        def bad_click():
            raise RuntimeError("subscriber bug")

        self.monitor.subscribe_click(bad_click)
        self.device.write_events(make_event(EV_KEY, 0x110, 1))
        # the good subscriber still receives the click
        self.assertTrue(wait_for(lambda: len(self.clicks) == 1))
        # and the reader keeps running for the next event
        self.device.write_events(make_event(EV_KEY, 0x110, 1))
        self.assertTrue(wait_for(lambda: len(self.clicks) == 2))

    # -- lifecycle and degradation ------------------------------------------
    def test_start_is_idempotent(self):
        self.assertTrue(self.monitor.start())
        self.assertTrue(self.monitor.start())  # no-op the second time
        self.assertTrue(self.monitor.available)

    def test_stop_closes_devices(self):
        self.assertTrue(self.monitor.start())
        read_fd = self.device.pipes[-1][0]
        self.monitor.stop()
        self.assertFalse(self.monitor.available)
        self.assertFalse(self.monitor.running)
        # the pipe's read end must be closed again
        with self.assertRaises(OSError):
            os.fstat(read_fd)
        # stop is idempotent
        self.monitor.stop()

    def test_start_after_stop_works(self):
        self.assertTrue(self.monitor.start())
        self.device.write_events(make_event(EV_KEY, 30, 1))
        self.assertTrue(wait_for(lambda: len(self.combos) == 1))
        self.monitor.stop()
        self.assertTrue(self.monitor.start())  # a fresh open()
        self.device.write_events(make_event(EV_KEY, 31, 1))
        self.assertTrue(wait_for(lambda: len(self.combos) == 2))

    def test_missing_device_dir(self):
        monitor = InputMonitor(device_dir=str(self.device_dir / "nope"))
        self.assertFalse(monitor.start())
        self.assertFalse(monitor.available)
        self.assertIn("cannot read", monitor.error)

    def test_unreadable_devices(self):
        def failing_opener(path: str) -> int:
            raise OSError("permission denied")

        monitor = InputMonitor(
            device_dir=str(self.device_dir), opener=failing_opener
        )
        self.assertFalse(monitor.start())
        self.assertFalse(monitor.available)
        self.assertIn("input", monitor.error)  # the group hint


class TestKeyNames(unittest.TestCase):
    def test_letters(self):
        # the QWERTY rows are NOT alphabet-contiguous — verify each one
        self.assertEqual(key_name(16), "q")
        self.assertEqual(key_name(17), "w")
        self.assertEqual(key_name(25), "p")
        self.assertEqual(key_name(30), "a")
        self.assertEqual(key_name(31), "s")
        self.assertEqual(key_name(38), "l")
        self.assertEqual(key_name(44), "z")
        self.assertEqual(key_name(49), "n")
        self.assertEqual(key_name(50), "m")

    def test_digits(self):
        self.assertEqual(key_name(2), "1")
        self.assertEqual(key_name(11), "0")

    def test_function_keys(self):
        self.assertEqual(key_name(59), "f1")
        self.assertEqual(key_name(68), "f10")
        self.assertEqual(key_name(87), "f11")
        self.assertEqual(key_name(96), "f20")

    def test_fixed_names(self):
        self.assertEqual(key_name(57), "space")
        self.assertEqual(key_name(1), "escape")
        self.assertEqual(key_name(28), "return")
        self.assertEqual(key_name(111), "delete")
        self.assertEqual(key_name(105), "left")

    def test_unmapped_is_none(self):
        self.assertIsNone(key_name(0))
        self.assertIsNone(key_name(400))  # a button code, not a key


class TestComboFor(unittest.TestCase):
    def test_no_modifiers(self):
        self.assertEqual(combo_for("x", set()), "x")

    def test_modifier_order(self):
        # ctrl(29) + shift(42) + key — order is ctrl, shift, alt
        self.assertEqual(combo_for("s", {29, 42}), "ctrl+shift+s")

    def test_right_variants_share_names(self):
        self.assertEqual(combo_for("a", {97}), "ctrl+a")   # right ctrl
        self.assertEqual(combo_for("a", {54}), "shift+a")  # right shift
        self.assertEqual(combo_for("a", {100}), "alt+a")   # right alt

    def test_all_modifiers(self):
        self.assertEqual(
            combo_for("f1", {29, 42, 56}), "ctrl+shift+alt+f1"
        )


if __name__ == "__main__":
    unittest.main()
