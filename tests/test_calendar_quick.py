"""Headless tests for deterministic calendar quick add and meeting notes."""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.calendar_quick import (  # noqa: E402
    meeting_note_markdown,
    parse_quick_event,
)


class QuickAddTest(unittest.TestCase):
    """The parser uses a fixed "today" so the tests never depend on a clock."""

    TODAY = date(2026, 11, 17)  # Tuesday

    def parse(self, text):
        return parse_quick_event(text, today=self.TODAY)

    def test_relative_date_and_time(self):
        event = self.parse("Lunch with Ann tomorrow at 13:00")
        self.assertEqual(event.title, "Lunch with Ann")
        self.assertEqual(event.date_iso, "2026-11-18")
        self.assertEqual(event.time, "13:00")
        self.assertEqual(event.end_time, "14:00")
        self.assertFalse(event.all_day)

    def test_ukrainian_quick_add(self):
        event = self.parse("Зустріч завтра о 15:30")
        self.assertEqual(event.title, "Зустріч")
        self.assertEqual(event.date_iso, "2026-11-18")
        self.assertEqual(event.time, "15:30")
        self.assertEqual(event.end_time, "16:30")

    def test_weekday_and_duration(self):
        event = self.parse("Standup next monday 9am for 15 minutes")
        self.assertEqual(event.title, "Standup")
        self.assertEqual(event.date_iso, "2026-11-23")
        self.assertEqual(event.time, "09:00")
        self.assertEqual(event.end_time, "09:15")

    def test_weekday_always_moves_to_the_future(self):
        event = self.parse("Retro Friday 10:00")
        self.assertEqual(event.date_iso, "2026-11-20")
        self.assertEqual(event.time, "10:00")
        self.assertEqual(event.end_time, "11:00")

    def test_explicit_time_range(self):
        event = self.parse("Dentist on Friday 10:00-11:30")
        self.assertEqual(event.title, "Dentist")
        self.assertEqual(event.date_iso, "2026-11-20")
        self.assertEqual(event.time, "10:00")
        self.assertEqual(event.end_time, "11:30")

    def test_from_to_range(self):
        event = self.parse("Workshop from 13:00 to 14:30 tomorrow")
        self.assertEqual(event.title, "Workshop")
        self.assertEqual(event.time, "13:00")
        self.assertEqual(event.end_time, "14:30")

    def test_month_name_date(self):
        event = self.parse("Planning on 20 November 2026 at 10:00")
        self.assertEqual(event.title, "Planning")
        self.assertEqual(event.date_iso, "2026-11-20")
        self.assertEqual(event.time, "10:00")

    def test_month_name_without_year_rolls_forward(self):
        event = self.parse("Trip 5 January")
        self.assertEqual(event.date_iso, "2027-01-05")

    def test_iso_date(self):
        event = self.parse("Release 2026-12-01 18:00")
        self.assertEqual(event.date_iso, "2026-12-01")
        self.assertEqual(event.time, "18:00")

    def test_dotted_date_with_year(self):
        event = self.parse("Іспит 20.11.2026 14:00-15:00")
        self.assertEqual(event.title, "Іспит")
        self.assertEqual(event.date_iso, "2026-11-20")
        self.assertEqual(event.time, "14:00")
        self.assertEqual(event.end_time, "15:00")

    def test_yearless_dotted_date_requires_an_unambiguous_day(self):
        event = self.parse("Dentist 20.11 10:00")
        self.assertEqual(event.date_iso, "2026-11-20")
        # "Python 3.11" is a title, not a date.
        version = self.parse("Python 3.11 tips tomorrow")
        self.assertEqual(version.title, "Python 3.11 tips")
        self.assertEqual(version.date_iso, "2026-11-18")

    def test_all_day_event(self):
        event = self.parse("Deep work tomorrow")
        self.assertEqual(event.title, "Deep work")
        self.assertEqual(event.date_iso, "2026-11-18")
        self.assertIsNone(event.time)
        self.assertIsNone(event.end_time)
        self.assertTrue(event.all_day)

    def test_am_pm(self):
        event = self.parse("Call at 7pm")
        self.assertEqual(event.title, "Call")
        self.assertEqual(event.time, "19:00")
        self.assertEqual(event.end_time, "20:00")

    def test_noon_and_midnight(self):
        self.assertEqual(self.parse("Lunch at 12pm").time, "12:00")
        self.assertEqual(self.parse("Night shift at 12am").time, "00:00")

    def test_at_hour_without_minutes(self):
        event = self.parse("Standup at 9")
        self.assertEqual(event.title, "Standup")
        self.assertEqual(event.time, "09:00")

    def test_plain_numbers_stay_in_the_title(self):
        event = self.parse("Buy 2 apples")
        self.assertEqual(event.title, "Buy 2 apples")
        self.assertIsNone(event.time)

    def test_empty_title_gets_an_empty_string(self):
        event = self.parse("tomorrow 13:00")
        self.assertEqual(event.title, "")
        self.assertEqual(event.date_iso, "2026-11-18")

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("   ")

    def test_invalid_calendar_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("Meeting 2026-02-31")

    def test_invalid_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("Meeting at 25:00")

    def test_backwards_range_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("Meeting tomorrow 15:00-14:00")

    def test_past_month_name_rolls_to_next_year(self):
        event = self.parse("Anniversary 1 May")
        self.assertEqual(event.date_iso, "2027-05-01")


class MeetingNoteTest(unittest.TestCase):
    def test_markdown_contains_event_details_and_sections(self):
        title, contents = meeting_note_markdown(
            {
                "title": "Design sync",
                "date": "2026-11-20",
                "time": "10:00",
                "end_time": "11:00",
                "location": "Room 4",
                "attendees": "Ann, Bob; Chris",
                "notes": "Review the new dashboard",
            },
            category_name="Work",
        )
        self.assertEqual(title, "Design sync — Meeting notes")
        self.assertIn("# Design sync — Meeting notes", contents)
        self.assertIn("- **Date:** 2026-11-20", contents)
        self.assertIn("- **Time:** 10:00–11:00", contents)
        self.assertIn("- **Location:** Room 4", contents)
        self.assertIn("- **Layer:** Work", contents)
        self.assertIn("## Agenda\nReview the new dashboard", contents)
        self.assertIn("- Ann", contents)
        self.assertIn("- Bob", contents)
        self.assertIn("- Chris", contents)
        self.assertIn("## Action items", contents)
        self.assertIn("- [ ] ", contents)

    def test_all_day_event_and_empty_lists(self):
        title, contents = meeting_note_markdown(
            {"title": "Retro", "date": "2026-11-20"}
        )
        self.assertEqual(title, "Retro — Meeting notes")
        self.assertIn("- **Time:** All day", contents)
        self.assertIn("## Agenda\n- ", contents)
        self.assertIn("## Attendees\n- ", contents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
