"""Headless tests for the calendar module (EventStore model, no GTK).

Run with:  python3 tests/test_calendar.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core.storage import Storage  # noqa: E402
from omnify.modules.calendar import (  # noqa: E402
    CalendarHistory,
    CalendarSettings,
    CategoryStore,
    EventStore,
    GoalStore,
    TemplateStore,
    due_reminders,
    filter_events,
    format_event_line,
    layer_css,
    normalize_clock_text,
    normalize_layer_color,
    normalize_layer_gradient,
    occurs_on,
    rgba_to_hex,
    week_start,
)


class TestEventStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(root=self._tmp.name)
        self.store = EventStore(self.storage)

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_empty(self):
        self.assertEqual(self.store.all_events(), [])
        self.assertEqual(self.store.get_for_date("2026-11-30"), [])

    def test_add_and_get_for_date(self):
        ev = self.store.add("2026-11-30", "14:30", "Meeting", "bring laptop")
        self.assertIsInstance(ev["id"], str)
        found = self.store.get_for_date("2026-11-30")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Meeting")
        self.assertEqual(found[0]["time"], "14:30")
        self.assertEqual(self.store.get_for_date("2026-11-29"), [])

    def test_add_requires_title(self):
        with self.assertRaises(ValueError):
            self.store.add("2026-11-30", None, "  ", "")
        with self.assertRaises(ValueError):
            self.store.add("bad-date", None, "x", "")

    def test_update(self):
        ev = self.store.add("2026-11-30", None, "Old", "")
        updated = self.store.update(ev["id"], title="New", date="2026-12-01")
        self.assertEqual(updated["title"], "New")
        self.assertEqual(updated["date"], "2026-12-01")
        self.assertEqual(self.store.get_for_date("2026-12-01")[0]["id"], ev["id"])
        self.assertEqual(self.store.get_for_date("2026-11-30"), [])

    def test_update_missing_id(self):
        with self.assertRaises(KeyError):
            self.store.update("nope", title="x")

    def test_remove(self):
        ev = self.store.add("2026-11-30", None, "X", "")
        self.store.remove(ev["id"])
        self.assertEqual(self.store.get_for_date("2026-11-30"), [])

    def test_remove_missing_id(self):
        with self.assertRaises(KeyError):
            self.store.remove("nope")

    def test_persistence_roundtrip(self):
        self.store.add("2026-11-30", "09:00", "Persist me", "")
        reloaded = EventStore(self.storage)
        events = reloaded.get_for_date("2026-11-30")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Persist me")

    def test_corrupt_data_falls_back_to_empty(self):
        (self.storage.path / "calendar.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.all_events(), [])

    def test_events_sorted_by_time(self):
        self.store.add("2026-11-30", "14:00", "late", "")
        self.store.add("2026-11-30", "09:00", "early", "")
        self.store.add("2026-11-30", None, "all day", "")
        titles = [e["title"] for e in self.store.get_for_date("2026-11-30")]
        self.assertEqual(titles, ["all day", "early", "late"])


class TestFormatEventLine(unittest.TestCase):
    def test_all_day(self):
        self.assertEqual(format_event_line({"time": None, "title": "X"}), "X")

    def test_with_time(self):
        self.assertEqual(
            format_event_line({"time": "09:30", "title": "Standup"}), "09:30  Standup"
        )


class CalendarModelTest(unittest.TestCase):
    """Shared fixture: a temporary Storage and an EventStore."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(root=self._tmp.name)
        self.store = EventStore(self.storage)

    def tearDown(self):
        self._tmp.cleanup()


