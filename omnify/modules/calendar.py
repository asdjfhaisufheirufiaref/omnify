"""Calendar module.

A Google-like planner on top of one storage key. The model is pure
Python (no GTK) and is unit-tested headlessly; the GTK layer below is a
week grid, a month page, a habit tracker, occupancy statistics and a
goals list, all sharing the same events.

Event layout (storage key ``"calendar"``)::

    {"id": "<uuid4-hex>", "date": "YYYY-MM-DD",
     "time": "HH:MM" | None, "end_time": "HH:MM" | None,
     "title": str, "notes": str, "category": "work",
     "recurrence": {"freq": "daily|weekly|monthly|yearly",
                    "interval": 1, "weekdays": [0..6],
                    "month_day": 1..31, "until": "YYYY-MM-DD"},
     "habit": bool, "done": ["YYYY-MM-DD", ...],
     "cancelled": ["YYYY-MM-DD", ...],
     "actions": [{"kind": "timer", "target": "Timer 1"},
                 {"kind": "stopwatch"},
                 {"kind": "command", "command": "..."}],
     "start_on_close": bool, "reminder": None | 0..1440}

Habits are simply recurring events with ``"habit": true``: the tracker
lists them, every tick is a ``done`` date, and statistics count the
occurrences. Categories ("layers") live under ``"calendar_categories"``,
goals under ``"calendar_goals"``, reusable event templates under
``"calendar_templates"`` and view/reminder preferences under
``"calendar_settings"``. ``"reminder"`` is the per-event lead time in
minutes (``None`` uses the global default, ``0`` disables it).
"""

from __future__ import annotations

import calendar as _calendar
import copy
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..core.i18n import tr
from ..core.module_context import BaseModule
from ..core.theme import CATEGORY_COLORS, category_color

STORAGE_KEY = "calendar"
CATEGORY_KEY = "calendar_categories"
GOAL_KEY = "calendar_goals"
TEMPLATE_KEY = "calendar_templates"
SETTINGS_KEY = "calendar_settings"
NOTIFIED_KEY = "calendar_notified"

_DATE_FMT = "%Y-%m-%d"
_TIME_FMT = "%H:%M"

#: Event fields the public ``update`` accepts (old + new schema).
_UPDATE_FIELDS = (
    "date",
    "time",
    "end_time",
    "title",
    "notes",
    "category",
    "recurrence",
    "habit",
    "done",
    "cancelled",
    "actions",
    "start_on_close",
    "reminder",
)

FREQUENCIES = ("none", "daily", "weekly", "monthly", "yearly")
FREQUENCY_LABELS = (
    ("none", "Does not repeat"),
    ("daily", "Every N days"),
    ("weekly", "Every N weeks"),
    ("monthly", "Every N months"),
    ("yearly", "Every N years"),
)

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: Layer colours available in the event dialog.
LAYER_COLORS = (
    "blue", "purple", "green", "orange",
    "pink", "teal", "red", "yellow", "grey",
)

DEFAULT_CATEGORIES: List[Dict[str, Any]] = [
    {"id": "work", "name": "Work", "color": "blue", "visible": True},
    {"id": "study", "name": "Study", "color": "purple", "visible": True},
    {"id": "health", "name": "Health", "color": "green", "visible": True},
    {"id": "personal", "name": "Personal", "color": "orange", "visible": True},
    {"id": "rest", "name": "Rest", "color": "teal", "visible": True},
]

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_layer_color(value: Any, fallback: str = "blue") -> str:
    """A palette name or a normalised ``#rrggbb`` custom colour."""
    if isinstance(value, str):
        text = value.strip()
        if text in CATEGORY_COLORS:
            return text
        if _HEX_COLOR_RE.fullmatch(text):
            return text.lower()
    return fallback


def normalize_layer_gradient(value: Any) -> Optional[List[str]]:
    """Two colours (palette names or hex) or ``None`` for no gradient."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if text in CATEGORY_COLORS:
            out.append(CATEGORY_COLORS[text])
        elif _HEX_COLOR_RE.fullmatch(text):
            out.append(text.lower())
        else:
            return None
    return out


def layer_style_id(category_id: str) -> str:
    """CSS-safe suffix derived from a category id."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(category_id or "layer"))


def _rgba(hex_color: str, alpha: float) -> str:
    from ..core.theme import parse_hex_color

    red, green, blue = parse_hex_color(hex_color)
    return (
        f"rgba({round(red * 255)}, {round(green * 255)}, "
        f"{round(blue * 255)}, {alpha})"
    )


def layer_css(category: Dict[str, Any], phase: int = 135) -> str:
    """Per-layer CSS for event chips, layer pills and swatch dots.

    Pure and unit-testable.  A custom gradient may be animated by
    re-loading this CSS with a different *phase* (gradient angle).
    """
    cid = layer_style_id(str(category.get("id") or "layer"))
    base = category_color(category.get("color"))
    gradient = normalize_layer_gradient(category.get("gradient"))
    animated = bool(category.get("animated")) and gradient is not None
    if gradient:
        first, second = gradient
        angle = int(phase) if animated else 135
        event_bg = (
            f"linear-gradient({angle}deg, "
            f"{_rgba(first, 0.38)}, {_rgba(second, 0.38)})"
        )
        pill_bg = (
            f"linear-gradient({angle}deg, "
            f"{_rgba(first, 0.20)}, {_rgba(second, 0.20)})"
        )
        dot_bg = f"linear-gradient({angle}deg, {first}, {second})"
        border = first
        pill_decl = f"background-image: {pill_bg};"
    else:
        event_bg = _rgba(base, 0.38)
        pill_bg = _rgba(base, 0.20)
        dot_bg = base
        border = base
        pill_decl = f"background-color: {pill_bg};"
    return (
        f".omnify-event.omnify-layer-{cid} {{ background: {event_bg}; "
        f"border-left: 3px solid {border}; color: var(--omnify-fg); }}\n"
        f".omnify-event.omnify-layer-{cid}:hover {{ background: "
        f"{event_bg}; opacity: 0.9; }}\n"
        f".omnify-layer-dot-{cid} {{ background: {dot_bg}; }}\n"
        f".omnify-layer.omnify-pill-{cid} {{ {pill_decl} "
        f"border-color: {border}; }}\n"
        f".omnify-layer.omnify-pill-{cid}:hover {{ {pill_decl} "
        f"opacity: 0.92; }}\n"
        f".omnify-layer.omnify-pill-{cid}:checked {{ {pill_decl} "
        f"border-color: {border}; }}\n"
        f".omnify-layer.omnify-pill-{cid}:checked:hover {{ {pill_decl} "
        f"opacity: 0.92; }}\n"
    )


def rgba_to_hex(red: float, green: float, blue: float) -> str:
    """Clamp three 0..1 channels into ``#rrggbb``."""
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(red * 255))),
        max(0, min(255, round(green * 255))),
        max(0, min(255, round(blue * 255))),
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _is_valid_date(value) -> bool:
    """True if *value* is a strict ``YYYY-MM-DD`` string."""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, _DATE_FMT)
    except ValueError:
        return False
    return True


def _is_valid_time(value) -> bool:
    """True if *value* is a strict ``HH:MM`` string."""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, _TIME_FMT)
    except ValueError:
        return False
    return True


def parse_minutes(value: Optional[str]) -> Optional[int]:
    """``"HH:MM"`` → minutes since midnight, ``None`` for all-day."""
    if not value:
        return None
    try:
        hours, minutes = value.split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return None


