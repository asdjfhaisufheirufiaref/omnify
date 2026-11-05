"""Headless tests for the stopwatch module (StopwatchModel + formatter, no GTK).

Run with:  python3 tests/test_stopwatch.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.stopwatch import StopwatchModel, format_stopwatch  # noqa: E402


class FakeClock:
    """A hand-driven monotonic clock so tests control time exactly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestStopwatchModel(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.model = StopwatchModel(clock=self.clock)

    def test_initial_state(self):
        self.assertEqual(self.model.state, "stopped")
        self.assertEqual(self.model.elapsed, 0.0)
        self.assertEqual(self.model.laps, [])

    def test_start_from_stopped_grows_exactly(self):
        self.model.start()
        self.assertEqual(self.model.state, "running")
        self.assertEqual(self.model.elapsed, 0.0)
        self.clock.advance(10.0)
        self.assertEqual(self.model.elapsed, 10.0)
        self.clock.advance(0.25)
        self.assertEqual(self.model.elapsed, 10.25)

    def test_start_twice_is_rejected(self):
        self.model.start()
        with self.assertRaises(ValueError):
            self.model.start()

    def test_start_from_paused_resumes_frozen_value(self):
        self.model.start()
        self.clock.advance(20.0)
        self.model.pause()
        self.assertEqual(self.model.elapsed, 20.0)
        # While paused the clock may run forever; nothing changes.
        self.clock.advance(999.0)
        self.assertEqual(self.model.elapsed, 20.0)
        self.model.start()
        self.assertEqual(self.model.state, "running")
        # No time lost or gained: continues from the frozen 20 s.
        self.clock.advance(15.0)
        self.assertEqual(self.model.elapsed, 35.0)

    def test_pause_freezes_elapsed(self):
        self.model.start()
        self.clock.advance(30.0)
        self.model.pause()
        self.assertEqual(self.model.state, "paused")
        self.assertEqual(self.model.elapsed, 30.0)
        self.clock.advance(1000.0)
        self.assertEqual(self.model.elapsed, 30.0)

    def test_pause_from_wrong_states(self):
        with self.assertRaises(ValueError):
            self.model.pause()  # stopped
        self.model.start()
        self.model.pause()
        with self.assertRaises(ValueError):
            self.model.pause()  # already paused

    def test_reset_from_any_state(self):
        # From stopped: trivially fine, never raises.
        self.model.reset()
        self.assertEqual(self.model.state, "stopped")
        self.assertEqual(self.model.elapsed, 0.0)
        # From running, with a lap recorded.
        self.model.start()
        self.clock.advance(5.0)
        self.model.lap()
        self.model.reset()
        self.assertEqual(self.model.state, "stopped")
        self.assertEqual(self.model.elapsed, 0.0)
        self.assertEqual(self.model.laps, [])
        # From paused.
        self.model.start()
        self.clock.advance(7.0)
        self.model.pause()
        self.model.reset()
        self.assertEqual(self.model.state, "stopped")
        self.assertEqual(self.model.elapsed, 0.0)

    def test_start_after_reset_begins_fresh(self):
        self.model.start()
        self.clock.advance(100.0)
        self.model.pause()
        self.model.reset()
        self.model.start()
        self.assertEqual(self.model.state, "running")
        # The old frozen base must not leak into the new run.
        self.assertEqual(self.model.elapsed, 0.0)
        self.clock.advance(3.0)
        self.assertEqual(self.model.elapsed, 3.0)

    def test_lap_from_wrong_states(self):
        with self.assertRaises(ValueError):
            self.model.lap()  # stopped
        self.model.start()
        self.model.pause()
        with self.assertRaises(ValueError):
            self.model.lap()  # paused

    def test_lap_returns_cumulative_and_appends(self):
        self.model.start()
        self.clock.advance(5.0)
        first = self.model.lap()
        self.assertEqual(first, 5.0)
        self.assertEqual(self.model.elapsed, 5.0)
        self.clock.advance(7.0)
        second = self.model.lap()
        self.assertEqual(second, 12.0)
        self.assertEqual(self.model.laps, [5.0, 12.0])

    def test_laps_oldest_first_and_copy(self):
        self.model.start()
        self.clock.advance(1.0)
        self.model.lap()
        self.clock.advance(2.0)
        self.model.lap()
        self.clock.advance(3.0)
        self.model.lap()
        self.assertEqual(self.model.laps, [1.0, 3.0, 6.0])
        # Mutating the returned list must not touch the model's state.
        laps = self.model.laps
        laps.append(99.0)
        self.assertEqual(self.model.laps, [1.0, 3.0, 6.0])


class TestStopwatchTriggers(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.model = StopwatchModel(clock=self.clock)

    def test_clicks_trigger_toggles(self):
        self.model.set_trigger("clicks", count=2)
        self.assertFalse(self.model.trigger_action("clicks"))
        self.assertTrue(self.model.trigger_action("clicks"))
        self.assertEqual(self.model.state, "running")
        # second arming: the same trigger now pauses
        self.model.set_trigger("clicks", count=1)
        self.assertTrue(self.model.trigger_action("clicks"))
        self.assertEqual(self.model.state, "paused")
        # third: resumes
        self.model.set_trigger("clicks", count=1)
        self.assertTrue(self.model.trigger_action("clicks"))
        self.assertEqual(self.model.state, "running")

    def test_keys_trigger_requires_matching_kind(self):
        self.model.set_trigger("keys", count=1, key="space")
        self.assertFalse(self.model.trigger_action("clicks"))
        self.assertTrue(self.model.trigger_action("keys"))
        self.assertEqual(self.model.state, "running")
        self.assertEqual(self.model.trigger_kind, "manual")  # disarmed

    def test_at_trigger_autostarts_via_tick(self):
        self.model.set_trigger("at", at=self.clock.now + 10)
        self.clock.advance(10)
        self.assertTrue(self.model.tick())
        self.assertEqual(self.model.state, "running")
        # disarmed: no second start
        self.assertFalse(self.model.tick())

    def test_set_trigger_validation(self):
        with self.assertRaises(ValueError):
            self.model.set_trigger("bogus")
        with self.assertRaises(ValueError):
            self.model.set_trigger("clicks", count=0)
        with self.assertRaises(ValueError):
            self.model.set_trigger("clicks", count=True)


class TestStopwatchPersistence(unittest.TestCase):
    def test_roundtrip_running(self):
        clock = FakeClock()
        model = StopwatchModel(clock=clock)
        model.start()
        clock.advance(30)
        model.lap()
        model2 = StopwatchModel(clock=clock)
        model2.from_dict(model.to_dict())
        self.assertEqual(model2.state, "running")
        self.assertEqual(model2.elapsed, 30.0)
        self.assertEqual(model2.laps, [30.0])

    def test_roundtrip_paused(self):
        clock = FakeClock()
        model = StopwatchModel(clock=clock)
        model.start()
        clock.advance(12)
        model.pause()
        model2 = StopwatchModel(clock=clock)
        model2.from_dict(model.to_dict())
        self.assertEqual(model2.state, "paused")
        # paused value stays frozen even if the clock runs on
        clock.advance(500)
        self.assertEqual(model2.elapsed, 12.0)

    def test_running_survives_reboot_gap(self):
        clock = FakeClock(start=1_000_000.0)
        model = StopwatchModel(clock=clock)
        model.start()
        data = model.to_dict()
        # "the computer was off" for an hour
        clock.advance(3600)
        model2 = StopwatchModel(clock=clock)
        model2.from_dict(data)
        self.assertEqual(model2.state, "running")
        self.assertEqual(model2.elapsed, 3600.0)

    def test_garbage_is_ignored(self):
        model = StopwatchModel(clock=FakeClock())
        model.from_dict("junk")
        model.from_dict(None)
        model.from_dict({"state": "bogus"})
        self.assertEqual(model.state, "stopped")
        self.assertEqual(model.elapsed, 0.0)
        self.assertEqual(model.laps, [])


class TestFormatStopwatch(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_stopwatch(0), "00:00.00")

    def test_sub_10ms_truncates(self):
        # Centiseconds are floored, never rounded up.
        self.assertEqual(format_stopwatch(9), "00:00.00")
        self.assertEqual(format_stopwatch(9.9), "00:00.00")

    def test_small_values(self):
        self.assertEqual(format_stopwatch(999.9), "00:00.99")
        self.assertEqual(format_stopwatch(1000), "00:01.00")

    def test_centisecond_truncation(self):
        self.assertEqual(format_stopwatch(10999.9), "00:10.99")
        self.assertEqual(format_stopwatch(11000.5), "00:11.00")

    def test_rollover(self):
        self.assertEqual(format_stopwatch(60000), "01:00.00")
        # Minutes grow beyond two digits if needed.
        self.assertEqual(format_stopwatch(3661000), "61:01.00")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(format_stopwatch(-1), "00:00.00")
        self.assertEqual(format_stopwatch(-3600000), "00:00.00")


if __name__ == "__main__":
    unittest.main()