class TestRecurrence(CalendarModelTest):
    def test_daily_interval(self):
        event = self.store.add(
            "2026-11-30", "09:00", "Standup",
            recurrence={"freq": "daily", "interval": 2},
        )
        self.assertTrue(occurs_on(event, date(2026, 11, 30)))
        self.assertFalse(occurs_on(event, date(2026, 12, 1)))
        self.assertTrue(occurs_on(event, date(2026, 12, 2)))
        self.assertFalse(occurs_on(event, date(2026, 11, 29)))

    def test_weekly_weekdays_and_interval(self):
        event = self.store.add(
            "2026-11-30", "18:00", "Gym",
            recurrence={"freq": "weekly", "interval": 2, "weekdays": [0, 2]},
        )
        self.assertTrue(occurs_on(event, date(2026, 11, 30)))  # Monday
        self.assertTrue(occurs_on(event, date(2026, 12, 2)))   # Wednesday
        self.assertFalse(occurs_on(event, date(2026, 12, 7)))  # off week
        self.assertTrue(occurs_on(event, date(2026, 12, 14)))
        self.assertFalse(occurs_on(event, date(2026, 12, 1)))  # Tuesday

    def test_until_date(self):
        event = self.store.add(
            "2026-11-30", None, "Advent",
            recurrence={
                "freq": "daily", "interval": 1, "until": "2026-12-02",
            },
        )
        self.assertTrue(occurs_on(event, date(2026, 12, 2)))
        self.assertFalse(occurs_on(event, date(2026, 12, 3)))

    def test_monthly_clamps_short_months(self):
        event = self.store.add(
            "2026-01-31", None, "Rent",
            recurrence={"freq": "monthly", "interval": 1},
        )
        self.assertTrue(occurs_on(event, date(2026, 2, 28)))
        self.assertFalse(occurs_on(event, date(2026, 2, 27)))
        self.assertTrue(occurs_on(event, date(2026, 3, 31)))
        self.assertTrue(occurs_on(event, date(2026, 4, 30)))

    def test_monthly_nth_weekday(self):
        event = self.store.add(
            "2026-11-10", None, "Retro",
            recurrence={
                "freq": "monthly", "interval": 1,
                "weekday": 1, "nth": 2,
            },
        )
        self.assertTrue(occurs_on(event, date(2026, 11, 10)))  # 2nd Tue
        self.assertFalse(occurs_on(event, date(2026, 11, 3)))
        self.assertTrue(occurs_on(event, date(2026, 12, 8)))

    def test_yearly_leap_day(self):
        event = self.store.add(
            "2024-02-29", None, "Birthday",
            recurrence={"freq": "yearly", "interval": 1},
        )
        self.assertTrue(occurs_on(event, date(2025, 2, 28)))
        self.assertTrue(occurs_on(event, date(2026, 2, 28)))
        self.assertTrue(occurs_on(event, date(2028, 2, 29)))

    def test_invalid_frequency_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add(
                "2026-11-30", None, "X", recurrence={"freq": "hourly"}
            )

    def test_week_start_is_monday(self):
        self.assertEqual(week_start(date(2026, 11, 30)), date(2026, 11, 30))
        self.assertEqual(week_start(date(2026, 12, 2)), date(2026, 11, 30))


