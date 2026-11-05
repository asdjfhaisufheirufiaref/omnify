"""Headless tests for the timer module (TimerManager + presets, no GTK).

Run with:  python3 tests/test_timer.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.storage import Storage  # noqa: E402
from omnify.modules.timer import (  # noqa: E402
    DEFAULT_DURATION,
    TRIGGER_AT,
    TRIGGER_CLICKS,
    TRIGGER_KEYS,
    TRIGGER_MANUAL,
    PresetStore,
    TimerEntry,
    TimerManager,
    duration_seconds,
    format_timer,
)


class FakeClock:
    """A hand-driven wall clock so tests control time exactly."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTimerManager(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.finished = []
        self.mgr = TimerManager(clock=self.clock, on_finish=self.finished.append)
        self.tid = self.mgr.add(minutes=1)  # 60 s

    # -- collection -------------------------------------------------------
    def test_add_defaults_to_default_duration(self):
        # a fresh manager: nothing has been "remembered" yet
        mgr = TimerManager(clock=self.clock)
        tid = mgr.add()
        entry = mgr.get(tid)
        self.assertEqual(entry.total, DEFAULT_DURATION["minutes"] * 60)
        self.assertEqual(entry.remaining, 300.0)
        self.assertEqual(entry.state, "idle")

    def test_add_remembers_last_duration(self):
        self.mgr.add(hours=1, minutes=23, seconds=45)
        tid = self.mgr.add()
        entry = self.mgr.get(tid)
        self.assertEqual(
            (entry.hours, entry.minutes, entry.seconds), (1, 23, 45)
        )

    def test_add_validates(self):
        with self.assertRaises(ValueError):
            self.mgr.add(minutes=0)  # zero total
        with self.assertRaises(ValueError):
            self.mgr.add(minutes=60)  # out of range
        with self.assertRaises(ValueError):
            self.mgr.add(weeks=True)  # bool is not an int here
        with self.assertRaises(ValueError):
            self.mgr.add(days=-1)

    def test_remove_and_get(self):
        with self.assertRaises(KeyError):
            self.mgr.get("nope")
        self.mgr.remove(self.tid)
        self.assertEqual(len(self.mgr), 0)
        with self.assertRaises(KeyError):
            self.mgr.remove(self.tid)

    def test_all_keeps_insertion_order(self):
        first = self.mgr.add()
        second = self.mgr.add()
        self.assertEqual(
            [e.id for e in self.mgr.all()], [self.tid, first, second]
        )

    def test_reorder_matches_given_order(self):
        first = self.mgr.add()
        second = self.mgr.add()
        self.mgr.reorder([second, self.tid, first])
        self.assertEqual(
            [e.id for e in self.mgr.all()], [second, self.tid, first]
        )
        # the entries themselves are intact (same objects, same data;
        # a bare add() reuses the last duration, which is 1 minute here)
        self.assertEqual(self.mgr.get(second).total, 60)

    def test_reorder_ignores_unknown_and_duplicates(self):
        first = self.mgr.add()
        self.mgr.reorder(["nope", first, first, self.tid, "also-nope"])
        self.assertEqual(
            [e.id for e in self.mgr.all()], [first, self.tid]
        )

    def test_reorder_with_partial_list_keeps_rest_at_end(self):
        first = self.mgr.add()
        second = self.mgr.add()
        # only move `first` to the front; the rest keeps its order
        self.mgr.reorder([first, second])
        self.assertEqual(
            [e.id for e in self.mgr.all()], [first, second, self.tid]
        )

    # -- state machine ------------------------------------------------------
    def test_start_pause_resume_reset(self):
        self.mgr.start(self.tid)
        self.assertEqual(self.mgr.get(self.tid).state, "running")
        self.clock.advance(25)
        self.assertEqual(self.mgr.remaining_of(self.tid), 35.0)
        self.mgr.pause(self.tid)
        self.assertEqual(self.mgr.get(self.tid).state, "paused")
        self.assertEqual(self.mgr.remaining_of(self.tid), 35.0)
        # paused: the clock may run forever, nothing changes
        self.clock.advance(999)
        self.assertEqual(self.mgr.remaining_of(self.tid), 35.0)
        self.mgr.resume(self.tid)
        self.assertEqual(self.mgr.get(self.tid).state, "running")
        self.clock.advance(35)
        self.assertEqual(self.mgr.remaining_of(self.tid), 0.0)
        self.mgr.reset(self.tid)
        self.assertEqual(self.mgr.get(self.tid).state, "idle")
        self.assertEqual(self.mgr.remaining_of(self.tid), 60.0)

    def test_illegal_transitions_raise(self):
        with self.assertRaises(ValueError):
            self.mgr.pause(self.tid)  # idle
        with self.assertRaises(ValueError):
            self.mgr.resume(self.tid)  # idle
        self.mgr.start(self.tid)
        with self.assertRaises(ValueError):
            self.mgr.start(self.tid)  # running

    def test_set_duration_rejected_while_running(self):
        self.mgr.start(self.tid)
        with self.assertRaises(ValueError):
            self.mgr.set_duration(self.tid, 0, 0, 0, 2, 0)

    def test_set_duration_updates_and_remembers(self):
        self.mgr.set_duration(self.tid, 0, 0, 1, 23, 45)
        self.assertEqual(self.mgr.remaining_of(self.tid), 3600 + 23 * 60 + 45)
        self.assertEqual(self.mgr.get(self.tid).state, "idle")
        other = self.mgr.add()
        self.assertEqual(self.mgr.get(other).total, 3600 + 23 * 60 + 45)

    def test_rename(self):
        self.mgr.rename(self.tid, "Tea")
        self.assertEqual(self.mgr.get(self.tid).name, "Tea")
        self.mgr.rename(self.tid, "")
        self.assertEqual(self.mgr.get(self.tid).name, "Timer")

    # -- ticking -------------------------------------------------------------
    def test_tick_finishes_exactly_once(self):
        self.mgr.start(self.tid)
        self.clock.advance(59)
        self.assertEqual(self.mgr.tick(), [])
        self.clock.advance(2)
        self.assertEqual(self.mgr.tick(), [self.tid])
        self.assertEqual(self.mgr.get(self.tid).state, "finished")
        self.assertEqual(self.mgr.remaining_of(self.tid), 0.0)
        self.assertEqual(self.finished, [self.tid])
        # no second fire, ever
        self.assertEqual(self.mgr.tick(), [])
        self.clock.advance(1000)
        self.assertEqual(self.mgr.tick(), [])
        self.assertEqual(self.finished, [self.tid])

    def test_independent_timers(self):
        tid2 = self.mgr.add(minutes=2)
        self.mgr.start(self.tid)
        self.mgr.start(tid2)
        self.clock.advance(61)
        finished = self.mgr.tick()
        self.assertEqual(finished, [self.tid])
        self.assertEqual(self.mgr.get(self.tid).state, "finished")
        self.assertEqual(self.mgr.get(tid2).state, "running")
        self.assertEqual(self.mgr.remaining_of(tid2), 59.0)
        # finishing the second one does not touch the first
        self.clock.advance(59)
        self.assertEqual(self.mgr.tick(), [tid2])
        self.assertEqual(self.mgr.get(self.tid).state, "finished")

    def test_restart_from_finished_uses_full_duration(self):
        self.mgr.start(self.tid)
        self.clock.advance(61)
        self.mgr.tick()
        self.mgr.start(self.tid)
        self.assertEqual(self.mgr.get(self.tid).state, "running")
        self.assertEqual(self.mgr.remaining_of(self.tid), 60.0)

    # -- triggers ---------------------------------------------------------------
    def test_clicks_trigger_counts_and_disarms(self):
        self.mgr.set_trigger(self.tid, TRIGGER_CLICKS, count=3)
        self.assertFalse(self.mgr.trigger_action(self.tid, TRIGGER_CLICKS))
        self.assertFalse(self.mgr.trigger_action(self.tid, TRIGGER_CLICKS))
        self.assertTrue(self.mgr.trigger_action(self.tid, TRIGGER_CLICKS))
        self.assertEqual(self.mgr.get(self.tid).state, "running")
        self.assertEqual(
            self.mgr.get(self.tid).trigger_kind, TRIGGER_MANUAL
        )
        # disarmed: further clicks do nothing
        self.assertFalse(self.mgr.trigger_action(self.tid, TRIGGER_CLICKS))

    def test_clicks_ignored_while_running_or_wrong_kind(self):
        self.mgr.start(self.tid)
        self.mgr.set_trigger(self.tid, TRIGGER_CLICKS, count=1)
        self.assertFalse(self.mgr.trigger_action(self.tid, TRIGGER_CLICKS))
        # a keys trigger does not react to clicks
        self.mgr.reset(self.tid)
        self.mgr.set_trigger(self.tid, TRIGGER_KEYS, count=1, key="space")
        self.assertFalse(self.mgr.trigger_action(self.tid, TRIGGER_CLICKS))
        self.assertTrue(self.mgr.trigger_action(self.tid, TRIGGER_KEYS))
        self.assertEqual(self.mgr.get(self.tid).state, "running")

    def test_at_trigger_autostarts_on_tick(self):
        self.mgr.set_trigger(self.tid, TRIGGER_AT, at=self.clock.now + 30)
        self.clock.advance(20)
        self.mgr.tick()
        self.assertEqual(self.mgr.get(self.tid).state, "idle")
        self.clock.advance(11)
        self.mgr.tick()
        self.assertEqual(self.mgr.get(self.tid).state, "running")
        self.assertEqual(
            self.mgr.get(self.tid).trigger_kind, TRIGGER_MANUAL
        )  # disarmed

    def test_at_trigger_ignores_paused_timer(self):
        self.mgr.start(self.tid)
        self.mgr.pause(self.tid)
        self.mgr.set_trigger(self.tid, TRIGGER_AT, at=self.clock.now + 5)
        self.clock.advance(10)
        self.mgr.tick()
        self.assertEqual(self.mgr.get(self.tid).state, "paused")

    def test_set_trigger_validation(self):
        with self.assertRaises(ValueError):
            self.mgr.set_trigger(self.tid, "bogus")
        with self.assertRaises(ValueError):
            self.mgr.set_trigger(self.tid, TRIGGER_CLICKS, count=0)
        with self.assertRaises(ValueError):
            self.mgr.set_trigger(self.tid, TRIGGER_CLICKS, count=True)

    # -- persistence ----------------------------------------------------------------
    def test_roundtrip_idle(self):
        data = self.mgr.to_dict()
        mgr2 = TimerManager(clock=self.clock)
        self.assertEqual(mgr2.load(data), [])
        entry = mgr2.get(self.tid)
        self.assertEqual(entry.name, self.mgr.get(self.tid).name)
        self.assertEqual(entry.remaining, 60.0)
        self.assertEqual(entry.state, "idle")

    def test_roundtrip_running_deadline_in_future(self):
        self.mgr.start(self.tid)
        data = self.mgr.to_dict()
        mgr2 = TimerManager(clock=self.clock)
        self.assertEqual(mgr2.load(data), [])
        self.assertEqual(mgr2.get(self.tid).state, "running")
        self.assertEqual(mgr2.remaining_of(self.tid), 60.0)

    def test_roundtrip_running_deadline_in_past_is_missed(self):
        self.mgr.start(self.tid)
        data = self.mgr.to_dict()
        self.clock.advance(120)  # the machine was off
        mgr2 = TimerManager(clock=self.clock)
        self.assertEqual(mgr2.load(data), [self.tid])
        entry = mgr2.get(self.tid)
        self.assertEqual(entry.state, "finished")
        self.assertTrue(entry.missed)
        self.assertEqual(entry.remaining, 0.0)

    def test_load_skips_garbage(self):
        mgr2 = TimerManager(clock=self.clock)
        self.assertEqual(mgr2.load(None), [])
        self.assertEqual(
            mgr2.load(
                {"timers": [{"name": "x"}, "junk", {"id": 5}, {"id": "ok",
                                                             "minutes": 1}]}
            ),
            [],
        )
        self.assertEqual(len(mgr2), 1)  # only the well-formed one survived
        self.assertEqual(mgr2.get("ok").minutes, 1)

    def test_last_duration_restored(self):
        self.mgr.add(hours=2)
        mgr2 = TimerManager(clock=self.clock)
        mgr2.load(self.mgr.to_dict())
        tid = mgr2.add()
        self.assertEqual(mgr2.get(tid).hours, 2)

    def test_to_dict_is_json_friendly(self):
        import json

        self.mgr.start(self.tid)
        self.mgr.set_trigger(self.tid, TRIGGER_CLICKS, count=4)
        payload = json.dumps(self.mgr.to_dict())
        self.assertIsInstance(payload, str)


