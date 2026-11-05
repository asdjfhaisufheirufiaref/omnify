"""Headless tests for the alarm clock model."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.alarm_engine import (  # noqa: E402
    ALL_DAYS,
    Alarm,
    AlarmClock,
    format_days,
    format_time,
    parse_days,
    parse_time,
)


class FixedClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class TestParsingAndFormatting(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("07:05"), (7, 5))
        self.assertEqual(parse_time("7:5"), (7, 5))
        self.assertEqual(parse_time("730"), (7, 30))
        self.assertEqual(parse_time("0730"), (7, 30))
        self.assertIsNone(parse_time("24:00"))
        self.assertIsNone(parse_time("12:60"))
        self.assertIsNone(parse_time(""))
        self.assertIsNone(parse_time("noon"))

    def test_format_time_and_days(self):
        self.assertEqual(format_time(7, 5), "07:05")
        self.assertEqual(format_days(set()), "Once")
        self.assertEqual(format_days(set(range(7))), "Every day")
        self.assertEqual(format_days({0, 1, 2, 3, 4}), "Weekdays")
        self.assertEqual(format_days({5, 6}), "Weekend")
        self.assertEqual(format_days({0, 2, 4}), "Mon, Wed, Fri")

    def test_parse_days_filters_invalid(self):
        self.assertEqual(parse_days([0, 6, 7, -1, "2", "bad"]), {0, 2, 6})


class TestAlarmClock(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock(datetime(2026, 10, 25, 6, 59))  # Sunday
        self.alarms = AlarmClock(self.clock)

    def test_add_update_remove(self):
        alarm = self.alarms.add("Wake", 7, 30, days=[0, 1, 2, 3, 4])
        self.assertEqual(alarm.time_text, "07:30")
        self.assertEqual(alarm.days_text, "Weekdays")
        changed = self.alarms.update(alarm.id, label="Work", hour=8, minute=0)
        self.assertEqual(changed.label, "Work")
        self.assertEqual(changed.time_text, "08:00")
        self.assertTrue(self.alarms.remove(alarm.id))
        self.assertFalse(self.alarms.remove(alarm.id))
        self.assertIsNone(self.alarms.get(alarm.id))

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.alarms.add("Bad", 24, 0)
        with self.assertRaises(ValueError):
            self.alarms.add("Bad", 7, 60)
        with self.assertRaises(ValueError):
            Alarm(hour=7, minute=0, snooze_minutes=0).validate()
        with self.assertRaises(ValueError):
            Alarm(hour=7, minute=0, days={7}).validate()

    def test_due_before_and_at_time(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.assertEqual(self.alarms.due(), [])
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        due = self.alarms.due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].id, alarm.id)
        # Side-effect free: still due until the UI acknowledges it.
        self.assertEqual(len(self.alarms.due()), 1)

    def test_daily_alarm_does_not_repeat_after_mark(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        self.alarms.mark_fired(alarm.id)
        self.assertEqual(self.alarms.due(), [])
        nxt = self.alarms.next_occurrence(alarm)
        self.assertEqual(nxt, datetime(2026, 10, 26, 7, 0))

    def test_one_shot_disables_after_mark(self):
        alarm = self.alarms.add("Once", 7, 0)
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        self.assertEqual(len(self.alarms.due()), 1)
        self.alarms.mark_fired(alarm.id)
        self.assertFalse(alarm.enabled)
        self.assertIsNone(self.alarms.next_occurrence(alarm))

    def test_weekday_filter(self):
        self.alarms.add("Weekday", 7, 0, days={0, 1, 2, 3, 4})
        self.clock.value = datetime(2026, 10, 25, 7, 0)  # Sunday
        self.assertEqual(self.alarms.due(), [])
        self.clock.value = datetime(2026, 10, 26, 7, 0)  # Monday
        self.assertEqual(len(self.alarms.due()), 1)

    def test_disabled_alarm_does_not_ring(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.alarms.set_enabled(alarm.id, False)
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        self.assertEqual(self.alarms.due(), [])
        self.alarms.set_enabled(alarm.id, True)
        self.assertEqual(len(self.alarms.due()), 1)

    def test_snooze_and_ring_again(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        self.alarms.snooze(alarm.id, 5)
        self.assertEqual(self.alarms.due(), [])
        self.clock.value = datetime(2026, 10, 25, 7, 4, 59)
        self.assertEqual(self.alarms.due(), [])
        self.clock.value = datetime(2026, 10, 25, 7, 5)
        self.assertEqual(len(self.alarms.due()), 1)
        self.alarms.mark_fired(alarm.id)
        self.assertEqual(self.alarms.due(), [])

    def test_snooze_clamps_to_one_hour(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.alarms.snooze(alarm.id, 100)
        self.assertEqual(alarm.snooze_minutes, 60)
        self.alarms.snooze(alarm.id, 0)
        self.assertEqual(alarm.snooze_minutes, 1)

    def test_next_occurrence_skips_past_today(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.clock.value = datetime(2026, 10, 25, 8, 0)
        self.assertEqual(
            self.alarms.next_occurrence(alarm),
            datetime(2026, 10, 26, 7, 0),
        )

    def test_next_alarm_chooses_soonest(self):
        self.alarms.add("Late", 9, 0, days=ALL_DAYS)
        early = self.alarms.add("Early", 7, 30, days=ALL_DAYS)
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        pair = self.alarms.next_alarm()
        self.assertIsNotNone(pair)
        self.assertEqual(pair[0].id, early.id)
        self.assertEqual(pair[1], datetime(2026, 10, 25, 7, 30))

    def test_update_schedule_resets_fired_state(self):
        alarm = self.alarms.add("Wake", 7, 0, days=ALL_DAYS)
        self.clock.value = datetime(2026, 10, 25, 7, 0)
        self.alarms.mark_fired(alarm.id)
        self.assertEqual(self.alarms.due(), [])
        self.clock.value = datetime(2026, 10, 25, 7, 2)
        self.alarms.update(alarm.id, minute=1)
        self.assertEqual(len(self.alarms.due()), 1)

    def test_persistence_roundtrip(self):
        self.alarms.add("Wake", 7, 5, days={0, 2, 4}, sound="/tmp/a.oga", snooze_minutes=9)
        payload = self.alarms.to_dict()
        restored = AlarmClock(self.clock)
        restored.load(payload)
        self.assertEqual(len(restored.all()), 1)
        alarm = restored.all()[0]
        self.assertEqual(alarm.label, "Wake")
        self.assertEqual(alarm.time_text, "07:05")
        self.assertEqual(alarm.days, {0, 2, 4})
        self.assertEqual(alarm.sound, "/tmp/a.oga")
        self.assertEqual(alarm.snooze_minutes, 9)

    def test_load_ignores_malformed_entries(self):
        self.alarms.load(
            {
                "alarms": [
                    {"label": "Good", "hour": 7, "minute": 0},
                    {"label": "Bad", "hour": 99, "minute": 0},
                    {"label": "Bad snooze", "hour": 7, "minute": 0, "snooze_minutes": "x"},
                    "not an object",
                ]
            }
        )
        # Invalid hour is dropped; invalid snooze gracefully falls back to 5.
        self.assertEqual(len(self.alarms.all()), 2)
        self.assertEqual(self.alarms.all()[0].label, "Good")
        self.assertEqual(self.alarms.all()[1].label, "Bad snooze")

    def test_enabled_count(self):
        self.alarms.add("One", 7, 0)
        second = self.alarms.add("Two", 8, 0)
        self.assertEqual(self.alarms.enabled_count(), 2)
        self.alarms.set_enabled(second.id, False)
        self.assertEqual(self.alarms.enabled_count(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