class TestDeleteScopes(CalendarModelTest):
    def test_cancel_only_one_occurrence(self):
        event = self.store.add(
            "2026-11-30", "09:00", "Standup",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.store.cancel_occurrence(event["id"], "2026-12-01")
        self.assertEqual(len(self.store.day_events("2026-11-30")), 1)
        self.assertEqual(self.store.day_events("2026-12-01"), [])
        self.assertEqual(len(self.store.day_events("2026-12-02")), 1)
        self.assertEqual(
            self.store.get(event["id"])["cancelled"], ["2026-12-01"]
        )

    def test_cancel_is_idempotent(self):
        event = self.store.add(
            "2026-11-30", None, "Daily",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.store.cancel_occurrence(event["id"], "2026-12-01")
        self.store.cancel_occurrence(event["id"], "2026-12-01")
        self.assertEqual(
            self.store.get(event["id"])["cancelled"], ["2026-12-01"]
        )

    def test_truncate_keeps_earlier_occurrences(self):
        event = self.store.add(
            "2026-11-30", "09:00", "Standup",
            recurrence={"freq": "daily", "interval": 1},
        )
        kept = self.store.truncate_series(event["id"], "2026-12-03")
        self.assertTrue(kept)
        stored = self.store.get(event["id"])
        self.assertEqual(stored["recurrence"]["until"], "2026-12-02")
        self.assertEqual(len(self.store.day_events("2026-12-02")), 1)
        self.assertEqual(self.store.day_events("2026-12-03"), [])

    def test_truncate_at_start_removes_whole_series(self):
        event = self.store.add(
            "2026-11-30", "09:00", "Standup",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.assertFalse(
            self.store.truncate_series(event["id"], "2026-11-30")
        )
        with self.assertRaises(KeyError):
            self.store.get(event["id"])

    def test_truncate_non_recurring_removes_event(self):
        event = self.store.add("2026-11-30", "09:00", "One-off")
        self.assertFalse(
            self.store.truncate_series(event["id"], "2026-11-30")
        )
        self.assertEqual(self.store.all_events(), [])

    def test_delete_scopes_reject_invalid_dates(self):
        event = self.store.add(
            "2026-11-30", None, "Daily",
            recurrence={"freq": "daily", "interval": 1},
        )
        with self.assertRaises(ValueError):
            self.store.cancel_occurrence(event["id"], "soon")
        with self.assertRaises(ValueError):
            self.store.truncate_series(event["id"], "soon")


class TestClockText(unittest.TestCase):
    def test_normalizes_typed_times(self):
        self.assertEqual(normalize_clock_text("8:43"), "08:43")
        self.assertEqual(normalize_clock_text("08:43"), "08:43")
        self.assertEqual(normalize_clock_text("8:4"), "08:04")
        self.assertEqual(normalize_clock_text("0:00"), "00:00")
        self.assertEqual(normalize_clock_text("23:59"), "23:59")

    def test_rejects_invalid_times(self):
        for value in ("24:00", "12:60", "-1:10", "abc", "12", ""):
            with self.assertRaises(ValueError, msg=value):
                normalize_clock_text(value)


class TestOccurrences(CalendarModelTest):
    def test_expand_and_sort(self):
        self.store.add(
            "2026-11-30", "14:00", "Late",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.store.add("2026-11-30", None, "All day")
        self.store.add(
            "2026-11-30", "09:00", "Early",
            recurrence={"freq": "daily", "interval": 1},
        )
        titles = [
            (event["occurrence_date"], event["title"])
            for event in self.store.occurrences_between(
                "2026-11-30", "2026-12-01"
            )
        ]
        self.assertEqual(
            titles,
            [
                ("2026-11-30", "All day"),
                ("2026-11-30", "Early"),
                ("2026-11-30", "Late"),
                ("2026-12-01", "Early"),
                ("2026-12-01", "Late"),
            ],
        )

    def test_layer_filter_and_cancelled_day(self):
        work = self.store.add(
            "2026-11-30", "10:00", "Work", category="work",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.store.add(
            "2026-11-30", "11:00", "Private", category="personal",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.store.update(work["id"], cancelled=["2026-12-01"])
        day = self.store.occurrences_between(
            "2026-12-01", "2026-12-01", visible_categories={"work"}
        )
        self.assertEqual(day, [])
        day = self.store.occurrences_between(
            "2026-12-01", "2026-12-01", visible_categories={"personal"}
        )
        self.assertEqual([event["title"] for event in day], ["Private"])

    def test_daily_counts(self):
        self.store.add(
            "2026-11-30", "10:00", "A",
            recurrence={"freq": "daily", "interval": 1},
        )
        self.store.add(
            "2026-11-30", "11:00", "B",
            recurrence={"freq": "daily", "interval": 1},
        )
        counts = self.store.daily_counts("2026-11-30", "2026-12-02")
        self.assertEqual(
            counts,
            [("2026-11-30", 2), ("2026-12-01", 2), ("2026-12-02", 2)],
        )
        categories = self.store.category_counts("2026-11-30", "2026-11-30")
        self.assertEqual(categories, {"": 2})


class TestHabits(CalendarModelTest):
    def test_habit_defaults_to_daily(self):
        habit = self.store.add("2026-11-30", None, "Brush teeth", habit=True)
        self.assertTrue(habit["habit"])
        self.assertEqual(habit["recurrence"], {"freq": "daily", "interval": 1})

    def test_toggle_and_stats(self):
        habit = self.store.add("2025-01-01", None, "Read", habit=True)
        self.assertTrue(self.store.toggle_done(habit["id"], "2025-01-01"))
        self.assertFalse(self.store.toggle_done(habit["id"], "2025-01-01"))
        self.store.set_done(habit["id"], "2025-01-01", True)
        self.store.set_done(habit["id"], "2025-01-03", True)
        stats = self.store.habit_stats(
            habit["id"], "2025-01-01", "2025-01-04"
        )
        self.assertEqual(stats["done"], 2)
        self.assertEqual(stats["expected"], 4)
        self.assertEqual(stats["missed"], 2)
        self.assertEqual(stats["percent"], 50)

    def test_month_summary_excludes_future_misses(self):
        habit = self.store.add("2026-11-30", None, "Floss", habit=True)
        self.store.set_done(habit["id"], "2026-11-30", True)
        summary = self.store.stats_between("2026-11-30", "2026-12-02")
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["done"], 1)
        self.assertEqual(summary["missed"], 0)

    def test_habit_stats_unknown_id(self):
        stats = self.store.habit_stats("nope", "2025-01-01", "2025-01-02")
        self.assertEqual(stats["expected"], 0)


class TestCategoriesAndGoals(CalendarModelTest):
    def test_default_layers_and_visibility(self):
        categories = CategoryStore(self.storage)
        self.assertEqual(len(categories.all()), 5)
        categories.ensure()
        categories.set_visible("work", False)
        self.assertNotIn("work", categories.visible_ids())
        categories.set_visible("work", True)
        self.assertIn("work", categories.visible_ids())

    def test_add_layer(self):
        categories = CategoryStore(self.storage)
        category = categories.add("Sport", "red")
        self.assertEqual(category["name"], "Sport")
        self.assertEqual(len(categories.all()), 6)

    def test_update_layer_name_and_colour(self):
        categories = CategoryStore(self.storage)
        categories.ensure()
        categories.update("work", name="Deep work", color="purple")
        reloaded = CategoryStore(self.storage).get("work")
        self.assertEqual(reloaded["name"], "Deep work")
        self.assertEqual(reloaded["color"], "purple")

    def test_layer_update_validates_fields(self):
        categories = CategoryStore(self.storage)
        categories.ensure()
        with self.assertRaises(ValueError):
            categories.update("work", name="   ")
        with self.assertRaises(ValueError):
            categories.update("work", colour="red")
        with self.assertRaises(KeyError):
            categories.update("missing", name="Nope")

    def test_deleting_every_layer_leaves_an_empty_store(self):
        categories = CategoryStore(self.storage)
        for category in list(categories.all()):
            categories.remove(category["id"])
        self.assertEqual(categories.all(), [])
        # Adding after a full wipe must not resurrect the defaults.
        categories.add("Only layer", "green")
        self.assertEqual([c["name"] for c in categories.all()], ["Only layer"])

    def test_goal_progress_from_items(self):
        goals = GoalStore(self.storage)
        goal = goals.add("Read 12 books", target=12, unit="books")
        done, total, percent = GoalStore.progress(goal)
        self.assertEqual((done, total, percent), (0, 12, 0))
        goals.add_item(goal["id"], "Book 1")
        goals.add_item(goal["id"], "Book 2")
        goals.add_item(goal["id"], "Book 3")
        goals.toggle_item(goal["id"], 0)
        goals.toggle_item(goal["id"], 2)
        reloaded = GoalStore(self.storage).get(goal["id"])
        done, total, percent = GoalStore.progress(reloaded)
        self.assertEqual((done, total, percent), (2, 3, 67))

    def test_goal_numeric_current(self):
        goals = GoalStore(self.storage)
        goal = goals.add("Run", target=10, unit="km")
        goals.update(goal["id"], current=4)
        done, total, percent = GoalStore.progress(goals.get(goal["id"]))
        self.assertEqual((done, total, percent), (4, 10, 40))

    def test_goal_deadline_is_validated(self):
        goals = GoalStore(self.storage)
        with self.assertRaises(ValueError):
            goals.add("Bad deadline", deadline="31.12.2026")


class TestCalendarQoL(unittest.TestCase):
    """Settings, templates, search, reminders and undo history."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(root=self._tmp.name)
        self.store = EventStore(self.storage)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reminder_is_persisted_and_validated(self):
        event = self.store.add(
            "2026-11-30", "10:00", "Standup", reminder=15
        )
        self.assertEqual(self.store.get(event["id"])["reminder"], 15)
        self.store.update(event["id"], reminder=0)
        self.assertEqual(self.store.get(event["id"])["reminder"], 0)
        with self.assertRaises(ValueError):
            self.store.add("2026-11-30", "10:00", "Bad", reminder=-1)

    def test_due_reminders_window(self):
        ten = self.store.add("2026-11-30", "10:00", "Ten", reminder=10)
        off = self.store.add("2026-11-30", "10:00", "Off", reminder=0)
        later = self.store.add("2026-11-30", "10:30", "Later")
        allday = self.store.add("2026-11-30", None, "All day")
        due = due_reminders(
            self.store, datetime(2026, 11, 30, 9, 55), default_lead=10
        )
        ids = [entry[0] for entry in due]
        self.assertIn(ten["id"], ids)
        self.assertNotIn(off["id"], ids)
        self.assertNotIn(later["id"], ids)
        self.assertNotIn(allday["id"], ids)
        self.assertEqual(due[0][3], 5)

    def test_settings_validate_and_persist(self):
        settings = CalendarSettings(self.storage)
        self.assertEqual(settings.all()["week_start"], 0)
        self.assertEqual(settings.all()["reminder_lead"], 30)
        updated = settings.update(
            week_start=6, day_start_hour=6, day_end_hour=24, hour_height=72
        )
        self.assertEqual(updated["week_start"], 6)
        self.assertEqual(CalendarSettings(self.storage).all()["hour_height"], 72)
        self.assertEqual(
            settings.update(reminder_lead=83)["reminder_lead"], 83
        )
        with self.assertRaises(ValueError):
            settings.update(reminder_lead=0)
        with self.assertRaises(ValueError):
            settings.update(week_start=3)
        with self.assertRaises(ValueError):
            settings.update(day_start_hour=19, day_end_hour=20)
        with self.assertRaises(ValueError):
            settings.update(hour_height=999)
        with self.assertRaises(ValueError):
            settings.update(unknown=1)

    def test_custom_layer_colours_and_gradients(self):
        categories = CategoryStore(self.storage)
        layer = categories.add(
            "Focus",
            "#A1B2C3",
            gradient=["#112233", "teal"],
            animated=True,
        )
        self.assertEqual(layer["color"], "#a1b2c3")
        self.assertEqual(layer["gradient"], ["#112233", "#2fb6c4"])
        self.assertTrue(layer["animated"])
        reloaded = categories.get(layer["id"])
        self.assertEqual(reloaded["color"], "#a1b2c3")
        self.assertEqual(
            categories.update(layer["id"], color="orange")["color"],
            "orange",
        )
        cleared = categories.update(
            layer["id"], gradient=None, animated=True
        )
        self.assertIsNone(cleared["gradient"])
        self.assertFalse(cleared["animated"])
        with self.assertRaises(ValueError):
            categories.update(layer["id"], gradient=["only-one"])

    def test_layer_css_builds_static_and_animated_gradients(self):
        static = layer_css(
            {
                "id": "custom-1",
                "color": "#a1b2c3",
                "gradient": ["#112233", "#445566"],
                "animated": False,
            }
        )
        self.assertIn("linear-gradient(135deg", static)
        self.assertIn(".omnify-layer.omnify-pill-custom-1", static)
        solid = layer_css({"id": "solid-1", "color": "#a1b2c3"})
        self.assertIn("background-color:", solid)
        self.assertNotIn("background-image:", solid)
        animated = layer_css(
            {
                "id": "custom-1",
                "color": "blue",
                "gradient": ["#112233", "#445566"],
                "animated": True,
            },
            phase=90,
        )
        self.assertIn("linear-gradient(90deg", animated)
        self.assertEqual(rgba_to_hex(1.0, 0.2, 0.0), "#ff3300")
        self.assertEqual(normalize_layer_color("#ABCDEF"), "#abcdef")
        self.assertEqual(normalize_layer_color("nope"), "blue")
        self.assertEqual(
            normalize_layer_gradient(["blue", "#AABBCC"]),
            ["#4f8cff", "#aabbcc"],
        )
        self.assertIsNone(normalize_layer_gradient(["#123456"]))

    def test_templates_add_use_and_remove(self):
        templates = TemplateStore(self.storage)
        template = templates.add({
            "title": "Deep work",
            "time": "09:00",
            "end_time": "11:00",
            "category": "work",
            "actions": [{"kind": "stopwatch"}],
        })
        self.assertEqual(templates.all()[0]["title"], "Deep work")
        self.assertEqual(templates.all()[0]["actions"][0]["kind"], "stopwatch")
        with self.assertRaises(ValueError):
            templates.add({"title": "   "})
        with self.assertRaises(ValueError):
            templates.add({"title": "Bad", "time": "25:00"})
        templates.remove(template["id"])
        self.assertEqual(templates.all(), [])
        with self.assertRaises(KeyError):
            templates.remove("missing")

    def test_filter_events(self):
        a = self.store.add("2026-11-30", "10:00", "Team sync", "zoom", category="work")
        b = self.store.add("2026-12-01", "18:00", "Gym", "", category="health")
        c = self.store.add(
            "2026-12-02", "09:00", "Weekly review", "",
            category="work", recurrence={"freq": "weekly", "interval": 1},
        )
        today = date(2026, 12, 1)
        self.assertEqual(
            [e["id"] for e in filter_events([a, b, c], query="team", today=today)],
            [a["id"]],
        )
        self.assertEqual(
            [e["id"] for e in filter_events([a, b, c], category="health", today=today)],
            [b["id"]],
        )
        self.assertEqual(
            [e["id"] for e in filter_events([a, b, c], scope="upcoming", today=today)],
            [b["id"], c["id"]],
        )
        self.assertEqual(
            [e["id"] for e in filter_events([a, b, c], scope="recurring", today=today)],
            [c["id"]],
        )

    def test_history_undo_redo_and_limit(self):
        history = CalendarHistory(limit=2)
        history.record("first", {"calendar": []}, {"calendar": [1]})
        history.record("second", {"calendar": [1]}, {"calendar": [1, 2]})
        self.assertTrue(history.can_undo)
        self.assertEqual(history.undo_label, "second")
        state, label = history.undo()
        self.assertEqual((state, label), ({"calendar": [1]}, "second"))
        state, label = history.redo()
        self.assertEqual((state, label), ({"calendar": [1, 2]}, "second"))
        history.record("third", {"calendar": [1, 2]}, {"calendar": [1, 2, 3]})
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo()[1], "third")
        state, label = history.undo()
        self.assertEqual((state, label), ({"calendar": [1]}, "second"))
        self.assertFalse(history.can_undo)
        self.assertIsNone(history.undo())


if __name__ == "__main__":
    unittest.main()