class TestTimerEntry(unittest.TestCase):
    def test_to_from_dict_roundtrip(self):
        entry = TimerEntry(id="abc", name="tea", minutes=3)
        entry.remaining = 180.0
        clone = TimerEntry.from_dict(entry.to_dict())
        self.assertEqual(clone.to_dict(), entry.to_dict())

    def test_from_dict_ignores_unknown_and_none(self):
        data = TimerEntry(id="abc", minutes=1).to_dict()
        data["unknown_key"] = 123
        data["missed"] = None
        clone = TimerEntry.from_dict(data)
        self.assertEqual(clone.minutes, 1)
        self.assertFalse(clone.missed)

    def test_total_zero_when_all_fields_zero(self):
        entry = TimerEntry(id="z", minutes=0, seconds=0, hours=0,
                           days=0, weeks=0)
        self.assertEqual(entry.total, 0)


class TestDurationSeconds(unittest.TestCase):
    def test_values(self):
        self.assertEqual(duration_seconds(0, 0, 0, 0, 45), 45)
        self.assertEqual(
            duration_seconds(1, 2, 3, 4, 5),
            604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5,
        )

    def test_validation(self):
        for bad in (
            lambda: duration_seconds(0, 0, 0, 0, 0),  # zero total
            lambda: duration_seconds(0, 0, 0.5, 0, 0),  # float
            lambda: duration_seconds(0, 0, 0, 60, 0),  # minutes range
            lambda: duration_seconds(0, 0, 0, 0, -1),  # negative
            lambda: duration_seconds(True, 0, 0, 0, 0),  # bool
        ):
            with self.assertRaises(ValueError):
                bad()


class TestPresetStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name))
        self.presets = PresetStore(self.storage)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_and_all(self):
        preset = self.presets.add("13 min", minutes=13)
        self.assertEqual(self.presets.all(), [preset])
        # a fresh store over the same storage sees the same preset
        again = PresetStore(self.storage)
        self.assertEqual(again.all(), [preset])

    def test_add_validation(self):
        with self.assertRaises(ValueError):
            self.presets.add("   ")
        with self.assertRaises(ValueError):
            self.presets.add("zero", minutes=0)

    def test_remove(self):
        preset = self.presets.add("a")
        self.presets.remove(preset["id"])
        self.assertEqual(self.presets.all(), [])
        # removing again is a no-op
        self.presets.remove(preset["id"])

    def test_upsert_replaces_same_label(self):
        first = self.presets.upsert("Tea", minutes=5)
        self.assertEqual(len(self.presets.all()), 1)
        updated = self.presets.upsert("Tea", minutes=10)
        items = self.presets.all()
        self.assertEqual(len(items), 1)  # still one preset
        self.assertEqual(items[0]["id"], first["id"])  # same preset
        self.assertEqual(items[0]["minutes"], 10)  # duration updated

    def test_upsert_label_case_insensitive(self):
        self.presets.upsert("Tea", minutes=5)
        updated = self.presets.upsert("tea", minutes=7)
        items = self.presets.all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["minutes"], 7)

    def test_upsert_different_label_appends(self):
        self.presets.upsert("Tea", minutes=5)
        second = self.presets.upsert("Eggs", minutes=3)
        self.assertEqual(len(self.presets.all()), 2)
        self.assertEqual(second["label"], "Eggs")

    def test_upsert_validation(self):
        with self.assertRaises(ValueError):
            self.presets.upsert("   ")
        with self.assertRaises(ValueError):
            self.presets.upsert("zero", minutes=0)
        # a failed upsert changes nothing
        self.assertEqual(self.presets.all(), [])

    def test_corrupt_storage_degrades_to_empty(self):
        self.storage.save(PresetStore.KEY, "not a list")
        self.assertEqual(self.presets.all(), [])


class TestFormatTimer(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_timer(0), "00:00:00")

    def test_small_values(self):
        self.assertEqual(format_timer(1), "00:00:01")
        self.assertEqual(format_timer(59), "00:00:59")

    def test_rollover(self):
        self.assertEqual(format_timer(60), "00:01:00")
        self.assertEqual(format_timer(3661), "01:01:01")

    def test_fraction_rounds_up(self):
        self.assertEqual(format_timer(0.4), "00:00:01")
        self.assertEqual(format_timer(59.1), "00:01:00")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(format_timer(-3), "00:00:00")


if __name__ == "__main__":
    unittest.main()