def format_minutes(minutes: int) -> str:
    """Minutes since midnight → ``"HH:MM"`` (clamped to one day)."""
    minutes = max(0, min(24 * 60 - 1, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_clock_text(value: str) -> str:
    """Accept typed ``8:43``/``08:43`` and return strict ``HH:MM``."""
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("Time must be HH:MM, e.g. 08:43")
    hours_text, minutes_text = value.strip().split(":", 1)
    try:
        hours = int(hours_text)
        minutes = int(minutes_text)
    except ValueError:
        raise ValueError("Time must be HH:MM, e.g. 08:43")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError("Time must be between 00:00 and 23:59")
    return f"{hours:02d}:{minutes:02d}"


def week_start(day: date, start_weekday: int = 0) -> date:
    """First day of *day*'s week (Monday by default, or Sunday with 6)."""
    return day - timedelta(days=(day.weekday() - start_weekday) % 7)


def iter_days(start: date, end: date) -> Iterable[date]:
    """Inclusive day iterator (never raises on reversed input)."""
    if end < start:
        start, end = end, start
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def format_event_line(event: Dict) -> str:
    """One-line rendering of an event: ``HH:MM  Title`` or just the title."""
    time = event.get("time")
    title = event.get("title", "")
    if time:
        return f"{time}  {title}"
    return title


def _month_name(month: int) -> str:
    """English month name for 1..12 (empty string when out of range)."""
    try:
        return _calendar.month_name[int(month)]
    except (IndexError, KeyError, TypeError, ValueError):
        return ""


def _normalize_date_list(values) -> List[str]:
    """Validate/unique a list of ISO dates (invalid entries are dropped)."""
    if not isinstance(values, list):
        return []
    return sorted({v for v in values if _is_valid_date(v)})


def normalize_recurrence(rec) -> Optional[Dict[str, Any]]:
    """Validate a recurrence dict; ``None``/``"none"`` mean no repeat."""
    if not rec or rec == "none":
        return None
    if not isinstance(rec, dict):
        raise ValueError("Recurrence must be an object")
    freq = rec.get("freq", "none")
    if freq in (None, "", "none"):
        return None
    if freq not in FREQUENCIES:
        raise ValueError(f"Unknown recurrence frequency {freq!r}")
    interval = max(1, int(rec.get("interval", 1) or 1))
    out: Dict[str, Any] = {"freq": freq, "interval": interval}
    if freq == "weekly":
        weekdays = rec.get("weekdays")
        if weekdays is not None:
            try:
                out["weekdays"] = sorted({int(d) % 7 for d in weekdays})
            except (TypeError, ValueError):
                raise ValueError("Weekly recurrence needs weekday numbers")
        if rec.get("until"):
            if not _is_valid_date(rec["until"]):
                raise ValueError("Invalid recurrence end date")
            out["until"] = rec["until"]
        return out
    if freq == "monthly":
        if rec.get("month_day") is not None:
            day = int(rec["month_day"])
            if not 1 <= day <= 31:
                raise ValueError("Day of month must be 1..31")
            out["month_day"] = day
        if rec.get("weekday") is not None:
            out["weekday"] = int(rec["weekday"]) % 7
            nth = int(rec.get("nth", 0) or 0)
            if not 1 <= nth <= 5:
                raise ValueError("Nth weekday must be 1..5 (5 = last)")
            out["nth"] = nth
    if rec.get("until"):
        if not _is_valid_date(rec["until"]):
            raise ValueError("Invalid recurrence end date")
        out["until"] = rec["until"]
    return out


def _weekday_index_in_month(day: date) -> int:
    """1 for the first occurrence of this weekday in the month, … 5 last."""
    nth = (day.day - 1) // 7 + 1
    if day.day + 7 > _calendar.monthrange(day.year, day.month)[1]:
        return 5
    return nth


def occurs_on(event: Dict, day: date) -> bool:
    """True if *event* (possibly recurring) has an occurrence on *day*."""
    try:
        anchor = date.fromisoformat(event.get("date", ""))
    except (TypeError, ValueError):
        return False
    rec = event.get("recurrence") or None
    if not rec:
        return day == anchor
    if day < anchor:
        return False
    until = rec.get("until")
    if until:
        try:
            if day > date.fromisoformat(until):
                return False
        except ValueError:
            pass
    freq = rec.get("freq")
    interval = max(1, int(rec.get("interval", 1) or 1))
    if freq == "daily":
        return (day - anchor).days % interval == 0
    if freq == "weekly":
        weekdays = rec.get("weekdays") or [anchor.weekday()]
        anchor_monday = week_start(anchor)
        day_monday = week_start(day)
        weeks = (day_monday - anchor_monday).days // 7
        return weeks % interval == 0 and day.weekday() in weekdays
    if freq == "monthly":
        months = (day.year - anchor.year) * 12 + (day.month - anchor.month)
        if months < 0 or months % interval != 0:
            return False
        if rec.get("weekday") is not None:
            if day.weekday() != int(rec["weekday"]) % 7:
                return False
            return _weekday_index_in_month(day) == int(rec.get("nth", 1))
        wanted = int(rec.get("month_day") or anchor.day)
        last = _calendar.monthrange(day.year, day.month)[1]
        return day.day == min(wanted, last)
    if freq == "yearly":
        years = day.year - anchor.year
        if years < 0 or years % interval != 0:
            return False
        last = _calendar.monthrange(day.year, anchor.month)[1]
        return (
            day.month == anchor.month
            and day.day == min(anchor.day, last)
        )
    return day == anchor


def _normalize_actions(actions) -> List[Dict[str, str]]:
    """Keep only well-formed actions (unknown kinds are ignored)."""
    if not isinstance(actions, list):
        return []
    out: List[Dict[str, str]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        kind = action.get("kind")
        if kind == "timer":
            target = str(action.get("target") or "").strip()
            if target:
                out.append({"kind": "timer", "target": target})
        elif kind == "stopwatch":
            out.append({"kind": "stopwatch"})
        elif kind == "command":
            command = str(action.get("command") or "").strip()
            if command:
                out.append({"kind": "command", "command": command})
    return out


def _normalize_reminder(value) -> Optional[int]:
    """Per-event reminder lead: ``None`` = default, ``0`` = off."""
    if value is None or value == "":
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ValueError("Reminder lead must be a whole number of minutes")
    if not 0 <= minutes <= 24 * 60:
        raise ValueError("Reminder lead must be between 0 and 1440 minutes")
    return minutes


def _validate_event(
    date_iso: str,
    time,
    title: str,
    end_time=None,
) -> None:
    """Raise :class:`ValueError` for an unusable event field combination."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Event title must not be empty")
    if not _is_valid_date(date_iso):
        raise ValueError(f"Invalid date {date_iso!r} (expected YYYY-MM-DD)")
    if time is not None and not _is_valid_time(time):
        raise ValueError(f"Invalid time {time!r} (expected HH:MM)")
    if end_time is not None:
        if not _is_valid_time(end_time):
            raise ValueError(f"Invalid end time {end_time!r} (expected HH:MM)")
        if time is not None and parse_minutes(end_time) <= parse_minutes(time):
            raise ValueError("End time must be after the start time")


# ---------------------------------------------------------------------------
# Event store
# ---------------------------------------------------------------------------
class EventStore:
    """A storage-backed collection of calendar events.

    The store keeps **no in-memory cache**: every read goes through
    ``storage.load`` and every mutation is load → modify → save, so a
    corrupted file transparently falls back to an empty calendar.
    """

    def __init__(self, storage) -> None:
        self._storage = storage

    # -- reads ---------------------------------------------------------
    def all_events(self) -> List[Dict]:
        """Return every stored event (corrupt data → empty list)."""
        data = self._storage.load(STORAGE_KEY, [])
        if not isinstance(data, list):
            return []
        return data

    def get(self, event_id: str) -> Dict:
        """One event by id (``KeyError`` when missing)."""
        for event in self.all_events():
            if event.get("id") == event_id:
                return event
        raise KeyError(event_id)

    def get_for_date(self, iso_date: str) -> List[Dict]:
        """Events anchored exactly on one day: all-day first, then by time."""
        events = [e for e in self.all_events() if e.get("date") == iso_date]
        events.sort(key=lambda e: (e.get("time") is not None, e.get("time") or ""))
        return events

    def occurrences_between(
        self,
        start_iso: str,
        end_iso: str,
        visible_categories: Optional[Iterable[str]] = None,
    ) -> List[Dict]:
        """Every occurrence in ``[start, end]``, expanded, sorted by day/time.

        Each returned dict is a shallow copy of the stored event with an
        extra ``occurrence_date`` (``YYYY-MM-DD``). Cancelled occurrences
        are skipped. ``visible_categories`` filters layers: an event with
        no category is always shown.
        """
        try:
            start = date.fromisoformat(start_iso)
            end = date.fromisoformat(end_iso)
        except (TypeError, ValueError):
            return []
        visible = (
            set(visible_categories) if visible_categories is not None else None
        )
        out: List[Dict] = []
        for event in self.all_events():
            category = event.get("category") or ""
            if visible is not None and category and category not in visible:
                continue
            cancelled = set(event.get("cancelled") or [])
            for day in iter_days(start, end):
                iso = day.isoformat()
                if iso in cancelled or not occurs_on(event, day):
                    continue
                occurrence = dict(event)
                occurrence["occurrence_date"] = iso
                out.append(occurrence)
        out.sort(
            key=lambda e: (
                e["occurrence_date"],
                e.get("time") is not None,
                e.get("time") or "",
                e.get("title") or "",
            )
        )
        return out

    def day_events(
        self,
        iso_date: str,
        visible_categories: Optional[Iterable[str]] = None,
    ) -> List[Dict]:
        """Occurrences on one day (all-day first, then by start time)."""
        return self.occurrences_between(
            iso_date, iso_date, visible_categories=visible_categories
        )

    def habits(self) -> List[Dict]:
        """All checkable habit events."""
        return [e for e in self.all_events() if e.get("habit")]

    def is_done(self, event_id: str, iso_date: str) -> bool:
        """True if the occurrence on *iso_date* is ticked off."""
        try:
            event = self.get(event_id)
        except KeyError:
            return False
        return iso_date in set(event.get("done") or [])

    def set_done(self, event_id: str, iso_date: str, done: bool = True) -> bool:
        """Tick/untick one occurrence; returns the new state."""
        if not _is_valid_date(iso_date):
            raise ValueError("Invalid occurrence date")
        events = self.all_events()
        target = next((e for e in events if e.get("id") == event_id), None)
        if target is None:
            raise KeyError(event_id)
        dates = set(target.get("done") or [])
        if done:
            dates.add(iso_date)
        else:
            dates.discard(iso_date)
        target["done"] = sorted(dates)
        self._storage.save(STORAGE_KEY, events)
        return done

    def toggle_done(self, event_id: str, iso_date: str) -> bool:
        """Flip one occurrence's completion state; returns the new state."""
        return self.set_done(
            event_id, iso_date, not self.is_done(event_id, iso_date)
        )

    # -- mutations -----------------------------------------------------
    def add(
        self,
        date_iso: str,
        time,
        title: str,
        notes: str = "",
        *,
        end_time=None,
        category: str = "",
        recurrence=None,
        habit: bool = False,
        actions=None,
        start_on_close: bool = False,
        reminder=None,
    ) -> Dict:
        """Create an event, persist it, and return the stored dict."""
        _validate_event(date_iso, time, title, end_time)
        rec = normalize_recurrence(recurrence)
        if habit and rec is None:
            rec = {"freq": "daily", "interval": 1}
        event = {
            "id": uuid.uuid4().hex,
            "date": date_iso,
            "time": time,
            "end_time": end_time,
            "title": title.strip(),
            "notes": notes or "",
            "category": str(category or ""),
            "recurrence": rec,
            "habit": bool(habit),
            "done": [],
            "cancelled": [],
            "actions": _normalize_actions(actions),
            "start_on_close": bool(start_on_close),
            "reminder": _normalize_reminder(reminder),
        }
        events = self.all_events()
        events.append(event)
        self._storage.save(STORAGE_KEY, events)
        return event

    def update(self, event_id: str, **fields) -> Dict:
        """Update whitelisted fields of an event; return the updated dict.

        Unknown field names raise :class:`ValueError`; a missing id raises
        :class:`KeyError`. ``date``/``time`` are validated like in
        :meth:`add`.
        """
        unknown = set(fields) - set(_UPDATE_FIELDS)
        if unknown:
            raise ValueError(f"Unknown event fields: {', '.join(sorted(unknown))}")
        events = self.all_events()
        target = next((e for e in events if e.get("id") == event_id), None)
        if target is None:
            raise KeyError(event_id)
        if "recurrence" in fields:
            fields["recurrence"] = normalize_recurrence(fields["recurrence"])
        if "actions" in fields:
            fields["actions"] = _normalize_actions(fields["actions"])
        if "done" in fields:
            fields["done"] = _normalize_date_list(fields["done"])
        if "cancelled" in fields:
            fields["cancelled"] = _normalize_date_list(fields["cancelled"])
        if "habit" in fields:
            fields["habit"] = bool(fields["habit"])
        if "start_on_close" in fields:
            fields["start_on_close"] = bool(fields["start_on_close"])
        if "reminder" in fields:
            fields["reminder"] = _normalize_reminder(fields["reminder"])
        if "title" in fields and isinstance(fields["title"], str):
            fields["title"] = fields["title"].strip()
        target.update(fields)
        _validate_event(
            target["date"], target.get("time"), target["title"],
            target.get("end_time"),
        )
        self._storage.save(STORAGE_KEY, events)
        return target

    def remove(self, event_id: str) -> None:
        """Delete an event; raise :class:`KeyError` if it does not exist."""
        events = self.all_events()
        remaining = [e for e in events if e.get("id") != event_id]
        if len(remaining) == len(events):
            raise KeyError(event_id)
        self._storage.save(STORAGE_KEY, remaining)

    def cancel_occurrence(self, event_id: str, iso_date: str) -> Dict:
        """Hide just one occurrence of a recurring event.

        The rest of the series keeps running; the date lands in the
        event's ``cancelled`` exception list.
        """
        if not _is_valid_date(iso_date):
            raise ValueError("Invalid occurrence date")
        event = self.get(event_id)
        cancelled = set(event.get("cancelled") or [])
        cancelled.add(iso_date)
        return self.update(event_id, cancelled=sorted(cancelled))

    def truncate_series(self, event_id: str, iso_date: str) -> bool:
        """Delete *iso_date* and every later occurrence.

        Returns ``True`` when an earlier part of the series remains.  A
        non-recurring event (or a cutoff on/before its first date) is
        removed outright; otherwise recurrence gets an ``until`` on the
        day before the cutoff.
        """
        if not _is_valid_date(iso_date):
            raise ValueError("Invalid occurrence date")
        event = self.get(event_id)
        recurrence = event.get("recurrence")
        if not recurrence or iso_date <= event["date"]:
            self.remove(event_id)
            return False
        until = (date.fromisoformat(iso_date) - timedelta(days=1)).isoformat()
        updated = dict(recurrence)
        updated["until"] = until
        cancelled = [
            day for day in (event.get("cancelled") or []) if day < iso_date
        ]
        self.update(
            event_id,
            recurrence=updated,
            cancelled=cancelled,
        )
        return True

    # -- aggregation ---------------------------------------------------
    def habit_stats(
        self, event_id: str, start_iso: str, end_iso: str
    ) -> Dict[str, int]:
        """Done / expected / missed / cancelled for one habit in a range."""
        try:
            event = self.get(event_id)
            start = date.fromisoformat(start_iso)
            end = date.fromisoformat(end_iso)
        except (KeyError, TypeError, ValueError):
            return {"done": 0, "expected": 0, "missed": 0,
                    "cancelled": 0, "percent": 0}
        done = set(event.get("done") or [])
        cancelled = set(event.get("cancelled") or [])
        today = date.today()
        expected = done_count = missed = cancelled_count = 0
        for day in iter_days(start, end):
            iso = day.isoformat()
            if not occurs_on(event, day):
                continue
            if iso in cancelled:
                cancelled_count += 1
                continue
            expected += 1
            if iso in done:
                done_count += 1
            elif day < today:
                missed += 1
        percent = round(100 * done_count / expected) if expected else 0
        return {
            "done": done_count,
            "expected": expected,
            "missed": missed,
            "cancelled": cancelled_count,
            "percent": percent,
        }

    def stats_between(
        self,
        start_iso: str,
        end_iso: str,
        visible_categories: Optional[Iterable[str]] = None,
    ) -> Dict[str, int]:
        """Habit totals for the statistics cards."""
        try:
            start = date.fromisoformat(start_iso)
            end = date.fromisoformat(end_iso)
        except (TypeError, ValueError):
            return {"total": 0, "done": 0, "missed": 0,
                    "cancelled": 0, "percent": 0}
        visible = (
            set(visible_categories) if visible_categories is not None else None
        )
        today = date.today()
        total = done_count = missed = cancelled = 0
        for event in self.habits():
            category = event.get("category") or ""
            if visible is not None and category and category not in visible:
                continue
            done = set(event.get("done") or [])
            cancelled_dates = set(event.get("cancelled") or [])
            for day in iter_days(start, end):
                iso = day.isoformat()
                if not occurs_on(event, day):
                    continue
                if iso in cancelled_dates:
                    cancelled += 1
                    continue
                total += 1
                if iso in done:
                    done_count += 1
                elif day < today:
                    missed += 1
        percent = round(100 * done_count / total) if total else 0
        return {
            "total": total,
            "done": done_count,
            "missed": missed,
            "cancelled": cancelled,
            "percent": percent,
        }

    def daily_counts(
        self,
        start_iso: str,
        end_iso: str,
        visible_categories: Optional[Iterable[str]] = None,
    ) -> List[Tuple[str, int]]:
        """``[(iso, occurrences)]`` for each day in the range."""
        occurrences = self.occurrences_between(
            start_iso, end_iso, visible_categories=visible_categories
        )
        counts: Dict[str, int] = {}
        try:
            start = date.fromisoformat(start_iso)
            end = date.fromisoformat(end_iso)
        except (TypeError, ValueError):
            return []
        for day in iter_days(start, end):
            counts[day.isoformat()] = 0
        for occurrence in occurrences:
            iso = occurrence["occurrence_date"]
            counts[iso] = counts.get(iso, 0) + 1
        return sorted(counts.items())

    def category_counts(
        self,
        start_iso: str,
        end_iso: str,
        visible_categories: Optional[Iterable[str]] = None,
    ) -> Dict[str, int]:
        """Occurrence count per category id in the range."""
        counts: Dict[str, int] = {}
        for occurrence in self.occurrences_between(
            start_iso, end_iso, visible_categories=visible_categories
        ):
            key = occurrence.get("category") or ""
            counts[key] = counts.get(key, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Categories ("layers")
# ---------------------------------------------------------------------------
class CategoryStore:
    """User-visible event layers; ids are stable, colours are palette names."""

    def __init__(self, storage) -> None:
        self._storage = storage

    def all(self) -> List[Dict[str, Any]]:
        data = self._storage.load(CATEGORY_KEY, None)
        if data is None:
            return [dict(category) for category in DEFAULT_CATEGORIES]
        if not isinstance(data, list):
            return [dict(category) for category in DEFAULT_CATEGORIES]
        out = []
        for category in data:
            if not isinstance(category, dict) or not category.get("id"):
                continue
            gradient = normalize_layer_gradient(category.get("gradient"))
            out.append({
                "id": category["id"],
                "name": category.get("name") or category["id"],
                "color": normalize_layer_color(category.get("color")),
                "visible": category.get("visible", True) is not False,
                "gradient": gradient,
                "animated": bool(category.get("animated"))
                and gradient is not None,
            })
        return out

    def ensure(self) -> List[Dict[str, Any]]:
        """Persist the defaults the first time the calendar is opened."""
        categories = self.all()
        if self._storage.load(CATEGORY_KEY, None) is None:
            self._storage.save(CATEGORY_KEY, categories)
        return categories

    def get(self, category_id: str) -> Optional[Dict[str, Any]]:
        for category in self.all():
            if category["id"] == category_id:
                return category
        return None

    def visible_ids(self) -> List[str]:
        return [c["id"] for c in self.all() if c.get("visible", True)]

    def set_visible(self, category_id: str, visible: bool) -> None:
        categories = self.all()
        for category in categories:
            if category["id"] == category_id:
                category["visible"] = bool(visible)
        self._storage.save(CATEGORY_KEY, categories)

    def update(self, category_id: str, **fields) -> Dict[str, Any]:
        """Rename/recolour/show a layer; unknown fields are rejected."""
        allowed = {"name", "color", "visible", "gradient", "animated"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(
                f"Unknown layer fields: {', '.join(sorted(unknown))}"
            )
        categories = self.all()
        target = next(
            (category for category in categories if category["id"] == category_id),
            None,
        )
        if target is None:
            raise KeyError(category_id)
        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise ValueError("Layer name must not be empty")
            fields["name"] = name
        if "color" in fields:
            fields["color"] = normalize_layer_color(fields["color"])
        if "gradient" in fields:
            gradient = normalize_layer_gradient(fields["gradient"])
            if fields["gradient"] is not None and gradient is None:
                raise ValueError(
                    "A gradient needs two valid colours"
                )
            fields["gradient"] = gradient
        if "animated" in fields:
            fields["animated"] = bool(fields["animated"])
        target.update(fields)
        if target.get("gradient") is None:
            target["animated"] = False
        self._storage.save(CATEGORY_KEY, categories)
        return target

    def add(
        self,
        name: str,
        color: str = "blue",
        gradient=None,
        animated: bool = False,
    ) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Layer name must not be empty")
        gradient = normalize_layer_gradient(gradient)
        categories = self.ensure()
        category_id = uuid.uuid4().hex[:8]
        category = {
            "id": category_id,
            "name": name,
            "color": normalize_layer_color(color),
            "visible": True,
            "gradient": gradient,
            "animated": bool(animated) and gradient is not None,
        }
        categories.append(category)
        self._storage.save(CATEGORY_KEY, categories)
        return category

    def remove(self, category_id: str) -> None:
        categories = [c for c in self.all() if c["id"] != category_id]
        self._storage.save(CATEGORY_KEY, categories)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------
class GoalStore:
    """Goal cards with either a numeric target or a checklist."""

    def __init__(self, storage) -> None:
        self._storage = storage

    def all(self) -> List[Dict]:
        data = self._storage.load(GOAL_KEY, [])
        return data if isinstance(data, list) else []

    def get(self, goal_id: str) -> Dict:
        for goal in self.all():
            if goal.get("id") == goal_id:
                return goal
        raise KeyError(goal_id)

    def add(
        self,
        title: str,
        target: int = 0,
        unit: str = "",
        deadline: str = "",
    ) -> Dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("Goal title must not be empty")
        if deadline and not _is_valid_date(deadline):
            raise ValueError("Invalid deadline (expected YYYY-MM-DD)")
        goal = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "target": max(0, int(target or 0)),
            "current": 0,
            "unit": (unit or "").strip(),
            "deadline": deadline or "",
            "items": [],
        }
        goals = self.all()
        goals.append(goal)
        self._storage.save(GOAL_KEY, goals)
        return goal

    def update(self, goal_id: str, **fields) -> Dict:
        allowed = {"title", "target", "current", "unit", "deadline"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown goal fields: {', '.join(sorted(unknown))}")
        goals = self.all()
        target = next((g for g in goals if g.get("id") == goal_id), None)
        if target is None:
            raise KeyError(goal_id)
        target.update(fields)
        self._storage.save(GOAL_KEY, goals)
        return target

    def remove(self, goal_id: str) -> None:
        goals = [g for g in self.all() if g.get("id") != goal_id]
        self._storage.save(GOAL_KEY, goals)

    def add_item(self, goal_id: str, text: str) -> Dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Goal item must not be empty")
        goals = self.all()
        goal = next((g for g in goals if g.get("id") == goal_id), None)
        if goal is None:
            raise KeyError(goal_id)
        item = {"text": text, "done": False}
        goal.setdefault("items", []).append(item)
        self._storage.save(GOAL_KEY, goals)
        return item

    def toggle_item(self, goal_id: str, index: int) -> bool:
        goals = self.all()
        goal = next((g for g in goals if g.get("id") == goal_id), None)
        if goal is None:
            raise KeyError(goal_id)
        items = goal.get("items") or []
        if not 0 <= index < len(items):
            raise IndexError(index)
        items[index]["done"] = not items[index].get("done", False)
        self._storage.save(GOAL_KEY, goals)
        return items[index]["done"]

    @staticmethod
    def progress(goal: Dict) -> Tuple[int, int, int]:
        """``(done, total, percent)`` from the checklist or numeric fields."""
        items = goal.get("items") or []
        if items:
            done = sum(1 for item in items if item.get("done"))
            total = len(items)
        else:
            total = int(goal.get("target") or 0)
            done = min(int(goal.get("current") or 0), total)
        percent = round(100 * done / total) if total else 0
        return done, total, percent


# ---------------------------------------------------------------------------
# View settings, templates and undo history
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: Dict[str, Any] = {
    "week_start": 0,        # Monday; 6 = Sunday
    "day_start_hour": 0,    # first visible hour in the week grid
    "day_end_hour": 24,     # exclusive
    "hour_height": 56,      # pixels per hour
    "reminders": True,
    "reminder_lead": 30,    # default lead time in minutes
}

_SETTING_RANGES = {
    "week_start": (0, 6),
    "day_start_hour": (0, 23),
    "day_end_hour": (1, 24),
    "hour_height": (24, 160),
    "reminder_lead": (1, 10080),
}


class CalendarSettings:
    """Persisted calendar view preferences with validation."""

    def __init__(self, storage) -> None:
        self._storage = storage

    def all(self) -> Dict[str, Any]:
        data = self._storage.load(SETTINGS_KEY, {})
        values = dict(DEFAULT_SETTINGS)
        if isinstance(data, dict):
            values.update({k: v for k, v in data.items() if k in values})
        try:
            return self._validate(values)
        except ValueError:
            return dict(DEFAULT_SETTINGS)

    def update(self, **fields) -> Dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(
                f"Unknown calendar settings: {', '.join(sorted(unknown))}"
            )
        values = self.all()
        values.update(fields)
        values = self._validate(values)
        self._storage.save(SETTINGS_KEY, values)
        return values

    @staticmethod
    def _validate(values: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(values)
        for key, (low, high) in _SETTING_RANGES.items():
            try:
                number = int(out.get(key, DEFAULT_SETTINGS[key]))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a whole number")
            if not low <= number <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
            out[key] = number
        if out["day_end_hour"] - out["day_start_hour"] < 2:
            raise ValueError("The visible day must span at least 2 hours")
        if out["week_start"] not in (0, 6):
            raise ValueError("The week must start on Monday or Sunday")
        out["reminders"] = bool(out.get("reminders", True))
        return out


class TemplateStore:
    """Reusable event presets (title, duration, layer, recurrence, actions)."""

    def __init__(self, storage) -> None:
        self._storage = storage

    def all(self) -> List[Dict[str, Any]]:
        data = self._storage.load(TEMPLATE_KEY, [])
        if not isinstance(data, list):
            return []
        return [
            template for template in data
            if isinstance(template, dict) and template.get("id")
            and template.get("title")
        ]

    def add(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        title = str(fields.get("title") or "").strip()
        if not title:
            raise ValueError("A template needs a title")
        time = fields.get("time")
        end_time = fields.get("end_time")
        if time is not None and not _is_valid_time(time):
            raise ValueError(f"Invalid time {time!r} (expected HH:MM)")
        if end_time is not None and not _is_valid_time(end_time):
            raise ValueError(f"Invalid end time {end_time!r}")
        if time and end_time and parse_minutes(end_time) <= parse_minutes(time):
            raise ValueError("End time must be after the start time")
        template = {
            "id": uuid.uuid4().hex[:10],
            "title": title,
            "time": time,
            "end_time": end_time,
            "notes": str(fields.get("notes") or ""),
            "category": str(fields.get("category") or ""),
            "recurrence": normalize_recurrence(fields.get("recurrence")),
            "habit": bool(fields.get("habit")),
            "actions": _normalize_actions(fields.get("actions")),
            "start_on_close": bool(fields.get("start_on_close")),
            "reminder": _normalize_reminder(fields.get("reminder")),
        }
        templates = self.all()
        templates.append(template)
        self._storage.save(TEMPLATE_KEY, templates)
        return template

    def remove(self, template_id: str) -> None:
        templates = self.all()
        remaining = [t for t in templates if t["id"] != template_id]
        if len(remaining) == len(templates):
            raise KeyError(template_id)
        self._storage.save(TEMPLATE_KEY, remaining)


class CalendarHistory:
    """Bounded undo/redo history over full calendar snapshots.

    The calendar is small (a few hundred events), so storing whole JSON
    snapshots is simpler and safer than per-action inverse operations:
    every change — event edits, habit ticks, goal progress, layer names —
    is undone by restoring the exact state that preceded it.
    """

    def __init__(self, limit: int = 80) -> None:
        self._limit = max(1, int(limit))
        self._undo: List[Tuple[str, Dict, Dict]] = []
        self._redo: List[Tuple[str, Dict, Dict]] = []

    def record(self, label: str, before: Dict, after: Dict) -> bool:
        if before == after:
            return False
        self._undo.append((label, copy.deepcopy(before), copy.deepcopy(after)))
        if len(self._undo) > self._limit:
            self._undo.pop(0)
        self._redo.clear()
        return True

    def undo(self) -> Optional[Tuple[Dict, str]]:
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(entry)
        return copy.deepcopy(entry[1]), entry[0]

    def redo(self) -> Optional[Tuple[Dict, str]]:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(entry)
        return copy.deepcopy(entry[2]), entry[0]

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()


# ---------------------------------------------------------------------------
# Search / reminder helpers (pure, unit-tested)
# ---------------------------------------------------------------------------
def filter_events(
    events: Iterable[Dict],
    query: str = "",
    category: str = "",
    scope: str = "all",
    today: Optional[date] = None,
) -> List[Dict]:
    """Filter events by text, layer and time scope; sorted chronologically."""
    needle = (query or "").strip().lower()
    today_iso = (today or date.today()).isoformat()
    out: List[Dict] = []
    for event in events:
        if category and (event.get("category") or "") != category:
            continue
        event_date = event.get("date") or ""
        if scope == "upcoming" and event_date < today_iso:
            continue
        if scope == "past" and event_date >= today_iso:
            continue
        if scope == "recurring" and not event.get("recurrence"):
            continue
        if needle:
            haystack = " ".join(
                str(event.get(field) or "")
                for field in ("title", "notes", "category")
            ).lower()
            if needle not in haystack:
                continue
        out.append(event)
    out.sort(
        key=lambda e: (
            e.get("date") or "",
            e.get("time") or "99:99",
            (e.get("title") or "").lower(),
        )
    )
    return out


def due_reminders(
    store: EventStore,
    now: datetime,
    default_lead: int = 10,
) -> List[Tuple[str, str, Dict, int]]:
    """Occurrences starting within their reminder lead time.

    Returns ``(event_id, occurrence_date, occurrence, minutes_left)``.
    Cancelled instances and all-day events are skipped; reminders fire at
    most once per occurrence (the caller persists the notified keys).
    """
    out: List[Tuple[str, str, Dict, int]] = []
    if default_lead <= 0:
        return out
    for delta in (0, 1):
        day = (now + timedelta(days=delta)).date()
        for occurrence in store.day_events(day.isoformat()):
            start = parse_minutes(occurrence.get("time"))
            if start is None:
                continue
            lead = occurrence.get("reminder")
            if lead is None:
                lead = default_lead
            try:
                lead = int(lead)
            except (TypeError, ValueError):
                continue
            if lead <= 0:
                continue
            start_dt = datetime.combine(day, datetime.min.time()).replace(
                hour=start // 60, minute=start % 60
            )
            seconds = (start_dt - now).total_seconds()
            if 0 < seconds <= lead * 60:
                minutes = max(1, int((seconds + 59) // 60))
                out.append(
                    (
                        occurrence.get("id") or "",
                        day.isoformat(),
                        occurrence,
                        minutes,
                    )
                )
    return out


# === GTK VIEW ===
class CalendarModule(BaseModule):
    """Google-like week/month planner with habits, statistics and goals."""

    id = "calendar"
    name = "Calendar"
    icon = "📅"
    description = "Week view, habits, occupancy stats and goals."
    group = "Tools"

    _DAY_WIDTH = 128
    _GUTTER_WIDTH = 58

    # -- construction -------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        self._store = EventStore(self.ctx.storage)
        self._cats = CategoryStore(self.ctx.storage)
        self._cats.ensure()
        self._cat_colors = {
            category["id"]: category.get("color") or "blue"
            for category in self._cats.all()
        }
        self._goals = GoalStore(self.ctx.storage)
        self._settings = CalendarSettings(self.ctx.storage)
        self._templates = TemplateStore(self.ctx.storage)
        self._prefs = self._settings.all()
        self._hour_height = self._prefs["hour_height"]
        self._day_start_hour = self._prefs["day_start_hour"]
        self._day_end_hour = self._prefs["day_end_hour"]
        self._history = CalendarHistory()
        today = date.today()
        self._week_start = week_start(today, self._prefs["week_start"])
        self._habit_week = self._week_start
        self._selected_day = today
        self._visible = set(self._cats.visible_ids())
        self._fired: set = set()
        self._fired_day = today.isoformat()
        self._dialog = None
        self._goal_dialog = None
        self._layers_dialog = None
        self._settings_dialog = None
        self._templates_popover = None
        self._shortcuts_popover = None
        self._cat_map: Dict[str, Dict[str, Any]] = {}
        self._layer_providers: Dict[str, Any] = {}
        self._layer_phase = 0
        self._layer_tick_id = 0
        self._week_bg_areas: List[Any] = []
        self._stat_labels: Dict[str, Any] = {}
        self._last_minute = ""

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(20)
        root.set_margin_bottom(20)
        root.set_margin_start(20)
        root.set_margin_end(20)
        self._root = root

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label="Calendar", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        header.append(title)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._switcher = Gtk.StackSwitcher()
        self._switcher.set_margin_start(12)
        header.append(self._switcher)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        self._undo_button = Gtk.Button(label="↶")
        self._undo_button.set_tooltip_text("Undo (Ctrl+Z)")
        self._undo_button.connect("clicked", lambda _b: self._undo())
        header.append(self._undo_button)
        self._redo_button = Gtk.Button(label="↷")
        self._redo_button.set_tooltip_text("Redo (Ctrl+Shift+Z)")
        self._redo_button.connect("clicked", lambda _b: self._redo())
        header.append(self._redo_button)

        search_button = Gtk.Button(label="🔍")
        search_button.set_tooltip_text("Search and filter events (Ctrl+F)")
        search_button.connect("clicked", lambda _b: self._focus_search())
        header.append(search_button)

        templates_button = Gtk.Button(label="Templates")
        templates_button.set_tooltip_text("Reusable event presets")
        templates_button.connect("clicked", self._open_templates_menu)
        header.append(templates_button)

        settings_button = Gtk.Button(label="⚙")
        settings_button.set_tooltip_text("Week grid and reminder settings")
        settings_button.connect("clicked", lambda _b: self._open_settings_dialog())
        header.append(settings_button)

        shortcuts_button = Gtk.Button(label="⌨")
        shortcuts_button.set_tooltip_text("Keyboard shortcuts")
        shortcuts_button.connect("clicked", self._open_shortcuts_menu)
        header.append(shortcuts_button)

        add_event = Gtk.Button(label="+ Event")
        add_event.get_style_context().add_class("omnify-accent")
        add_event.connect("clicked", lambda _b: self._open_event_dialog())
        header.append(add_event)
        add_habit = Gtk.Button(label="+ Habit")
        add_habit.connect(
            "clicked", lambda _b: self._open_event_dialog(habit=True)
        )
        header.append(add_habit)
        root.append(header)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        prev_button = Gtk.Button(label="‹")
        prev_button.set_tooltip_text("Previous week/month")
        prev_button.connect("clicked", lambda _b: self._navigate(-1))
        today_button = Gtk.Button(label="Today")
        today_button.connect("clicked", lambda _b: self._go_today())
        next_button = Gtk.Button(label="›")
        next_button.set_tooltip_text("Next week/month")
        next_button.connect("clicked", lambda _b: self._navigate(1))
        self._range_label = Gtk.Label(label="", xalign=0.0)
        self._range_label.get_style_context().add_class("omnify-card-title")
        self._range_label.set_margin_start(8)
        nav.append(prev_button)
        nav.append(today_button)
        nav.append(next_button)
        nav.append(self._range_label)
        root.append(nav)

        layers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        layers_label = Gtk.Label(label="Layers:", xalign=0.0)
        layers_label.get_style_context().add_class("omnify-dim")
        layers.append(layers_label)
        self._layers_row = layers
        self._rebuild_layer_toggles()
        root.append(layers)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._stack.add_titled(self._build_week_page(), "week", "Week")
        self._stack.add_titled(self._build_month_page(), "month", "Day")
        self._stack.add_titled(self._build_habits_page(), "habits", "Habits")
        self._stack.add_titled(self._build_stats_page(), "stats", "Stats")
        self._stack.add_titled(self._build_goals_page(), "goals", "Goals")
        self._stack.add_titled(self._build_search_page(), "search", "Search")
        # Bind *after* the pages exist: a switcher bound to an empty stack
        # creates its buttons but never shows them.
        self._switcher.set_stack(self._stack)
        self._stack.set_visible_child_name("week")
        self._stack.connect("notify::visible-child", self._on_page_changed)
        self._stack.set_vexpand(True)
        root.append(self._stack)

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_module_key_pressed)
        root.add_controller(controller)

        self._sync_history_buttons()
        self._refresh_current()
        return root

    def _rebuild_layer_toggles(self) -> None:
        """Recreate the layer pills + the \"edit layers\" button."""
        from gi.repository import Gtk

        self._refresh_layer_styles()
        label = self._layers_row.get_first_child()
        child = label.get_next_sibling() if label is not None else None
        while child is not None:
            following = child.get_next_sibling()
            self._layers_row.remove(child)
            child = following
        categories = self._cats.all()
        self._cat_colors = {
            category["id"]: category.get("color") or "blue"
            for category in categories
        }
        self._visible = set(self._cats.visible_ids())
        for category in categories:
            self._layers_row.append(self._layer_toggle(category))
        edit = Gtk.Button(label="✎ Layers")
        edit.get_style_context().add_class("omnify-layer")
        edit.set_tooltip_text(
            "Create, rename, recolour or delete event layers"
        )
        edit.connect("clicked", lambda _b: self._open_layers_dialog())
        self._layers_row.append(edit)

    def _layer_toggle(self, category: Dict[str, Any]):
        """One pill toggle for a category layer."""
        from gi.repository import Gtk

        button = Gtk.ToggleButton()
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Box()
        dot.get_style_context().add_class("omnify-swatch")
        self._apply_layer_dot(dot, category)
        content.append(dot)
        content.append(Gtk.Label(label=category.get("name") or category["id"]))
        button.set_child(content)
        button.get_style_context().add_class("omnify-layer")
        self._apply_layer_pill(button, category)
        button.set_active(category.get("visible", True))
        button.set_tooltip_text("Show/hide this layer")
        button.connect("toggled", self._on_layer_toggled, category["id"])
        return button

    # -- custom layer colours / gradients ------------------------------
    def _refresh_layer_styles(self) -> None:
        """Rebuild the per-layer CSS providers used by pills and chips."""
        from gi.repository import Gdk, Gtk

        display = Gdk.Display.get_default()
        categories = self._cats.all()
        self._cat_map = {category["id"]: category for category in categories}
        animated = False
        for category in categories:
            category_id = category["id"]
            provider = self._layer_providers.get(category_id)
            if provider is None:
                provider = Gtk.CssProvider()
                self._layer_providers[category_id] = provider
                if display is not None:
                    Gtk.StyleContext.add_provider_for_display(
                        display,
                        provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
            try:
                provider.load_from_data(
                    layer_css(category, self._layer_phase).encode()
                )
            except Exception:
                pass
            if category.get("animated") and category.get("gradient"):
                animated = True
        for category_id in list(self._layer_providers):
            if category_id in self._cat_map:
                continue
            provider = self._layer_providers.pop(category_id)
            if display is not None:
                try:
                    Gtk.StyleContext.remove_provider_for_display(
                        display, provider
                    )
                except Exception:
                    pass
        if animated:
            self._start_layer_ticker()
        else:
            self._stop_layer_ticker()

    def _start_layer_ticker(self) -> None:
        if self._layer_tick_id:
            return
        from gi.repository import GLib

        self._layer_tick_id = GLib.timeout_add(
            120, self._tick_layer_animation
        )

    def _stop_layer_ticker(self) -> None:
        if not self._layer_tick_id:
            return
        from gi.repository import GLib

        try:
            GLib.source_remove(self._layer_tick_id)
        except Exception:
            pass
        self._layer_tick_id = 0

    def _tick_layer_animation(self) -> bool:
        animated = [
            category
            for category in self._cat_map.values()
            if category.get("animated") and category.get("gradient")
        ]
        if not animated:
            self._layer_tick_id = 0
            return False
        self._layer_phase = (self._layer_phase + 4) % 360
        for category in animated:
            provider = self._layer_providers.get(category["id"])
            if provider is None:
                continue
            try:
                provider.load_from_data(
                    layer_css(category, self._layer_phase).encode()
                )
            except Exception:
                pass
        return True

    def _category_is_preset(self, category: Optional[Dict[str, Any]]) -> bool:
        if category is None:
            return True
        return (
            not category.get("gradient")
            and not category.get("animated")
            and (category.get("color") or "grey") in CATEGORY_COLORS
        )

    def _apply_layer_dot(self, dot, category) -> None:
        if category is not None and not self._category_is_preset(category):
            dot.get_style_context().add_class(
                f"omnify-layer-dot-{layer_style_id(category['id'])}"
            )
            return
        color = (category or {}).get("color") or "grey"
        if color not in CATEGORY_COLORS:
            color = "grey"
        dot.get_style_context().add_class(f"omnify-swatch-{color}")

    def _apply_layer_pill(self, button, category) -> None:
        if category is not None and not self._category_is_preset(category):
            button.get_style_context().add_class(
                f"omnify-pill-{layer_style_id(category['id'])}"
            )

    def _apply_event_layer(self, button, event) -> None:
        category = self._cat_map.get(event.get("category") or "")
        if category is not None and not self._category_is_preset(category):
            button.get_style_context().add_class(
                f"omnify-layer-{layer_style_id(category['id'])}"
            )
            return
        color = (category or {}).get("color") or "grey"
        if color not in CATEGORY_COLORS:
            color = "grey"
        button.get_style_context().add_class(f"omnify-cat-{color}")

    def _on_layer_toggled(self, button: Gtk.ToggleButton, category_id: str) -> None:
        before = self._snapshot()
        visible = button.get_active()
        if visible:
            self._visible.add(category_id)
        else:
            self._visible.discard(category_id)
        self._cats.set_visible(category_id, visible)
        self._record_history(
            "Show layer" if visible else "Hide layer", before
        )
        self._refresh_current()

    # -- undo / redo ---------------------------------------------------
    def _snapshot(self) -> Dict[str, Any]:
        """Deep-copy every calendar storage key for the history stack."""
        return {
            key: copy.deepcopy(self.ctx.storage.load(key, None))
            for key in (
                STORAGE_KEY,
                GOAL_KEY,
                CATEGORY_KEY,
                TEMPLATE_KEY,
                SETTINGS_KEY,
            )
        }

    def _record_history(self, label: str, before: Dict[str, Any]) -> None:
        if self._history.record(label, before, self._snapshot()):
            self._sync_history_buttons()

    def _sync_history_buttons(self) -> None:
        if getattr(self, "_undo_button", None) is None:
            return
        self._undo_button.set_sensitive(self._history.can_undo)
        self._redo_button.set_sensitive(self._history.can_redo)
        undo_label = self._history.undo_label
        redo_label = self._history.redo_label
        self._undo_button.set_tooltip_text(
            f"Undo: {undo_label} (Ctrl+Z)"
            if undo_label
            else "Nothing to undo"
        )
        self._redo_button.set_tooltip_text(
            f"Redo: {redo_label} (Ctrl+Shift+Z)"
            if redo_label
            else "Nothing to redo"
        )

    def _restore_snapshot(self, snapshot: Dict[str, Any]) -> None:
        for key, value in snapshot.items():
            if value is None:
                self.ctx.storage.delete(key)
            else:
                self.ctx.storage.save(key, copy.deepcopy(value))
        self._prefs = self._settings.all()
        self._hour_height = self._prefs["hour_height"]
        self._day_start_hour = self._prefs["day_start_hour"]
        self._day_end_hour = self._prefs["day_end_hour"]
        self._rebuild_layer_toggles()
        self._week_scrolled = False
        self._sync_history_buttons()
        self._refresh_current()
        if self._stack.get_visible_child_name() == "week":
            self._scroll_week_to_now()

    def _undo(self) -> None:
        result = self._history.undo()
        if result is None:
            self.ctx.notify("Nothing to undo")
            return
        snapshot, label = result
        self._restore_snapshot(snapshot)
        self.ctx.notify(f"Undid: {label}")

    def _redo(self) -> None:
        result = self._history.redo()
        if result is None:
            self.ctx.notify("Nothing to redo")
            return
        snapshot, label = result
        self._restore_snapshot(snapshot)
        self.ctx.notify(f"Redid: {label}")

    # -- keyboard shortcuts --------------------------------------------
    def _focus_is_editable(self) -> bool:
        from gi.repository import Gtk

        root = self._root.get_root()
        if root is None or not hasattr(root, "get_focus"):
            return False
        widget = root.get_focus()
        editable_types = (
            Gtk.Entry,
            Gtk.SearchEntry,
            Gtk.SpinButton,
            Gtk.TextView,
            Gtk.DropDown,
            Gtk.ComboBoxText,
        )
        while widget is not None:
            if isinstance(widget, editable_types):
                return True
            widget = widget.get_parent()
        return False

    def _on_module_key_pressed(
        self, _controller, keyval, _keycode, state
    ) -> bool:
        """Calendar-wide shortcuts (Ctrl combos, safe inside text fields)."""
        from gi.repository import Gdk

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if not control:
            return False
        if keyval == Gdk.KEY_f:
            self._focus_search()
            return True
        if keyval == Gdk.KEY_n:
            self._open_event_dialog(habit=shift)
            return True
        if keyval == Gdk.KEY_t:
            self._go_today()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            if self._focus_is_editable():
                return False
            self._navigate(-1 if keyval == Gdk.KEY_Left else 1)
            return True
        if keyval == Gdk.KEY_z:
            if self._focus_is_editable():
                return False
            self._redo() if shift else self._undo()
            return True
        if keyval == Gdk.KEY_y:
            if self._focus_is_editable():
                return False
            self._redo()
            return True
        if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._zoom_hour_height(1)
            return True
        if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._zoom_hour_height(-1)
            return True
        if keyval == Gdk.KEY_0:
            self._zoom_hour_height(0)
            return True
        page_keys = {
            Gdk.KEY_1: "week",
            Gdk.KEY_2: "month",
            Gdk.KEY_3: "habits",
            Gdk.KEY_4: "stats",
            Gdk.KEY_5: "goals",
            Gdk.KEY_6: "search",
        }
        page = page_keys.get(keyval)
        if page:
            self._stack.set_visible_child_name(page)
            return True
        return False

    def _focus_search(self) -> None:
        from gi.repository import GLib

        self._stack.set_visible_child_name("search")
        self._search_entry.grab_focus()
        GLib.idle_add(self._select_search_text)

    def _select_search_text(self) -> bool:
        self._search_entry.select_region(0, -1)
        return False

    _ZOOM_STEPS = (24, 32, 40, 48, 56, 64, 76, 88, 104, 120, 140, 160)

    def _zoom_hour_height(self, direction: int) -> None:
        """Ctrl +/- zoom for the time grid; 0 restores the default."""
        from gi.repository import GLib

        current = int(self._hour_height)
        if direction == 0:
            target = DEFAULT_SETTINGS["hour_height"]
        elif direction > 0:
            target = next(
                (value for value in self._ZOOM_STEPS if value > current),
                self._ZOOM_STEPS[-1],
            )
        else:
            target = next(
                (
                    value
                    for value in reversed(self._ZOOM_STEPS)
                    if value < current
                ),
                self._ZOOM_STEPS[0],
            )
        if target == current:
            return
        old_value = 0.0
        week_visible = self._stack.get_visible_child_name() == "week"
        if week_visible:
            adjustment = self._week_scroll.get_vadjustment()
            old_value = adjustment.get_value()
        before = self._snapshot()
        try:
            self._prefs = self._settings.update(hour_height=target)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._hour_height = target
        self._record_history("Zoom time grid", before)
        self._refresh_current()
        if week_visible:
            scaled = old_value * target / max(1, current)
            GLib.idle_add(self._restore_week_scroll, scaled)

    def _restore_week_scroll(self, value: float) -> bool:
        if getattr(self, "_week_scroll", None) is None:
            return False
        adjustment = self._week_scroll.get_vadjustment()
        lower = adjustment.get_lower()
        upper = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(max(lower, min(upper, value)))
        return False

    # -- templates -----------------------------------------------------
    def _open_templates_menu(self, button) -> None:
        from gi.repository import Gtk

        if self._templates_popover is not None:
            self._templates_popover.popdown()
            return
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        heading = Gtk.Label(label="Event templates", xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        box.append(heading)
        templates = self._templates.all()
        if not templates:
            hint = Gtk.Label(
                label=(
                    "No templates yet. Open an event, press "
                    "“Save as template”, and reuse it later."
                ),
                xalign=0.0,
            )
            hint.set_wrap(True)
            hint.set_max_width_chars(36)
            hint.get_style_context().add_class("omnify-dim")
            box.append(hint)
        for template in templates:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            use = Gtk.Button(label=template["title"])
            use.set_hexpand(True)
            use.set_halign(Gtk.Align.FILL)
            use.set_tooltip_text(
                f"{template.get('time') or 'all day'} · "
                f"{template.get('category') or 'no layer'}"
            )
            use.connect(
                "clicked",
                lambda _b, tpl=template: self._use_template(tpl),
            )
            remove = Gtk.Button(label="✕")
            remove.get_style_context().add_class("omnify-danger")
            remove.set_tooltip_text("Delete this template")
            remove.connect(
                "clicked",
                lambda _b, tid=template["id"]: self._delete_template(tid),
            )
            row.append(use)
            row.append(remove)
            box.append(row)
        popover.set_child(box)
        popover.set_parent(button)
        popover.connect("closed", self._on_templates_popover_closed)
        self._templates_popover = popover
        popover.popup()

    def _on_templates_popover_closed(self, popover) -> None:
        self._templates_popover = None
        popover.unparent()

    def _close_templates_menu(self) -> None:
        if self._templates_popover is not None:
            self._templates_popover.popdown()

    def _use_template(self, template: Dict[str, Any]) -> None:
        self._close_templates_menu()
        self._open_event_dialog(
            date_iso=date.today().isoformat(), prefill=template
        )

    def _delete_template(self, template_id: str) -> None:
        before = self._snapshot()
        try:
            self._templates.remove(template_id)
        except KeyError:
            return
        self._record_history("Delete template", before)
        self._close_templates_menu()
        self.ctx.notify("Template deleted")

    def _save_template_from_fields(self, fields: Dict[str, Any]) -> None:
        before = self._snapshot()
        try:
            template = self._templates.add(fields)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._record_history("Save template", before)
        self.ctx.notify(f"Template saved: {template['title']}")

    def _open_shortcuts_menu(self, button) -> None:
        """Static cheat-sheet for the calendar's keyboard shortcuts."""
        from gi.repository import Gtk

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        heading = Gtk.Label(label="Keyboard shortcuts", xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        box.append(heading)
        for keys, action in (
            ("Ctrl + N", "New event"),
            ("Ctrl + Shift + N", "New habit"),
            ("Ctrl + F", "Search and filters"),
            ("Ctrl + T", "Jump to today"),
            ("Ctrl + ← / →", "Previous / next week or day"),
            ("Ctrl + + / −", "Zoom the time grid in / out"),
            ("Ctrl + 0", "Reset the time-grid zoom"),
            ("Ctrl + 1 … 6", "Week, Day, Habits, Stats, Goals, Search"),
            ("Ctrl + Z", "Undo"),
            ("Ctrl + Shift + Z", "Redo"),
            ("Esc", "Close the open dialog"),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            key = Gtk.Label(label=keys, xalign=0.0)
            key.set_size_request(150, -1)
            key.get_style_context().add_class("omnify-dim")
            row.append(key)
            row.append(Gtk.Label(label=action, xalign=0.0))
            box.append(row)
        popover.set_child(box)
        popover.set_parent(button)
        popover.connect(
            "closed", lambda p: (p.unparent(), setattr(self, "_shortcuts_popover", None))
        )
        self._shortcuts_popover = popover
        popover.popup()

    def _open_layers_dialog(self) -> None:
        """Create, rename, recolour or delete event layers."""
        from gi.repository import Gdk, Gtk

        if self._layers_dialog is not None:
            dialog = self._layers_dialog
            if dialog.get_visible() and dialog.get_realized():
                dialog.present()
                return
            try:
                dialog.destroy()
            except Exception:
                pass
            self._layers_dialog = None

        win = Gtk.Window()
        win.set_title("Event layers")
        win.set_default_size(580, 560)
        win.set_modal(True)
        main = self._root.get_root()
        if main is not None:
            win.set_transient_for(main)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        hint = Gtk.Label(
            label=(
                "Layers colour-code events. Pick a palette colour or any "
                "custom colour, optionally with a static or animated "
                "gradient. Rename layers, add new ones or delete unused ones."
            ),
            xalign=0.0,
        )
        hint.set_wrap(True)
        hint.get_style_context().add_class("omnify-dim")
        box.append(hint)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll.set_child(rows_box)
        box.append(scroll)

        rows: List[Dict[str, Any]] = []
        deleted: set = set()

        def rgba_for(value: Any):
            rgba = Gdk.RGBA()
            rgba.parse(category_color(value))
            return rgba

        def make_row(category: Optional[Dict[str, Any]] = None) -> None:
            container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            name = Gtk.Entry()
            name.set_hexpand(True)
            if category:
                name.set_text(category.get("name") or "")
            else:
                name.set_placeholder_text("New layer name…")
            color = Gtk.ComboBoxText()
            for color_name in LAYER_COLORS:
                color.append(color_name, color_name.capitalize())
            color.append("custom", "Custom…")
            current_color = (category or {}).get("color") or "blue"
            color.set_active_id(
                current_color if current_color in LAYER_COLORS else "custom"
            )
            custom = Gtk.ColorDialogButton.new(
                Gtk.ColorDialog(title="Layer colour", with_alpha=False)
            )
            custom.set_tooltip_text("Pick any custom colour")
            custom.set_rgba(rgba_for(current_color))
            syncing = False

            def on_preset_changed(*_args) -> None:
                nonlocal syncing
                if syncing:
                    return
                chosen = color.get_active_id()
                if chosen == "custom" or chosen not in CATEGORY_COLORS:
                    return
                syncing = True
                custom.set_rgba(rgba_for(chosen))
                syncing = False

            def on_custom_changed(*_args) -> None:
                nonlocal syncing
                if syncing or color.get_active_id() == "custom":
                    return
                syncing = True
                color.set_active_id("custom")
                syncing = False

            color.connect("changed", on_preset_changed)
            custom.connect("notify::rgba", on_custom_changed)

            gradient = Gtk.CheckButton(label="Gradient")
            existing_gradient = (category or {}).get("gradient") or []
            gradient.set_active(len(existing_gradient) == 2)
            end_color = Gtk.ColorDialogButton.new(
                Gtk.ColorDialog(title="Gradient end colour", with_alpha=False)
            )
            end_color.set_tooltip_text("Gradient end colour")
            end_color.set_rgba(
                rgba_for(
                    existing_gradient[1] if len(existing_gradient) == 2
                    else "purple"
                )
            )
            animate = Gtk.CheckButton(label="Animate")
            animate.set_active(bool((category or {}).get("animated")))

            def sync_style_controls(*_args) -> None:
                enabled = gradient.get_active()
                end_color.set_visible(enabled)
                animate.set_visible(enabled)

            gradient.connect("toggled", sync_style_controls)
            sync_style_controls()

            record = {
                "id": (category or {}).get("id"),
                "name": name,
                "color": color,
                "custom": custom,
                "gradient": gradient,
                "end_color": end_color,
                "animate": animate,
                "row": container,
            }

            def remove_row(_button, rec=record) -> None:
                if rec.get("id"):
                    deleted.add(rec["id"])
                if rec in rows:
                    rows.remove(rec)
                rows_box.remove(rec["row"])

            delete = Gtk.Button(label="✕")
            delete.get_style_context().add_class("omnify-danger")
            delete.set_tooltip_text("Delete this layer")
            delete.connect("clicked", remove_row)
            row.append(name)
            row.append(color)
            row.append(custom)
            row.append(delete)
            container.append(row)
            style_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            style_row.set_margin_start(4)
            style_row.append(gradient)
            style_row.append(end_color)
            style_row.append(animate)
            container.append(style_row)
            rows.append(record)
            rows_box.append(container)

        for category in self._cats.all():
            make_row(category)

        add = Gtk.Button(label="+ Add layer")
        add.set_halign(Gtk.Align.START)
        add.connect("clicked", lambda _b: make_row())
        box.append(add)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("omnify-accent")
        cancel = Gtk.Button(label="Cancel")
        actions.append(save)
        actions.append(cancel)
        box.append(actions)

        def on_save(_button) -> None:
            prepared = []
            seen = set()
            for record in rows:
                name = record["name"].get_text().strip()
                if not name:
                    self.ctx.notify("Layer name must not be empty")
                    return
                key = name.lower()
                if key in seen:
                    self.ctx.notify(f"Duplicate layer name: {name}")
                    return
                seen.add(key)
                color_id = record["color"].get_active_id() or "blue"
                if color_id == "custom":
                    chosen = record["custom"].get_rgba()
                    base_hex = rgba_to_hex(
                        chosen.red, chosen.green, chosen.blue
                    )
                else:
                    base_hex = category_color(color_id)
                gradient = None
                color_value: str = color_id
                if record["gradient"].get_active():
                    end = record["end_color"].get_rgba()
                    gradient = [
                        base_hex,
                        rgba_to_hex(end.red, end.green, end.blue),
                    ]
                    color_value = base_hex
                animated = bool(
                    record["animate"].get_active() and gradient is not None
                )
                prepared.append(
                    (record, name, color_value, gradient, animated)
                )
            before = self._snapshot()
            try:
                for category_id in deleted:
                    if self._cats.get(category_id) is None:
                        continue
                    self._cats.remove(category_id)
                    # A deleted layer must not hide its events: make them
                    # uncategorised (uncategorised events are always shown).
                    for event in self._store.all_events():
                        if event.get("category") == category_id:
                            self._store.update(event["id"], category="")
                for record, name, color_value, gradient, animated in prepared:
                    if record["id"]:
                        self._cats.update(
                            record["id"],
                            name=name,
                            color=color_value,
                            gradient=gradient,
                            animated=animated,
                        )
                    else:
                        self._cats.add(
                            name,
                            color_value,
                            gradient=gradient,
                            animated=animated,
                        )
            except (ValueError, KeyError) as exc:
                self.ctx.notify(str(exc))
                return
            self._record_history("Edit layers", before)
            self._rebuild_layer_toggles()
            self._close_layers_dialog()
            self._refresh_current()

        save.connect("clicked", on_save)
        cancel.connect("clicked", lambda _b: self._close_layers_dialog())
        win.set_child(box)
        win.set_hide_on_close(False)
        win.get_style_context().add_class("omnify-dialog")
        win.connect("close-request", self._on_layers_dialog_close_request)
        win.connect("destroy", self._on_layers_dialog_destroyed)
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_dialog_key_pressed)
        win.add_controller(controller)
        self._layers_dialog = win
        win.present()

    def _close_layers_dialog(self) -> None:
        if self._layers_dialog is not None:
            dialog, self._layers_dialog = self._layers_dialog, None
            dialog.destroy()

    def _on_layers_dialog_close_request(self, _window) -> bool:
        self._layers_dialog = None
        return False

    def _on_layers_dialog_destroyed(self, _window) -> None:
        self._layers_dialog = None

    def _open_settings_dialog(self) -> None:
        """Week grid density/hours, week start and reminder defaults."""
        from gi.repository import Gtk

        if self._settings_dialog is not None:
            dialog = self._settings_dialog
            if dialog.get_visible() and dialog.get_realized():
                dialog.present()
                return
            try:
                dialog.destroy()
            except Exception:
                pass
            self._settings_dialog = None

        prefs = dict(self._prefs)
        win = Gtk.Window()
        win.set_title("Grid and reminders")
        win.set_default_size(460, -1)
        win.set_modal(True)
        main = self._root.get_root()
        if main is not None:
            win.set_transient_for(main)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)

        week = Gtk.ComboBoxText()
        week.append("0", "Monday")
        week.append("6", "Sunday")
        week.set_active_id(str(prefs["week_start"]))

        start_hour = Gtk.SpinButton.new_with_range(0, 22, 1)
        start_hour.set_value(prefs["day_start_hour"])
        end_hour = Gtk.SpinButton.new_with_range(2, 24, 1)
        end_hour.set_value(prefs["day_end_hour"])

        height = Gtk.ComboBoxText()
        for value, label in ((40, "Compact"), (56, "Normal"), (76, "Roomy")):
            height.append(str(value), label)
        height.set_active_id(str(prefs["hour_height"]))
        if height.get_active_id() is None:
            height.append(str(prefs["hour_height"]), "Custom")
            height.set_active_id(str(prefs["hour_height"]))

        reminders = Gtk.Switch()
        reminders.set_active(bool(prefs["reminders"]))
        lead = Gtk.SpinButton.new_with_range(1, 10080, 1)
        lead.set_value(prefs["reminder_lead"])
        lead.set_numeric(True)
        lead.set_tooltip_text(
            "Default reminder lead time in minutes (1 minute to 7 days)"
        )
        lead_suffix = Gtk.Label(label="minutes before", xalign=0.0)
        lead_suffix.get_style_context().add_class("omnify-dim")
        lead_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lead_row.append(lead)
        lead_row.append(lead_suffix)

        for row, (text, widget) in enumerate(
            (
                ("Week starts on", week),
                ("First visible hour", start_hour),
                ("Last visible hour", end_hour),
                ("Grid density", height),
                ("Reminders", reminders),
                ("Default reminder", lead_row),
            )
        ):
            label = Gtk.Label(label=text, xalign=0.0)
            label.get_style_context().add_class("omnify-dim")
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        box.append(grid)

        hint = Gtk.Label(
            label=(
                "Reminders use a desktop notification before each timed "
                "event. Per-event leads can be set in the event dialog; "
                "a lead of 0 disables the reminder for that event."
            ),
            xalign=0.0,
        )
        hint.set_wrap(True)
        hint.get_style_context().add_class("omnify-dim")
        box.append(hint)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("omnify-accent")
        cancel = Gtk.Button(label="Cancel")
        buttons.append(save)
        buttons.append(cancel)
        box.append(buttons)

        def on_save(_button) -> None:
            before = self._snapshot()
            try:
                updated = self._settings.update(
                    week_start=int(week.get_active_id() or "0"),
                    day_start_hour=int(start_hour.get_value()),
                    day_end_hour=int(end_hour.get_value()),
                    hour_height=int(height.get_active_id() or "56"),
                    reminders=reminders.get_active(),
                    reminder_lead=int(lead.get_value()),
                )
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            self._prefs = updated
            self._hour_height = updated["hour_height"]
            self._day_start_hour = updated["day_start_hour"]
            self._day_end_hour = updated["day_end_hour"]
            self._week_start = week_start(
                self._week_start, updated["week_start"]
            )
            self._habit_week = week_start(
                self._habit_week, updated["week_start"]
            )
            self._week_scrolled = False
            self._record_history("Change grid settings", before)
            self._close_settings_dialog()
            self._refresh_current()
            if self._stack.get_visible_child_name() == "week":
                self._scroll_week_to_now()

        save.connect("clicked", on_save)
        cancel.connect("clicked", lambda _b: self._close_settings_dialog())
        win.set_child(box)
        win.set_hide_on_close(False)
        win.get_style_context().add_class("omnify-dialog")
        win.connect("close-request", self._on_settings_dialog_close_request)
        win.connect("destroy", self._on_settings_dialog_destroyed)
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_dialog_key_pressed)
        win.add_controller(controller)
        self._settings_dialog = win
        win.present()

    def _close_settings_dialog(self) -> None:
        if self._settings_dialog is not None:
            dialog, self._settings_dialog = self._settings_dialog, None
            dialog.destroy()

    def _on_settings_dialog_close_request(self, _window) -> bool:
        self._settings_dialog = None
        return False

    def _on_settings_dialog_destroyed(self, _window) -> None:
        self._settings_dialog = None

    # -- page builders -------------------------------------------------
    def _build_week_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._week_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        page.append(self._week_header)

        self._week_allday = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        allday_scroll = Gtk.ScrolledWindow()
        allday_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        allday_scroll.set_min_content_height(28)
        allday_scroll.set_max_content_height(120)
        allday_scroll.set_propagate_natural_height(True)
        allday_scroll.set_child(self._week_allday)
        page.append(allday_scroll)

        self._week_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        week_scroll = Gtk.ScrolledWindow()
        week_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        week_scroll.set_child(self._week_grid)
        week_scroll.set_vexpand(True)
        self._week_scroll = week_scroll
        self._week_scrolled = False
        page.append(week_scroll)
        return page

    def _build_month_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._month_calendar = Gtk.Calendar()
        self._month_calendar.connect(
            "day-selected", self._on_month_day_selected
        )

        self._month_day_label = Gtk.Label(label="", xalign=0.0)
        self._month_day_label.get_style_context().add_class("omnify-card-title")
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back = Gtk.Button(label="← Week")
        back.set_tooltip_text("Back to the week planner")
        back.connect(
            "clicked", lambda _b: self._stack.set_visible_child_name("week")
        )
        top_row.append(back)
        top_row.append(self._month_day_label)

        self._month_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        month_scroll = Gtk.ScrolledWindow()
        month_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        month_scroll.set_child(self._month_list)
        month_scroll.set_vexpand(True)

        add_row = Gtk.Button(label="+ Add event on this day")
        add_row.connect("clicked", self._on_month_add)

        # Calendar picker on the left, the selected day's agenda on the right.
        calendar_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        calendar_holder.set_size_request(330, -1)
        self._month_calendar.set_valign(Gtk.Align.START)
        calendar_holder.append(self._month_calendar)
        agenda = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        agenda.set_hexpand(True)
        agenda.append(month_scroll)
        agenda.append(add_row)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        body.set_vexpand(True)
        body.append(calendar_holder)
        body.append(agenda)

        page.append(top_row)
        page.append(body)
        return page

# === WEEK RENDERING ===
    def _theme_rgb(self, name: str) -> Tuple[float, float, float]:
        """Theme colour as RGB, with a neutral fallback for headless runs."""
        try:
            from ..core.theme import resolve_color

            theme = getattr(self.ctx, "theme", None)
            if theme is not None:
                rgb = resolve_color(
                    theme.mode, theme.accent, name, getattr(theme, "custom", None)
                )
                if rgb != (0.0, 0.0, 0.0):
                    return rgb
        except Exception:
            pass
        return (0.7, 0.7, 0.7)

    def _refresh_week(self) -> None:
        from gi.repository import Gtk

        start = self._week_start
        days = [start + timedelta(days=offset) for offset in range(7)]
        today = date.today()

        for container in (self._week_header, self._week_allday, self._week_grid):
            while container.get_first_child() is not None:
                container.remove(container.get_first_child())
        self._week_bg_areas = []

        gutter_space = Gtk.Box()
        gutter_space.set_size_request(self._GUTTER_WIDTH, -1)
        self._week_header.append(gutter_space)
        for day in days:
            button = Gtk.Button()
            button.set_size_request(self._DAY_WIDTH, -1)
            label = Gtk.Label(
                label=f"{tr(WEEKDAY_NAMES[day.weekday()])}\n{day.day:02d}",
                justify=Gtk.Justification.CENTER,
            )
            button.set_child(label)
            if day == today:
                button.get_style_context().add_class("omnify-today")
            elif day.weekday() >= 5:
                label.get_style_context().add_class("omnify-weekend")
            button.set_tooltip_text(day.strftime("%A %d %B %Y"))
            button.connect("clicked", self._on_week_day_clicked, day)
            self._week_header.append(button)

        allday_label = Gtk.Label(
            label=tr("all-day"), xalign=1.0, yalign=0.0
        )
        allday_label.set_size_request(self._GUTTER_WIDTH - 8, -1)
        allday_label.set_margin_end(8)
        allday_label.get_style_context().add_class("omnify-dim")
        self._week_allday.append(allday_label)

        per_day = {
            day: self._store.day_events(day.isoformat(), self._visible)
            for day in days
        }

        # The all-day strip: all-day events + habit checkboxes.
        for day in days:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_size_request(self._DAY_WIDTH, -1)
            for event in per_day[day]:
                if event.get("time"):
                    continue
                if event.get("habit"):
                    box.append(self._habit_check(event, day.isoformat()))
                else:
                    box.append(self._event_chip(event, compact=True))
            self._week_allday.append(box)

        # The visible part of the day.
        gutter = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        gutter.set_size_request(self._GUTTER_WIDTH, -1)
        for hour in range(self._day_start_hour, self._day_end_hour):
            label = Gtk.Label(label=f"{hour:02d}:00", xalign=1.0)
            label.set_size_request(-1, self._hour_height)
            label.set_margin_end(8)
            label.set_valign(Gtk.Align.START)
            label.get_style_context().add_class("omnify-dim")
            gutter.append(label)
        self._week_grid.append(gutter)

        total_height = (
            (self._day_end_hour - self._day_start_hour) * self._hour_height
        )
        for day in days:
            overlay = Gtk.Overlay()
            overlay.set_size_request(self._DAY_WIDTH, total_height)
            overlay.set_halign(Gtk.Align.START)
            background = Gtk.DrawingArea()
            background.set_size_request(self._DAY_WIDTH, total_height)
            background.set_draw_func(self._draw_week_background, day)
            overlay.set_child(background)
            self._week_bg_areas.append(background)

            fixed = Gtk.Fixed()
            fixed.set_size_request(self._DAY_WIDTH, total_height)
            timed = [e for e in per_day[day] if e.get("time")]
            for event, x, y, width, height in self._layout_timed(timed):
                chip = self._event_chip(event)
                chip.set_size_request(width, height)
                fixed.put(chip, int(x), int(y))
            overlay.add_overlay(fixed)
            self._week_grid.append(overlay)

        # Open the week around the current time (or 07:30 for other weeks).
        if not self._week_scrolled:
            self._week_scrolled = True
            self._scroll_week_to_now()

    def _scroll_week_to(self, minutes: Optional[int] = None) -> bool:
        """Scroll the week grid so *minutes* sits near the top."""
        if getattr(self, "_week_scroll", None) is not None:
            if minutes is None:
                minutes = 7 * 60 + 30
            adjustment = self._week_scroll.get_vadjustment()
            if adjustment is not None:
                offset = minutes - self._day_start_hour * 60
                value = offset / 60.0 * self._hour_height - 48
                adjustment.set_value(max(0.0, value))
        return False

    def _scroll_week_to_now(self) -> None:
        """Autoscroll the week view to the current time on open/navigation."""
        from gi.repository import GLib

        days = [self._week_start + timedelta(days=offset) for offset in range(7)]
        target = 7 * 60 + 30
        if date.today() in days:
            now = datetime.now()
            target = now.hour * 60 + now.minute - 90
        # A timeout (not an idle) gives the scrolled window one allocation
        # cycle, otherwise ``set_value`` is a no-op while upper == lower.
        GLib.timeout_add(80, self._scroll_week_to, target)

    def _draw_week_background(self, _area, cr, width, height, day: date) -> None:
        """Hour lines, a right border and the current-time marker."""
        border = self._theme_rgb("border")
        cr.set_line_width(1.0)
        cr.set_source_rgba(border[0], border[1], border[2], 0.55)
        visible_hours = self._day_end_hour - self._day_start_hour
        for offset in range(visible_hours + 1):
            y = offset * self._hour_height + 0.5
            cr.move_to(0, y)
            cr.line_to(width, y)
            cr.stroke()
        cr.move_to(width - 0.5, 0)
        cr.line_to(width - 0.5, height)
        cr.stroke()

        if day != date.today():
            return
        now = datetime.now()
        minutes = now.hour * 60 + now.minute
        if not (
            self._day_start_hour * 60
            <= minutes
            <= self._day_end_hour * 60
        ):
            return
        y = (
            (minutes - self._day_start_hour * 60)
            / 60.0
            * self._hour_height
        )
        accent = self._theme_rgb("accent")
        cr.set_source_rgba(accent[0], accent[1], accent[2], 0.95)
        cr.set_line_width(2.0)
        cr.move_to(0, y)
        cr.line_to(width, y)
        cr.stroke()
        cr.arc(4, y, 3.5, 0, 6.2832)
        cr.fill()

    def _layout_timed(
        self, events: List[Dict]
    ) -> List[Tuple[Dict, int, int, int, int]]:
        """Position timed events: y/height by minutes, side-by-side overlaps."""
        visible_start = self._day_start_hour * 60
        visible_end = self._day_end_hour * 60
        entries = []
        for event in events:
            start = parse_minutes(event.get("time"))
            if start is None:
                continue
            end = parse_minutes(event.get("end_time"))
            if end is None or end <= start:
                end = start + 60
            end = max(start + 15, min(end, 24 * 60))
            if end <= visible_start or start >= visible_end:
                continue
            draw_start = max(start, visible_start)
            draw_end = min(end, visible_end)
            entries.append((event, draw_start, draw_end))
        entries.sort(key=lambda item: (item[1], item[2], item[0].get("title", "")))
        columns_end: List[int] = []
        assigned = []
        for event, start, end in entries:
            column = None
            for index, column_end in enumerate(columns_end):
                if column_end <= start:
                    columns_end[index] = end
                    column = index
                    break
            if column is None:
                columns_end.append(end)
                column = len(columns_end) - 1
            assigned.append((event, start, end, column))
        columns = max(len(columns_end), 1)
        width = max(46, (self._DAY_WIDTH - 4) // columns)
        total_height = (
            (self._day_end_hour - self._day_start_hour) * self._hour_height
        )
        out = []
        for event, start, end, column in assigned:
            x = 2 + column * width
            y = round(
                (start - visible_start) / 60.0 * self._hour_height
            )
            height = max(
                22, round((end - start) / 60.0 * self._hour_height)
            )
            if y + height > total_height:
                height = max(22, total_height - y)
            out.append((event, x, y, width - 2, height))
        return out

    def _event_color_name(self, event: Dict) -> str:
        """Palette colour name for an event's category layer."""
        category = event.get("category") or ""
        return self._cat_colors.get(category, "grey") if category else "grey"

    def _event_chip(self, event: Dict, compact: bool = False):
        """A clickable event block coloured by its layer."""
        from gi.repository import Gtk

        time_text = ""
        if event.get("time"):
            time_text = event["time"]
            if event.get("end_time"):
                time_text += f"–{event['end_time']}"
        if compact or not time_text:
            text = event.get("title") or "(untitled)"
        else:
            text = f"{event.get('title') or '(untitled)'}\n{time_text}"
        button = Gtk.Button()
        label = Gtk.Label(label=text, xalign=0.0, yalign=0.0)
        label.set_wrap(True)
        label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        button.set_child(label)
        button.get_style_context().add_class("omnify-event")
        self._apply_event_layer(button, event)
        tooltip = event.get("title") or ""
        if time_text:
            tooltip += f"\n{time_text}"
        if event.get("notes"):
            tooltip += f"\n{event['notes']}"
        if event.get("habit"):
            tooltip += "\nHabit — tick it in the tracker"
        button.set_tooltip_text(tooltip)
        button.connect(
            "clicked",
            lambda _b: self._open_event_dialog(
                event_id=event.get("id"),
                date_iso=event.get("occurrence_date"),
            ),
        )
        return button

    def _habit_check(self, event: Dict, iso_date: str):
        from gi.repository import Gtk

        check = Gtk.CheckButton(label=event.get("title") or "")
        check.set_active(self._store.is_done(event["id"], iso_date))
        check.set_tooltip_text(f"{event.get('title')} — {iso_date}")
        check.connect("toggled", self._on_habit_toggled, event["id"], iso_date)
        return check

    def _on_habit_toggled(self, check, event_id: str, iso_date: str) -> None:
        from gi.repository import GLib

        before = self._snapshot()
        try:
            self._store.set_done(event_id, iso_date, check.get_active())
        except (KeyError, ValueError) as exc:
            self.ctx.notify(str(exc))
            return
        self._record_history(
            "Tick habit" if check.get_active() else "Untick habit", before
        )
        # Rebuilding widgets from inside their own signal is unsafe.
        GLib.idle_add(self._refresh_current)

    def _on_week_day_clicked(self, _button, day: date) -> None:
        self._select_month_day(day)
        self._stack.set_visible_child_name("month")

# === MONTH / NAVIGATION ===
    def _calendar_iso(self) -> str:
        dt = self._month_calendar.get_date()
        return (
            f"{dt.get_year():04d}-{dt.get_month():02d}-"
            f"{dt.get_day_of_month():02d}"
        )

    def _select_month_day(self, day: date) -> None:
        from gi.repository import GLib

        self._selected_day = day
        self._month_calendar.select_day(
            GLib.DateTime.new_local(day.year, day.month, day.day, 0, 0, 0)
        )

    def _on_month_day_selected(self, _calendar) -> None:
        try:
            self._selected_day = date.fromisoformat(self._calendar_iso())
        except ValueError:
            return
        if self._stack.get_visible_child_name() == "month":
            self._refresh_month()

    def _on_month_add(self, _button) -> None:
        self._open_event_dialog(date_iso=self._calendar_iso())

    def _refresh_month(self) -> None:
        from gi.repository import Gtk

        iso = self._calendar_iso()
        try:
            self._selected_day = date.fromisoformat(iso)
        except ValueError:
            return
        dt = self._month_calendar.get_date()
        self._month_day_label.set_label(
            f"{dt.get_day_of_month():02d} {dt.format('%B')} {dt.get_year()}"
        )
        while self._month_list.get_first_child() is not None:
            self._month_list.remove(self._month_list.get_first_child())

        events = self._store.day_events(iso, self._visible)
        timed = [event for event in events if event.get("time")]
        total_minutes = 0
        for event in timed:
            start = parse_minutes(event.get("time"))
            end = parse_minutes(event.get("end_time"))
            if start is None:
                continue
            total_minutes += max(30, (end if end is not None else start + 60) - start)
        parts = [
            f"{len(events)} event{'s' if len(events) != 1 else ''}"
        ]
        if total_minutes:
            hours, minutes = divmod(total_minutes, 60)
            parts.append(
                f"{hours}h {minutes:02d}m planned"
                if hours
                else f"{minutes}m planned"
            )
        all_day = len(events) - len(timed)
        if all_day:
            parts.append(f"{all_day} all-day")
        habits = sum(1 for event in events if event.get("habit"))
        if habits:
            parts.append(f"{habits} habit{'s' if habits != 1 else ''}")

        overview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        overview.get_style_context().add_class("omnify-card")
        overview_title = Gtk.Label(
            label=(
                "Nothing scheduled"
                if not events
                else "Day overview"
            ),
            xalign=0.0,
        )
        overview_title.get_style_context().add_class("omnify-card-title")
        overview.append(overview_title)
        summary = Gtk.Label(label=" · ".join(parts), xalign=0.0)
        summary.get_style_context().add_class("omnify-dim")
        overview.append(summary)

        if not events:
            hint = Gtk.Label(
                label=(
                    "This day is free. Add an event, start a habit, or "
                    "jump back to the week planner."
                ),
                xalign=0.0,
            )
            hint.set_wrap(True)
            hint.get_style_context().add_class("omnify-dim")
            overview.append(hint)
            quick = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            new_event = Gtk.Button(label="+ Event")
            new_event.get_style_context().add_class("omnify-accent")
            new_event.connect(
                "clicked",
                lambda _b: self._open_event_dialog(date_iso=iso),
            )
            new_habit = Gtk.Button(label="+ Habit")
            new_habit.connect(
                "clicked",
                lambda _b: self._open_event_dialog(
                    date_iso=iso, habit=True
                ),
            )
            to_week = Gtk.Button(label="Open week")
            to_week.connect(
                "clicked",
                lambda _b: self._stack.set_visible_child_name("week"),
            )
            quick.append(new_event)
            quick.append(new_habit)
            quick.append(to_week)
            overview.append(quick)
        self._month_list.append(overview)

        for event in events:
            self._month_list.append(self._month_row(event, iso))

        if not events:
            try:
                selected = date.fromisoformat(iso)
            except ValueError:
                selected = date.today()
            upcoming = self._store.occurrences_between(
                (selected + timedelta(days=1)).isoformat(),
                (selected + timedelta(days=14)).isoformat(),
                self._visible,
            )[:5]
            if upcoming:
                heading = Gtk.Label(label=tr("Coming up"), xalign=0.0)
                heading.get_style_context().add_class("omnify-card-title")
                heading.set_margin_top(6)
                self._month_list.append(heading)
                for event in upcoming:
                    when = event.get("occurrence_date") or event.get("date")
                    row = Gtk.Button()
                    line = Gtk.Box(
                        orientation=Gtk.Orientation.HORIZONTAL, spacing=8
                    )
                    when_label = Gtk.Label(
                        label=f"{when} · {format_event_line(event)}",
                        xalign=0.0,
                        hexpand=True,
                    )
                    line.append(when_label)
                    row.set_child(line)
                    row.connect(
                        "clicked",
                        lambda _b, ev=event: self._open_event_dialog(
                            event_id=ev.get("id"),
                            date_iso=ev.get("occurrence_date"),
                        ),
                    )
                    self._month_list.append(row)

    def _month_row(self, event: Dict, iso_date: str):
        from gi.repository import Gtk

        button = Gtk.Button()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dot = Gtk.Box()
        dot.get_style_context().add_class("omnify-swatch")
        self._apply_layer_dot(
            dot, self._cat_map.get(event.get("category") or "")
        )
        dot.set_valign(Gtk.Align.CENTER)
        row.append(dot)
        label = Gtk.Label(
            label=format_event_line(event), xalign=0.0, hexpand=True
        )
        row.append(label)
        if event.get("habit"):
            check = Gtk.CheckButton()
            check.set_active(self._store.is_done(event["id"], iso_date))
            check.connect(
                "toggled", self._on_habit_toggled, event["id"], iso_date
            )
            row.append(check)
        if event.get("recurrence"):
            badge = Gtk.Label(label="↻")
            badge.get_style_context().add_class("omnify-dim")
            row.append(badge)
        button.set_child(row)
        button.connect(
            "clicked",
            lambda _b: self._open_event_dialog(
                event_id=event.get("id"), date_iso=iso_date
            ),
        )
        return button

    # -- navigation ----------------------------------------------------
    def _on_page_changed(self, *_args) -> None:
        self._refresh_current()
        if self._stack.get_visible_child_name() == "week":
            self._scroll_week_to_now()

    def _navigate(self, direction: int) -> None:
        page = self._stack.get_visible_child_name()
        if page == "week":
            self._week_start += timedelta(days=7 * direction)
        elif page == "habits":
            self._habit_week += timedelta(days=7 * direction)
        elif page == "month":
            # This page is a day view with a month picker: arrows move the
            # selected *day* (the calendar widget changes months).
            self._select_month_day(
                self._selected_day + timedelta(days=direction)
            )
            return
        else:
            return
        self._refresh_current()
        if page == "week":
            self._scroll_week_to_now()

    def _shift_month(self, direction: int) -> None:
        """Kept for programmatic month jumps (the calendar has arrows too)."""
        from gi.repository import GLib

        dt = self._month_calendar.get_date()
        target = dt.add_months(direction)
        self._month_calendar.select_day(
            GLib.DateTime.new_local(
                target.get_year(), target.get_month(), 1, 0, 0, 0
            )
        )

    def _go_today(self) -> None:
        today = date.today()
        self._week_start = week_start(today, self._prefs["week_start"])
        self._habit_week = self._week_start
        self._select_month_day(today)
        self._refresh_current()
        if self._stack.get_visible_child_name() == "week":
            self._scroll_week_to_now()

    def _refresh_range_label(self, page: Optional[str] = None) -> None:
        page = page or self._stack.get_visible_child_name()
        if page == "week":
            start = self._week_start
            end = start + timedelta(days=6)
            self._range_label.set_label(
                f"{start.day} {_month_name(start.month)} – "
                f"{end.day} {_month_name(end.month)} {end.year}"
            )
        elif page == "month":
            day = self._selected_day
            self._range_label.set_label(
                f"{day.day} {_month_name(day.month)} {day.year}"
            )
        elif page == "habits":
            start = self._habit_week
            end = start + timedelta(days=6)
            self._range_label.set_label(
                f"{tr('Habits')} · {start.day} {_month_name(start.month)} – "
                f"{end.day} {_month_name(end.month)}"
            )
        elif page == "stats":
            today = date.today()
            self._range_label.set_label(
                f"{tr('Statistics')} · {_month_name(today.month)} {today.year}"
            )
        elif page == "search":
            self._range_label.set_label(tr("Search and filters"))
        else:
            self._range_label.set_label(tr("Goals"))

    def _refresh_current(self) -> None:
        if getattr(self, "_stack", None) is None:
            return
        page = self._stack.get_visible_child_name()
        self._refresh_range_label(page)
        if page == "week":
            self._refresh_week()
        elif page == "month":
            self._refresh_month()
        elif page == "habits":
            self._refresh_habits()
        elif page == "stats":
            self._refresh_stats()
        elif page == "goals":
            self._refresh_goals()
        elif page == "search":
            self._refresh_search()

# === HABITS ===
    def _build_habits_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        info = Gtk.Label(
            label=(
                "Tick a habit for every day it is due. The last column is the "
                "week's completion percentage."
            ),
            xalign=0.0,
        )
        info.get_style_context().add_class("omnify-dim")
        page.append(info)
        self._habits_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._habits_box)
        scroll.set_vexpand(True)
        page.append(scroll)
        return page

    def _refresh_habits(self) -> None:
        from gi.repository import Gtk

        while self._habits_box.get_first_child() is not None:
            self._habits_box.remove(self._habits_box.get_first_child())
        days = [self._habit_week + timedelta(days=offset) for offset in range(7)]
        habits = [
            habit for habit in self._store.habits()
            if self._layer_visible(habit)
        ]
        habits.sort(key=lambda h: (h.get("title") or "").lower())
        if not habits:
            label = Gtk.Label(
                label=(
                    "No habits yet. Press “+ Habit” in the header to add one "
                    "(for example “Brush teeth”)."
                ),
                xalign=0.0,
            )
            label.get_style_context().add_class("omnify-dim")
            label.set_margin_top(10)
            self._habits_box.append(label)
            return
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_halign(Gtk.Align.START)
        heading = Gtk.Label(label="Habit", xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        grid.attach(heading, 0, 0, 1, 1)
        for index, day in enumerate(days):
            label = Gtk.Label(
                label=f"{tr(WEEKDAY_NAMES[day.weekday()])}\n{day.day:02d}",
                justify=Gtk.Justification.CENTER,
            )
            label.set_size_request(46, -1)
            if day == date.today():
                label.get_style_context().add_class("omnify-today")
            elif day.weekday() >= 5:
                label.get_style_context().add_class("omnify-weekend")
            grid.attach(label, index + 1, 0, 1, 1)
        percent_heading = Gtk.Label(label="Week %")
        percent_heading.get_style_context().add_class("omnify-card-title")
        grid.attach(percent_heading, 8, 0, 1, 1)

        for row, habit in enumerate(habits, start=1):
            name = Gtk.Label(label=habit.get("title") or "", xalign=0.0)
            name.set_size_request(170, -1)
            name.set_hexpand(True)
            grid.attach(name, 0, row, 1, 1)
            for index, day in enumerate(days):
                iso = day.isoformat()
                if not occurs_on(habit, day):
                    dash = Gtk.Label(label="·")
                    dash.get_style_context().add_class("omnify-dim")
                    grid.attach(dash, index + 1, row, 1, 1)
                    continue
                check = Gtk.CheckButton()
                check.set_halign(Gtk.Align.CENTER)
                check.set_active(self._store.is_done(habit["id"], iso))
                check.connect(
                    "toggled", self._on_habit_toggled, habit["id"], iso
                )
                grid.attach(check, index + 1, row, 1, 1)
            stats = self._store.habit_stats(
                habit["id"], days[0].isoformat(), days[-1].isoformat()
            )
            percent = Gtk.Label(label=f"{stats['percent']}%")
            percent.set_size_request(56, -1)
            grid.attach(percent, 8, row, 1, 1)

        self._habits_box.append(grid)
        overall = self._store.stats_between(
            days[0].isoformat(), days[-1].isoformat(), self._visible
        )
        summary = Gtk.Label(
            label=(
                f"This week: {overall['done']} of {overall['total']} done "
                f"({overall['percent']}%), {overall['missed']} missed."
            ),
            xalign=0.0,
        )
        summary.get_style_context().add_class("omnify-dim")
        summary.set_margin_top(8)
        self._habits_box.append(summary)

    def _layer_visible(self, event: Dict) -> bool:
        category = event.get("category") or ""
        return not category or category in self._visible

# === STATS ===
    def _build_stats_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        cards = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for key, title in (
            ("total", "Habits scheduled"),
            ("done", "Completed"),
            ("missed", "Missed"),
            ("cancelled", "Cancelled"),
            ("percent", "Completion"),
        ):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card.get_style_context().add_class("omnify-card")
            card.set_hexpand(True)
            value = Gtk.Label(label="0", xalign=0.0)
            value.get_style_context().add_class("omnify-big-value")
            caption = Gtk.Label(label=title, xalign=0.0)
            caption.get_style_context().add_class("omnify-dim")
            card.append(value)
            card.append(caption)
            self._stat_labels[key] = value
            cards.append(card)
        page.append(cards)

        chart_title = Gtk.Label(
            label="Last 7 days — scheduled (grey) vs completed (green)",
            xalign=0.0,
        )
        chart_title.get_style_context().add_class("omnify-card-title")
        page.append(chart_title)
        self._stats_chart = Gtk.DrawingArea()
        self._stats_chart.set_size_request(-1, 170)
        self._stats_chart.set_hexpand(True)
        self._stats_chart.set_draw_func(self._draw_stats_chart)
        page.append(self._stats_chart)
        self._chart_labels = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=0
        )
        self._chart_labels.set_homogeneous(True)
        page.append(self._chart_labels)

        contribution_title = Gtk.Label(
            label="Occupancy — last 12 months (darker = busier)",
            xalign=0.0,
        )
        contribution_title.get_style_context().add_class("omnify-card-title")
        page.append(contribution_title)
        self._contrib_chart = Gtk.DrawingArea()
        self._contrib_chart.set_size_request(-1, 104)
        self._contrib_chart.set_hexpand(True)
        self._contrib_chart.set_draw_func(self._draw_contrib_chart)
        page.append(self._contrib_chart)

        self._category_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        page.append(self._category_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(page)
        scroll.set_vexpand(True)
        return scroll

    def _habit_day_counts(
        self, start: date, end: date
    ) -> Dict[str, Tuple[int, int]]:
        """``{iso: (scheduled, done)}`` for habit occurrences in a range."""
        days = list(iter_days(start, end))
        totals: Dict[str, List[int]] = {
            day.isoformat(): [0, 0] for day in days
        }
        for habit in self._store.habits():
            if not self._layer_visible(habit):
                continue
            done = set(habit.get("done") or [])
            for day in days:
                if not occurs_on(habit, day):
                    continue
                iso = day.isoformat()
                totals[iso][0] += 1
                if iso in done:
                    totals[iso][1] += 1
        return {iso: (value[0], value[1]) for iso, value in totals.items()}

    def _draw_stats_chart(self, _area, cr, width, height) -> None:
        """Seven grouped bars: scheduled (grey) and completed (green)."""
        today = date.today()
        start = today - timedelta(days=6)
        counts = self._habit_day_counts(start, today)
        days = [start + timedelta(days=offset) for offset in range(7)]
        margin_x, margin_top, margin_bottom = 14, 10, 12
        chart_height = max(20, height - margin_top - margin_bottom)
        slot = (width - 2 * margin_x) / 7.0
        bar_width = max(5, slot / 2 - 5)
        max_total = max(
            [1] + [total for total, _done in counts.values()]
        )
        border = self._theme_rgb("border")
        background = self._theme_rgb("bg-hover")
        success = self._theme_rgb("success")

        cr.set_line_width(1.0)
        for fraction in (0.25, 0.5, 0.75, 1.0):
            y = margin_top + chart_height * (1 - fraction)
            cr.set_source_rgba(border[0], border[1], border[2], 0.35)
            cr.move_to(margin_x, y)
            cr.line_to(width - margin_x, y)
            cr.stroke()

        for index, day in enumerate(days):
            total, done = counts.get(day.isoformat(), (0, 0))
            offset_x = margin_x + index * slot + (slot - bar_width * 2 - 4) / 2
            if total:
                bar_height = chart_height * total / max_total
                cr.set_source_rgba(
                    background[0], background[1], background[2], 0.95
                )
                cr.rectangle(
                    offset_x,
                    margin_top + chart_height - bar_height,
                    bar_width,
                    bar_height,
                )
                cr.fill()
            if done:
                bar_height = chart_height * done / max_total
                cr.set_source_rgba(success[0], success[1], success[2], 0.95)
                cr.rectangle(
                    offset_x + bar_width + 4,
                    margin_top + chart_height - bar_height,
                    bar_width,
                    bar_height,
                )
                cr.fill()

        cr.set_source_rgba(border[0], border[1], border[2], 0.7)
        cr.set_line_width(1.0)
        cr.move_to(margin_x, margin_top + chart_height + 0.5)
        cr.line_to(width - margin_x, margin_top + chart_height + 0.5)
        cr.stroke()

    def _draw_contrib_chart(self, _area, cr, width, height) -> None:
        """GitHub-style occupancy grid for the last 12 months."""
        today = date.today()
        weeks = 53
        start = week_start(today) - timedelta(days=7 * (weeks - 1))
        last = min(start + timedelta(days=weeks * 7 - 1), today)
        counts = dict(
            self._store.daily_counts(
                start.isoformat(), last.isoformat(), self._visible
            )
        )
        cell, gap = 10, 2
        pitch = cell + gap
        left, top = 4, 4
        background = self._theme_rgb("bg-hover")
        accent = self._theme_rgb("accent")
        for week in range(weeks):
            for weekday in range(7):
                day = start + timedelta(days=week * 7 + weekday)
                if day > today:
                    continue
                count = counts.get(day.isoformat(), 0)
                if count <= 0:
                    color, alpha = background, 0.8
                elif count == 1:
                    color, alpha = accent, 0.22
                elif count == 2:
                    color, alpha = accent, 0.45
                elif count == 3:
                    color, alpha = accent, 0.70
                else:
                    color, alpha = accent, 1.0
                cr.set_source_rgba(color[0], color[1], color[2], alpha)
                cr.rectangle(
                    left + week * pitch, top + weekday * pitch, cell, cell
                )
                cr.fill()

    def _refresh_stats(self) -> None:
        from gi.repository import Gtk

        today = date.today()
        month_start = today.replace(day=1)
        month_end = date(
            today.year,
            today.month,
            _calendar.monthrange(today.year, today.month)[1],
        )
        stats = self._store.stats_between(
            month_start.isoformat(),
            month_end.isoformat(),
            self._visible,
        )
        for key, label in self._stat_labels.items():
            label.set_label(
                f"{stats[key]}%" if key == "percent" else str(stats[key])
            )
        self._stats_chart.queue_draw()
        self._contrib_chart.queue_draw()

        while self._chart_labels.get_first_child() is not None:
            self._chart_labels.remove(self._chart_labels.get_first_child())
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            label = Gtk.Label(
                label=f"{tr(WEEKDAY_NAMES[day.weekday()])} {day.day}"
            )
            label.get_style_context().add_class("omnify-dim")
            self._chart_labels.append(label)

        while self._category_box.get_first_child() is not None:
            self._category_box.remove(self._category_box.get_first_child())
        heading = Gtk.Label(label="By layer (this month)", xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        self._category_box.append(heading)
        counts = self._store.category_counts(
            month_start.isoformat(),
            month_end.isoformat(),
            self._visible,
        )
        total = sum(counts.values()) or 1
        for category in self._cats.all():
            count = counts.get(category["id"], 0)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            dot = Gtk.Box()
            dot.get_style_context().add_class("omnify-swatch")
            self._apply_layer_dot(dot, category)
            dot.set_valign(Gtk.Align.CENTER)
            row.append(dot)
            name = Gtk.Label(
                label=category.get("name") or category["id"],
                xalign=0.0,
                hexpand=True,
            )
            row.append(name)
            value = Gtk.Label(
                label=f"{count}  ·  {round(100 * count / total)}%"
            )
            value.get_style_context().add_class("omnify-dim")
            row.append(value)
            self._category_box.append(row)

# === GOALS ===
    def _build_goals_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add = Gtk.Button(label="+ New goal")
        add.get_style_context().add_class("omnify-accent")
        add.connect("clicked", lambda _b: self._open_goal_dialog())
        toolbar.append(add)
        hint = Gtk.Label(
            label=(
                "Track a numeric target or a checklist — progress updates "
                "as you tick items."
            ),
            xalign=1.0,
            hexpand=True,
        )
        hint.get_style_context().add_class("omnify-dim")
        toolbar.append(hint)
        page.append(toolbar)

        self._goals_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._goals_box)
        scroll.set_vexpand(True)
        page.append(scroll)
        return page

# === SEARCH ===
    def _build_search_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(
            "Search titles and notes…"
        )
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        controls.append(self._search_entry)

        self._search_layer = Gtk.ComboBoxText()
        self._search_layer.set_tooltip_text("Filter by event layer")
        self._search_layer.connect("changed", self._on_search_changed)
        controls.append(self._search_layer)

        self._search_scope = Gtk.ComboBoxText()
        self._search_scope.append("all", tr("All events"))
        self._search_scope.append("upcoming", tr("Upcoming"))
        self._search_scope.append("past", tr("Past"))
        self._search_scope.append("recurring", tr("Recurring"))
        self._search_scope.set_active_id("all")
        self._search_scope.set_tooltip_text("Filter by time scope")
        self._search_scope.connect("changed", self._on_search_changed)
        controls.append(self._search_scope)
        page.append(controls)

        self._search_count = Gtk.Label(label="", xalign=0.0)
        self._search_count.get_style_context().add_class("omnify-dim")
        page.append(self._search_count)

        self._search_results = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._search_results)
        scroll.set_vexpand(True)
        page.append(scroll)
        self._search_populating = False
        return page

    def _on_search_changed(self, *_args) -> None:
        if getattr(self, "_search_populating", False):
            return
        if self._stack.get_visible_child_name() != "search":
            return
        self._refresh_search()

    def _refresh_search(self) -> None:
        from gi.repository import Gtk

        if getattr(self, "_search_entry", None) is None:
            return
        self._search_populating = True
        try:
            active = self._search_layer.get_active_id() or ""
            self._search_layer.remove_all()
            self._search_layer.append("", "All layers")
            for category in self._cats.all():
                self._search_layer.append(
                    category["id"], category.get("name") or category["id"]
                )
            self._search_layer.set_active_id(active)
        finally:
            self._search_populating = False

        query = self._search_entry.get_text()
        category = self._search_layer.get_active_id() or ""
        scope = self._search_scope.get_active_id() or "all"
        events = filter_events(
            self._store.all_events(),
            query=query,
            category=category,
            scope=scope,
        )
        while self._search_results.get_first_child() is not None:
            self._search_results.remove(
                self._search_results.get_first_child()
            )
        total = len(events)
        if total == 1:
            count_text = tr("{count} matching event", count=total)
        else:
            count_text = tr("{count} matching events", count=total)
        if total > 300:
            count_text += " " + tr("(showing the first 300)")
        self._search_count.set_label(count_text)
        if not events:
            empty = Gtk.Label(
                label=(
                    "Nothing found. Try another phrase, layer or scope."
                ),
                xalign=0.0,
            )
            empty.get_style_context().add_class("omnify-dim")
            empty.set_margin_top(8)
            self._search_results.append(empty)
            return
        layers = {
            category["id"]: category.get("name") or category["id"]
            for category in self._cats.all()
        }
        for event in events[:300]:
            try:
                day = date.fromisoformat(event.get("date") or "")
            except ValueError:
                continue
            text = f"{day.strftime('%a, %d %b %Y')} · {format_event_line(event)}"
            row = Gtk.Button()
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            dot = Gtk.Box()
            dot.get_style_context().add_class("omnify-swatch")
            self._apply_layer_dot(
                dot, self._cat_map.get(event.get("category") or "")
            )
            dot.set_valign(Gtk.Align.CENTER)
            line.append(dot)
            label = Gtk.Label(label=text, xalign=0.0, hexpand=True)
            label.set_wrap(True)
            line.append(label)
            layer_name = layers.get(event.get("category") or "", "")
            if layer_name:
                layer_label = Gtk.Label(label=layer_name)
                layer_label.get_style_context().add_class("omnify-dim")
                line.append(layer_label)
            if event.get("recurrence"):
                repeat = Gtk.Label(label="↻")
                repeat.get_style_context().add_class("omnify-dim")
                line.append(repeat)
            row.set_child(line)
            row.connect(
                "clicked",
                lambda _b, ev=event: self._open_event_dialog(
                    event_id=ev.get("id"), date_iso=ev.get("date")
                ),
            )
            self._search_results.append(row)

    def _refresh_goals(self) -> None:
        from gi.repository import Gtk

        while self._goals_box.get_first_child() is not None:
            self._goals_box.remove(self._goals_box.get_first_child())
        goals = self._goals.all()
        if not goals:
            label = Gtk.Label(
                label=(
                    "No goals yet. Press “+ New goal” to add one — for "
                    "example “Read 12 books” or a checklist for a project."
                ),
                xalign=0.0,
            )
            label.get_style_context().add_class("omnify-dim")
            label.set_margin_top(10)
            self._goals_box.append(label)
            return
        for goal in goals:
            self._goals_box.append(self._goal_card(goal))

    def _goal_card(self, goal: Dict):
        from gi.repository import Gtk

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("omnify-card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=goal.get("title") or "(goal)", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        title.set_hexpand(True)
        header.append(title)
        if goal.get("deadline"):
            deadline = Gtk.Label(
                label=tr("by {date}", date=goal["deadline"])
            )
            deadline.get_style_context().add_class("omnify-dim")
            header.append(deadline)
        edit = Gtk.Button(label="Edit")
        edit.connect(
            "clicked",
            lambda _b, gid=goal["id"]: self._open_goal_dialog(gid),
        )
        header.append(edit)
        delete = Gtk.Button(label="✕")
        delete.get_style_context().add_class("omnify-danger")
        delete.set_tooltip_text("Delete this goal")
        delete.connect(
            "clicked", lambda _b, gid=goal["id"]: self._delete_goal(gid)
        )
        header.append(delete)
        card.append(header)

        done, total, percent = self._goals.progress(goal)
        progress = Gtk.ProgressBar()
        progress.set_fraction(percent / 100.0)
        progress.set_show_text(False)
        card.append(progress)
        unit = goal.get("unit") or ""
        summary = f"{done}/{total}" + (f" {unit}" if unit else "")
        if total:
            summary += f"  ·  {percent}%"
        summary_label = Gtk.Label(label=summary, xalign=0.0)
        summary_label.get_style_context().add_class("omnify-dim")
        card.append(summary_label)

        items = goal.get("items") or []
        for index, item in enumerate(items):
            check = Gtk.CheckButton(label=item.get("text") or "")
            check.set_active(bool(item.get("done")))
            check.connect(
                "toggled", self._on_goal_item_toggled, goal["id"], index
            )
            card.append(check)
        if not items and goal.get("target"):
            increment = Gtk.Button(label="+1")
            increment.set_halign(Gtk.Align.START)
            increment.connect(
                "clicked", self._on_goal_increment, goal["id"]
            )
            card.append(increment)

        item_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Add checklist item…")
        entry.set_hexpand(True)
        add_item = Gtk.Button(label="Add")
        add_item.connect(
            "clicked", self._on_goal_add_item, goal["id"], entry
        )
        entry.connect(
            "activate",
            lambda _e, gid=goal["id"], ent=entry: self._on_goal_add_item(
                None, gid, ent
            ),
        )
        item_row.append(entry)
        item_row.append(add_item)
        card.append(item_row)
        return card

    def _on_goal_item_toggled(self, _check, goal_id: str, index: int) -> None:
        from gi.repository import GLib

        before = self._snapshot()
        try:
            self._goals.toggle_item(goal_id, index)
        except (KeyError, IndexError) as exc:
            self.ctx.notify(str(exc))
            return
        self._record_history("Tick goal item", before)
        GLib.idle_add(self._refresh_goals)

    def _on_goal_add_item(self, _button, goal_id: str, entry) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        before = self._snapshot()
        try:
            self._goals.add_item(goal_id, text)
        except (KeyError, ValueError) as exc:
            self.ctx.notify(str(exc))
            return
        self._record_history("Add goal item", before)
        entry.set_text("")
        self._refresh_goals()

    def _on_goal_increment(self, _button, goal_id: str) -> None:
        before = self._snapshot()
        try:
            goal = self._goals.get(goal_id)
            target = int(goal.get("target") or 0)
            current = min(int(goal.get("current") or 0) + 1, target)
            self._goals.update(goal_id, current=current)
        except (KeyError, ValueError) as exc:
            self.ctx.notify(str(exc))
            return
        self._record_history("Update goal", before)
        self._refresh_goals()

    def _delete_goal(self, goal_id: str) -> None:
        before = self._snapshot()
        self._goals.remove(goal_id)
        self._record_history("Delete goal", before)
        self._refresh_goals()

    # -- goal dialog ---------------------------------------------------
    def _open_goal_dialog(self, goal_id: Optional[str] = None) -> None:
        from gi.repository import Gtk

        if self._goal_dialog is not None:
            dialog = self._goal_dialog
            if dialog.get_visible() and dialog.get_realized():
                dialog.present()
                return
            try:
                dialog.destroy()
            except Exception:
                pass
            self._goal_dialog = None
        editing = None
        if goal_id:
            try:
                editing = self._goals.get(goal_id)
            except KeyError:
                editing = None
        win = Gtk.Window()
        win.set_title("Edit goal" if editing else "New goal")
        win.set_default_size(420, -1)
        win.set_modal(True)
        main = self._root.get_root()
        if main is not None:
            win.set_transient_for(main)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        title = Gtk.Entry()
        title.set_hexpand(True)
        title.set_text((editing or {}).get("title", ""))
        target = Gtk.SpinButton.new_with_range(0, 100000, 1)
        target.set_value((editing or {}).get("target", 0) or 0)
        unit = Gtk.Entry()
        unit.set_text((editing or {}).get("unit", "") or "")
        deadline = Gtk.Entry()
        deadline.set_placeholder_text("YYYY-MM-DD (optional)")
        deadline.set_text((editing or {}).get("deadline", "") or "")
        for row, (label_text, widget) in enumerate(
            (
                ("Title", title),
                ("Target", target),
                ("Unit", unit),
                ("Deadline", deadline),
            )
        ):
            label = Gtk.Label(label=label_text, xalign=0.0)
            label.get_style_context().add_class("omnify-dim")
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        box.append(grid)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("omnify-accent")
        cancel = Gtk.Button(label="Cancel")
        buttons.append(save)
        buttons.append(cancel)
        if editing:
            delete = Gtk.Button(label="Delete")
            delete.get_style_context().add_class("omnify-danger")
            delete.connect("clicked", lambda _b, gid=goal_id: self._remove_goal_from_dialog(gid))
            buttons.append(delete)
        box.append(buttons)
        win.set_child(box)

        def on_save(_button) -> None:
            before = self._snapshot()
            try:
                if editing:
                    self._goals.update(
                        goal_id,
                        title=title.get_text(),
                        target=int(target.get_value()),
                        unit=unit.get_text().strip(),
                        deadline=deadline.get_text().strip(),
                    )
                else:
                    self._goals.add(
                        title.get_text(),
                        int(target.get_value()),
                        unit.get_text().strip(),
                        deadline.get_text().strip(),
                    )
            except (ValueError, KeyError) as exc:
                self.ctx.notify(str(exc))
                return
            self._record_history(
                "Edit goal" if editing else "Add goal", before
            )
            self._close_goal_dialog()
            self._refresh_goals()

        save.connect("clicked", on_save)
        cancel.connect("clicked", lambda _b: self._close_goal_dialog())
        win.set_hide_on_close(False)
        win.get_style_context().add_class("omnify-dialog")
        win.connect("close-request", self._on_goal_dialog_close_request)
        win.connect("destroy", self._on_goal_dialog_destroyed)
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_dialog_key_pressed)
        win.add_controller(controller)
        self._goal_dialog = win
        win.present()

        def focus_title() -> bool:
            if (
                self._goal_dialog is win
                and win.get_realized()
                and win.get_visible()
            ):
                title.grab_focus()
            return False

        from gi.repository import GLib

        GLib.idle_add(focus_title)

    def _remove_goal_from_dialog(self, goal_id: str) -> None:
        before = self._snapshot()
        self._goals.remove(goal_id)
        self._record_history("Delete goal", before)
        self._close_goal_dialog()
        self._refresh_goals()

    def _close_goal_dialog(self) -> None:
        if self._goal_dialog is not None:
            dialog, self._goal_dialog = self._goal_dialog, None
            dialog.destroy()

    def _on_goal_dialog_close_request(self, _window) -> bool:
        self._goal_dialog = None
        return False

    def _on_goal_dialog_destroyed(self, _window) -> None:
        self._goal_dialog = None

# === EVENT DIALOG ===
    def _open_event_dialog(
        self,
        event_id: Optional[str] = None,
        date_iso: Optional[str] = None,
        habit: bool = False,
        prefill: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create/edit an event (or habit) in a modal form."""
        from gi.repository import Gtk

        if self._dialog is not None:
            dialog = self._dialog
            if dialog.get_visible() and dialog.get_realized():
                dialog.present()
                return
            # A destroyed/unrealized window must never be presented again:
            # it renders as an empty transparent modal that eats all input.
            try:
                dialog.destroy()
            except Exception:
                pass
            self._dialog = None
        editing = None
        if event_id:
            try:
                editing = self._store.get(event_id)
            except KeyError:
                editing = None
        data = editing or prefill or {}
        target_date = (
            date_iso
            or data.get("occurrence_date")
            or data.get("date")
            or date.today().isoformat()
        )
        start_text = data.get("time") or "09:00"
        start_minutes = parse_minutes(start_text)
        if start_minutes is None:
            start_minutes = 9 * 60
        end_minutes = parse_minutes(data.get("end_time"))
        if end_minutes is None or end_minutes <= start_minutes:
            end_minutes = min(start_minutes + 60, 23 * 60 + 59)
        record = data.get("recurrence") or {}

        win = Gtk.Window()
        win.set_title(
            "Edit event"
            if editing
            else ("New habit" if habit else "New event")
        )
        win.set_default_size(580, 760)
        win.set_modal(True)
        main = self._root.get_root()
        if main is not None:
            win.set_transient_for(main)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(16)
        outer.set_margin_bottom(16)
        outer.set_margin_start(16)
        outer.set_margin_end(16)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        form.set_margin_end(8)
        scroll.set_child(form)
        outer.append(scroll)

        def dim_label(text: str):
            label = Gtk.Label(label=text, xalign=0.0)
            label.get_style_context().add_class("omnify-dim")
            return label

        details = Gtk.Grid(column_spacing=10, row_spacing=8)
        title_entry = Gtk.Entry()
        title_entry.set_hexpand(True)
        title_entry.set_placeholder_text("What is happening?")
        title_entry.set_text(data.get("title", "") or "")
        date_value = [target_date]
        date_button = Gtk.MenuButton(label=target_date)
        date_button.set_tooltip_text("Click to pick a date from the calendar")
        date_button.set_always_show_arrow(False)
        date_button.set_hexpand(True)
        date_button.set_halign(Gtk.Align.FILL)
        date_button.get_style_context().add_class("omnify-date-field")
        picker = Gtk.Calendar()
        manual_date = Gtk.Entry()
        manual_date.set_placeholder_text("YYYY-MM-DD")
        manual_date.set_text(target_date)
        manual_date.set_hexpand(True)
        manual_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        manual_row.append(manual_date)
        today_button = Gtk.Button(label="Today")
        manual_row.append(today_button)
        picker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        picker_box.set_margin_top(8)
        picker_box.set_margin_bottom(8)
        picker_box.set_margin_start(8)
        picker_box.set_margin_end(8)
        picker_box.append(picker)
        picker_box.append(manual_row)
        picker_popover = Gtk.Popover()
        picker_popover.set_child(picker_box)
        date_button.set_popover(picker_popover)
        syncing_picker = False

        def set_date_value(value: str) -> None:
            try:
                normalized = date.fromisoformat(value.strip()).isoformat()
            except ValueError:
                raise ValueError("Date must be YYYY-MM-DD")
            date_value[0] = normalized
            date_button.set_label(normalized)
            manual_date.set_text(normalized)

        def sync_date_picker(*_args) -> None:
            nonlocal syncing_picker
            if not date_button.get_active():
                return
            from gi.repository import GLib

            try:
                picked = date.fromisoformat(date_value[0])
            except ValueError:
                picked = date.today()
            syncing_picker = True
            picker.set_date(
                GLib.DateTime.new_local(
                    picked.year, picked.month, picked.day, 0, 0, 0
                )
            )
            manual_date.set_text(date_value[0])
            syncing_picker = False

        def on_day_selected(_calendar) -> None:
            if syncing_picker:
                return
            chosen = _calendar.get_date()
            set_date_value(
                f"{chosen.get_year():04d}-"
                f"{chosen.get_month():02d}-"
                f"{chosen.get_day_of_month():02d}"
            )
            picker_popover.popdown()
            date_button.set_active(False)

        def on_manual_date(_entry) -> None:
            try:
                set_date_value(manual_date.get_text())
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            picker_popover.popdown()
            date_button.set_active(False)

        def on_date_today(_button) -> None:
            set_date_value(date.today().isoformat())
            picker_popover.popdown()
            date_button.set_active(False)

        date_button.connect("notify::active", sync_date_picker)
        picker.connect("day-selected", on_day_selected)
        manual_date.connect("activate", on_manual_date)
        today_button.connect("clicked", on_date_today)
        date_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        date_row.append(date_button)

        allday_switch = Gtk.Switch()
        allday_switch.set_active(bool(data) and not data.get("time"))
        start_entry = Gtk.Entry()
        start_entry.set_width_chars(6)
        start_entry.set_max_length(5)
        start_entry.set_placeholder_text("HH:MM")
        start_entry.set_text(format_minutes(start_minutes))
        start_entry.set_tooltip_text("Start time in 24-hour format, e.g. 08:43")
        end_entry = Gtk.Entry()
        end_entry.set_width_chars(6)
        end_entry.set_max_length(5)
        end_entry.set_placeholder_text("HH:MM")
        end_entry.set_text(format_minutes(end_minutes))
        end_entry.set_tooltip_text("End time in 24-hour format, e.g. 09:30")
        time_widgets = (start_entry,)
        end_widgets = (end_entry,)
        time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        time_row.append(dim_label("all day"))
        time_row.append(allday_switch)
        start_label = dim_label("from")
        end_label = dim_label("to")
        time_row.append(start_label)
        for widget in time_widgets:
            time_row.append(widget)
        time_row.append(end_label)
        for widget in end_widgets:
            time_row.append(widget)

        def sync_time(*_args) -> None:
            visible = not allday_switch.get_active()
            for widget in time_widgets + end_widgets + (start_label, end_label):
                widget.set_visible(visible)

        allday_switch.connect("notify::active", sync_time)

        category_combo = Gtk.ComboBoxText()
        category_combo.append("", "No layer")
        for category in self._cats.all():
            category_combo.append(category["id"], category["name"])
        category_combo.set_active(0)
        current_category = data.get("category") or ""
        if current_category:
            category_combo.set_active_id(current_category)

        reminder_combo = Gtk.ComboBoxText()
        default_lead = self._prefs["reminder_lead"]
        default_label = (
            f"Default ({default_lead} min)"
            if self._prefs["reminders"]
            else "Default (off)"
        )
        reminder_combo.append("default", default_label)
        reminder_combo.append("off", "Off")
        preset_leads = (5, 10, 15, 30, 60)
        for minutes in preset_leads:
            reminder_combo.append(str(minutes), f"{minutes} minutes before")
        reminder_combo.append("custom", "Custom…")
        current_reminder = data.get("reminder")
        if current_reminder is None:
            reminder_combo.set_active_id("default")
        elif current_reminder == 0:
            reminder_combo.set_active_id("off")
        elif current_reminder in preset_leads:
            reminder_combo.set_active_id(str(current_reminder))
        else:
            reminder_combo.set_active_id("custom")
        reminder_minutes = Gtk.SpinButton.new_with_range(1, 10080, 1)
        reminder_minutes.set_numeric(True)
        reminder_minutes.set_tooltip_text(
            "Minutes before the event starts (1 minute to 7 days)"
        )
        if isinstance(current_reminder, int) and current_reminder not in (
            0,
            *preset_leads,
        ):
            reminder_minutes.set_value(current_reminder)
        else:
            reminder_minutes.set_value(default_lead)
        reminder_suffix = dim_label("minutes before")
        reminder_custom = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        reminder_custom.append(reminder_minutes)
        reminder_custom.append(reminder_suffix)
        reminder_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        reminder_row.append(reminder_combo)
        reminder_row.append(reminder_custom)

        def sync_reminder(*_args) -> None:
            reminder_custom.set_visible(
                reminder_combo.get_active_id() == "custom"
            )

        reminder_combo.connect("changed", sync_reminder)
        sync_reminder()

        habit_switch = Gtk.Switch()
        habit_switch.set_active(
            bool(data.get("habit")) or (bool(habit) and not editing)
        )

        details.attach(dim_label("Title"), 0, 0, 1, 1)
        details.attach(title_entry, 1, 0, 2, 1)
        details.attach(dim_label("Date"), 0, 1, 1, 1)
        details.attach(date_row, 1, 1, 2, 1)
        details.attach(dim_label("Time"), 0, 2, 1, 1)
        details.attach(time_row, 1, 2, 2, 1)
        details.attach(dim_label("Layer"), 0, 3, 1, 1)
        details.attach(category_combo, 1, 3, 2, 1)
        details.attach(dim_label("Reminder"), 0, 4, 1, 1)
        details.attach(reminder_row, 1, 4, 2, 1)
        details.attach(dim_label("Habit"), 0, 5, 1, 1)
        habit_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        habit_row.append(habit_switch)
        habit_hint = Gtk.Label(
            label="checkable every day it repeats (tracker + statistics)",
            xalign=0.0,
        )
        habit_hint.get_style_context().add_class("omnify-dim")
        habit_row.append(habit_hint)
        details.attach(habit_row, 1, 5, 2, 1)
        form.append(details)
        sync_time()

        # --- recurrence ----------------------------------------------
        repeat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        repeat_title = Gtk.Label(label="Repeat", xalign=0.0)
        repeat_title.get_style_context().add_class("omnify-card-title")
        repeat_box.append(repeat_title)
        freq_combo = Gtk.ComboBoxText()
        for frequency_id, frequency_label in FREQUENCY_LABELS:
            freq_combo.append(frequency_id, frequency_label)
        freq_combo.set_active_id(record.get("freq") or "none")
        interval = Gtk.SpinButton.new_with_range(1, 99, 1)
        interval.set_value(int(record.get("interval", 1) or 1))
        every_label = Gtk.Label(label="every")
        every_suffix = Gtk.Label(label="period(s)")
        repeat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        repeat_row.append(freq_combo)
        repeat_row.append(every_label)
        repeat_row.append(interval)
        repeat_row.append(every_suffix)
        repeat_box.append(repeat_row)

        weekday_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        weekday_checks = []
        selected_weekdays = set(record.get("weekdays") or [])
        for index, name in enumerate(WEEKDAY_NAMES):
            check = Gtk.CheckButton(label=name)
            check.set_active(index in selected_weekdays)
            weekday_checks.append(check)
            weekday_row.append(check)
        repeat_box.append(weekday_row)

        month_day = Gtk.SpinButton.new_with_range(0, 31, 1)
        month_day.set_value(int(record.get("month_day", 0) or 0))
        month_day_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        month_day_row.append(dim_label("Day of month (0 = start date's day)"))
        month_day_row.append(month_day)
        repeat_box.append(month_day_row)

        until_entry = Gtk.Entry()
        until_entry.set_placeholder_text("YYYY-MM-DD (optional)")
        until_entry.set_text(record.get("until", "") or "")
        until_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        until_row.append(dim_label("Until"))
        until_entry.set_hexpand(True)
        until_row.append(until_entry)
        repeat_box.append(until_row)

        def sync_recurrence(*_args) -> None:
            frequency = freq_combo.get_active_id() or "none"
            repeating = frequency != "none"
            every_label.set_visible(repeating)
            interval.set_visible(repeating)
            every_suffix.set_visible(repeating)
            weekday_row.set_visible(frequency == "weekly")
            month_day_row.set_visible(frequency == "monthly")
            until_row.set_visible(repeating)

        freq_combo.connect("changed", sync_recurrence)
        sync_recurrence()

        def sync_habit(*_args) -> None:
            if habit_switch.get_active() and (
                freq_combo.get_active_id() in (None, "none")
            ):
                freq_combo.set_active_id("daily")

        habit_switch.connect("notify::active", sync_habit)
        form.append(repeat_box)

        # --- event actions -------------------------------------------
        actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        actions_box.get_style_context().add_class("omnify-card")
        actions_title = Gtk.Label(label="When the event starts", xalign=0.0)
        actions_title.get_style_context().add_class("omnify-card-title")
        actions_box.append(actions_title)
        existing_actions = data.get("actions") or []
        timer_target = next(
            (a.get("target", "") for a in existing_actions
             if a.get("kind") == "timer"),
            "",
        )
        has_stopwatch = any(
            a.get("kind") == "stopwatch" for a in existing_actions
        )
        existing_command = next(
            (a.get("command", "") for a in existing_actions
             if a.get("kind") == "command"),
            "",
        )
        timer_entry = Gtk.Entry()
        timer_entry.set_placeholder_text("Timer name, e.g. Timer 1")
        timer_entry.set_text(timer_target)
        stopwatch_switch = Gtk.Switch()
        stopwatch_switch.set_active(has_stopwatch)
        command_entry = Gtk.Entry()
        command_entry.set_placeholder_text("Command, e.g. libreoffice --writer")
        command_entry.set_text(existing_command)
        close_switch = Gtk.Switch()
        close_switch.set_active(
            bool(data.get("start_on_close"))
        )
        for label_text, widget in (
            ("Start timer", timer_entry),
            ("Stopwatch", stopwatch_switch),
            ("Run command", command_entry),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(dim_label(label_text))
            widget.set_hexpand(True)
            row.append(widget)
            actions_box.append(row)
        close_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        close_row.append(dim_label("Also start when Omnify's window closes"))
        close_row.append(close_switch)
        actions_box.append(close_row)
        form.append(actions_box)

        # --- notes ----------------------------------------------------
        notes_title = Gtk.Label(label="Notes", xalign=0.0)
        notes_title.get_style_context().add_class("omnify-card-title")
        form.append(notes_title)
        notes_view = Gtk.TextView()
        notes_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        notes_buffer = notes_view.get_buffer()
        notes_buffer.set_text(data.get("notes", "") or "")
        notes_scroll = Gtk.ScrolledWindow()
        notes_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        notes_scroll.set_min_content_height(80)
        notes_scroll.set_child(notes_view)
        form.append(notes_scroll)

        # --- buttons --------------------------------------------------
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        save_button = Gtk.Button(label="Save")
        save_button.get_style_context().add_class("omnify-accent")
        cancel_button = Gtk.Button(label="Cancel")
        buttons.append(save_button)
        buttons.append(cancel_button)
        if editing:
            template_button = Gtk.Button(label="Save as template")
            duplicate_button = Gtk.Button(label="Duplicate")
            recurring = bool(editing.get("recurrence"))
            if recurring:
                delete_button = Gtk.MenuButton(label="Delete")
                delete_popover = Gtk.Popover()
                delete_box = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL, spacing=4
                )
                delete_box.set_margin_top(8)
                delete_box.set_margin_bottom(8)
                delete_box.set_margin_start(8)
                delete_box.set_margin_end(8)
                delete_title = Gtk.Label(
                    label="Delete recurring event", xalign=0.0
                )
                delete_title.get_style_context().add_class("omnify-card-title")
                delete_box.append(delete_title)
                delete_only = Gtk.Button(label="Only this event")
                delete_all = Gtk.Button(label="All events in the series")
                delete_following = Gtk.Button(
                    label="This and following events"
                )
                for item in (delete_only, delete_all, delete_following):
                    item.set_halign(Gtk.Align.FILL)
                    delete_box.append(item)
                delete_popover.set_child(delete_box)
                delete_button.set_popover(delete_popover)
            else:
                delete_button = Gtk.Button(label="Delete")
                delete_only = delete_all = delete_following = None
            delete_button.get_style_context().add_class("omnify-danger")
            buttons.append(template_button)
            buttons.append(duplicate_button)
            buttons.append(delete_button)
        outer.append(buttons)
        win.set_child(outer)

        def collect_fields() -> Dict[str, Any]:
            """Read every form widget into the EventStore schema."""
            all_day = allday_switch.get_active()
            date_text = date_value[0].strip()
            if all_day:
                start_value = None
                end_value = None
            else:
                start_value = normalize_clock_text(start_entry.get_text())
                end_value = normalize_clock_text(end_entry.get_text())
            frequency = freq_combo.get_active_id() or "none"
            recurrence = None
            if frequency != "none":
                recurrence = {
                    "freq": frequency,
                    "interval": int(interval.get_value()),
                }
                if frequency == "weekly":
                    selected = [
                        index
                        for index, check in enumerate(weekday_checks)
                        if check.get_active()
                    ]
                    if not selected:
                        selected = [date.fromisoformat(date_text).weekday()]
                    recurrence["weekdays"] = selected
                if frequency == "monthly":
                    day_number = int(month_day.get_value())
                    if day_number:
                        recurrence["month_day"] = day_number
                until_text = until_entry.get_text().strip()
                if until_text:
                    recurrence["until"] = until_text
            elif habit_switch.get_active():
                recurrence = {"freq": "daily", "interval": 1}
            text = notes_buffer.get_text(
                notes_buffer.get_start_iter(),
                notes_buffer.get_end_iter(),
                True,
            )
            actions = []
            timer_name = timer_entry.get_text().strip()
            if timer_name:
                actions.append({"kind": "timer", "target": timer_name})
            if stopwatch_switch.get_active():
                actions.append({"kind": "stopwatch"})
            command_text = command_entry.get_text().strip()
            if command_text:
                actions.append({"kind": "command", "command": command_text})
            reminder_id = reminder_combo.get_active_id() or "default"
            if reminder_id == "default":
                reminder = None
            elif reminder_id == "off":
                reminder = 0
            elif reminder_id == "custom":
                reminder = int(reminder_minutes.get_value())
            else:
                reminder = int(reminder_id)
            return {
                "date": date_text,
                "time": start_value,
                "end_time": end_value,
                "title": title_entry.get_text().strip(),
                "notes": text,
                "category": category_combo.get_active_id() or "",
                "recurrence": recurrence,
                "habit": habit_switch.get_active(),
                "actions": actions,
                "start_on_close": close_switch.get_active(),
                "reminder": reminder,
            }

        def on_save(_button) -> None:
            try:
                fields = collect_fields()
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            before = self._snapshot()
            try:
                if editing:
                    self._store.update(editing["id"], **fields)
                else:
                    self._store.add(
                        fields["date"],
                        fields["time"],
                        fields["title"],
                        fields["notes"],
                        end_time=fields["end_time"],
                        category=fields["category"],
                        recurrence=fields["recurrence"],
                        habit=fields["habit"],
                        actions=fields["actions"],
                        start_on_close=fields["start_on_close"],
                        reminder=fields["reminder"],
                    )
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            self._record_history(
                "Edit event"
                if editing
                else ("Add habit" if fields["habit"] else "Add event"),
                before,
            )
            was_habit = fields["habit"] and not editing
            self._close_event_dialog()
            self._refresh_current()
            if was_habit:
                self._stack.set_visible_child_name("habits")

        def on_duplicate(_button) -> None:
            try:
                fields = collect_fields()
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            before = self._snapshot()
            try:
                copy = self._store.add(
                    fields["date"],
                    fields["time"],
                    f"{fields['title']} (copy)",
                    fields["notes"],
                    end_time=fields["end_time"],
                    category=fields["category"],
                    recurrence=fields["recurrence"],
                    habit=False,
                    actions=fields["actions"],
                    start_on_close=fields["start_on_close"],
                    reminder=fields["reminder"],
                )
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            self._record_history("Duplicate event", before)
            self._close_event_dialog()
            self._refresh_current()
            self.ctx.notify(f"Duplicated: {copy['title']}")

        def on_save_template(_button) -> None:
            try:
                fields = collect_fields()
            except ValueError as exc:
                self.ctx.notify(str(exc))
                return
            self._save_template_from_fields(fields)

        def delete_scope(scope: str) -> None:
            if not editing:
                return
            try:
                cutoff = date.fromisoformat(
                    date_value[0]
                ).isoformat()
            except ValueError:
                self.ctx.notify("Date must be YYYY-MM-DD")
                return
            before = self._snapshot()
            try:
                if scope == "only":
                    self._store.cancel_occurrence(editing["id"], cutoff)
                    message = "Occurrence deleted"
                elif scope == "following":
                    kept = self._store.truncate_series(
                        editing["id"], cutoff
                    )
                    message = (
                        "Following events deleted"
                        if kept
                        else "Event deleted"
                    )
                else:
                    self._store.remove(editing["id"])
                    message = "Event deleted"
            except KeyError:
                self.ctx.notify("Event no longer exists")
                return
            labels = {
                "only": "Delete occurrence",
                "following": "Delete following events",
                "all": "Delete event",
            }
            self._record_history(labels.get(scope, "Delete event"), before)
            self._close_event_dialog()
            self._refresh_current()
            self.ctx.notify(message)

        save_button.connect("clicked", on_save)
        cancel_button.connect("clicked", lambda _b: self._close_event_dialog())
        if editing:
            template_button.connect("clicked", on_save_template)
            duplicate_button.connect("clicked", on_duplicate)
            if delete_only is not None:
                delete_only.connect(
                    "clicked", lambda _b: delete_scope("only")
                )
                delete_following.connect(
                    "clicked", lambda _b: delete_scope("following")
                )
                delete_all.connect(
                    "clicked", lambda _b: delete_scope("all")
                )
            else:
                delete_button.connect(
                    "clicked", lambda _b: delete_scope("all")
                )
        win.set_hide_on_close(False)
        win.get_style_context().add_class("omnify-dialog")
        win.connect("close-request", self._on_event_dialog_close_request)
        win.connect("destroy", self._on_event_dialog_destroyed)
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_dialog_key_pressed)
        win.add_controller(controller)
        self._dialog = win
        win.present()

        def focus_title() -> bool:
            if (
                self._dialog is win
                and win.get_realized()
                and win.get_visible()
            ):
                title_entry.grab_focus()
            return False

        from gi.repository import GLib

        GLib.idle_add(focus_title)

    def _close_event_dialog(self) -> None:
        if self._dialog is not None:
            dialog, self._dialog = self._dialog, None
            dialog.destroy()

    def _on_event_dialog_close_request(self, _window) -> bool:
        self._dialog = None
        return False

    def _on_event_dialog_destroyed(self, _window) -> None:
        self._dialog = None

    def _on_dialog_key_pressed(
        self, _controller, keyval, _keycode, _state
    ) -> bool:
        """Escape closes whichever planner dialog is open."""
        from gi.repository import Gdk

        if keyval != Gdk.KEY_Escape:
            return False
        if self._dialog is not None:
            self._close_event_dialog()
        elif self._layers_dialog is not None:
            self._close_layers_dialog()
        elif self._goal_dialog is not None:
            self._close_goal_dialog()
        elif self._settings_dialog is not None:
            self._close_settings_dialog()
        return True

# === ACTIONS / PUMP ===
    def pump(self) -> bool:
        """Fire event actions at their start minute (called by the shell)."""
        if getattr(self, "_store", None) is None:
            return False
        now = datetime.now()
        today_iso = now.strftime("%Y-%m-%d")
        minute = now.strftime("%H:%M")
        if self._fired_day != today_iso:
            self._fired_day = today_iso
            self._fired = {
                key for key in self._fired if len(key) > 1 and key[1] == today_iso
            }
        fired = False
        for occurrence in self._store.day_events(today_iso, self._visible):
            if occurrence.get("time") != minute:
                continue
            for index, action in enumerate(occurrence.get("actions") or []):
                key = (occurrence.get("id"), today_iso, index)
                if key in self._fired:
                    continue
                self._fired.add(key)
                self._run_action(action, occurrence)
                fired = True
        self._check_reminders(now)
        # Keep the week's "now" line fresh while the page is visible.
        if (
            self._stack.get_visible_child_name() == "week"
            and minute != self._last_minute
        ):
            self._last_minute = minute
            for area in self._week_bg_areas:
                area.queue_draw()
        return fired

    def _check_reminders(self, now: datetime) -> None:
        """Send one desktop notification per occurrence inside its lead time."""
        if not self._prefs.get("reminders"):
            return
        lead = int(self._prefs.get("reminder_lead") or 0)
        if lead <= 0:
            return
        due = due_reminders(self._store, now, lead)
        if not due:
            return
        stored = self.ctx.storage.load(NOTIFIED_KEY, [])
        if not isinstance(stored, list):
            stored = []
        cutoff = (now.date() - timedelta(days=7)).isoformat()
        known = set()
        for key in stored:
            text = str(key)
            event_id, _, day_iso = text.partition("|")
            if event_id and day_iso and day_iso >= cutoff:
                known.add(text)
        changed = len(known) != len(stored)
        for event_id, day_iso, occurrence, minutes in due:
            key = f"{event_id}|{day_iso}"
            if key in known:
                continue
            title = occurrence.get("title") or "Event"
            self.ctx.notify(f"⏰ {title} starts in {minutes} min")
            known.add(key)
            changed = True
        if changed:
            self.ctx.storage.save(NOTIFIED_KEY, sorted(known))

    def _run_action(self, action: Dict, occurrence: Dict) -> None:
        """Start a timer/stopwatch or launch a program for one event."""
        from ..core.logfile import log

        kind = action.get("kind")
        title = occurrence.get("title") or "Event"
        if kind == "timer":
            module = self.ctx.get_module("timer")
            start = getattr(module, "start_named_timer", None)
            if callable(start):
                if start(action.get("target") or ""):
                    self.ctx.notify(f"{title}: timer started")
                    log(
                        f"calendar: timer {action.get('target')!r} "
                        f"started by {title!r}"
                    )
                else:
                    self.ctx.notify(
                        f"Timer {action.get('target')!r} is missing or busy"
                    )
            else:
                self.ctx.notify("Timer module is not available")
        elif kind == "stopwatch":
            module = self.ctx.get_module("stopwatch")
            start = getattr(module, "start_from_action", None)
            if callable(start) and start():
                self.ctx.notify(f"{title}: stopwatch started")
                log(f"calendar: stopwatch started by {title!r}")
        elif kind == "command":
            command = action.get("command") or ""
            try:
                import shlex
                import subprocess

                args = shlex.split(command)
                if args:
                    subprocess.Popen(
                        args,
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self.ctx.notify(f"{title}: launched {args[0]}")
                    log(f"calendar: launched {args!r} by {title!r}")
            except (OSError, ValueError) as exc:
                self.ctx.notify(f"Could not launch command: {exc}")
                log(f"calendar: command {command!r} failed: {exc!r}")

    def on_show(self) -> None:
        """Refresh the last page and autoscroll the week to *now*."""
        if getattr(self, "_stack", None) is None:
            return
        self._refresh_layer_styles()
        self._refresh_current()
        if self._stack.get_visible_child_name() == "week":
            self._scroll_week_to_now()

    def on_hide(self) -> None:
        """Pause custom-gradient animation while another module is open."""
        self._stop_layer_ticker()

    def on_window_closed(self) -> None:
        """Run ``start_on_close`` actions for events that have already begun.

        Closing the main window hides it (the app keeps running in the
        tray); this hook is how an event can kick off a timer/stopwatch
        exactly when the user leaves the planner to start working.
        """
        if getattr(self, "_store", None) is None:
            return
        # Never leave a modal planner dialog floating over a hidden window.
        self._close_event_dialog()
        self._close_goal_dialog()
        self._close_layers_dialog()
        self._close_settings_dialog()
        self._close_templates_menu()
        if getattr(self, "_shortcuts_popover", None) is not None:
            self._shortcuts_popover.popdown()
        now = datetime.now()
        today_iso = now.strftime("%Y-%m-%d")
        minute = now.strftime("%H:%M")
        for occurrence in self._store.day_events(today_iso, self._visible):
            if not occurrence.get("start_on_close"):
                continue
            start = occurrence.get("time")
            if start and start > minute:
                continue
            key = (occurrence.get("id"), today_iso, "close")
            if key in self._fired:
                continue
            self._fired.add(key)
            for action in occurrence.get("actions") or []:
                self._run_action(action, occurrence)








