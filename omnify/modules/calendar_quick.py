"""Deterministic natural-language quick add for the calendar.

This is deliberately *not* an AI feature.  Quick add understands a small,
documented grammar of dates, times, durations and all-day phrases, so the
result is predictable, works completely offline and never sends an event
title to a remote service.  The parser is GTK-free and covered by headless
tests.

Examples::

    Lunch with Ann tomorrow at 13:00
    Dentist on Friday 10:00-11:00
    Standup next monday 9am for 15 minutes
    Deep work tomorrow
    Зустріч завтра о 15:30
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..core.i18n import tr

#: Month names accepted by quick add.  Genitive Ukrainian forms are included
#: because dates are normally written as ``20 листопада``.
_MONTHS: Dict[str, int] = {}


def _register_months(number: int, *names: str) -> None:
    for name in names:
        _MONTHS[name] = number


_register_months(
    1, "january", "jan",
    "січень", "січня", "січні",
)
_register_months(
    2, "february", "feb",
    "лютий", "лютого", "лютому",
)
_register_months(
    3, "march", "mar",
    "березень", "березня",
)
_register_months(
    4, "april", "apr",
    "квітень", "квітня",
)
_register_months(
    5, "may",
    "травень", "травня",
)
_register_months(
    6, "june", "jun",
    "червень", "червня",
)
_register_months(
    7, "july", "jul",
    "липень", "липня",
)
_register_months(
    8, "august", "aug",
    "серпень", "серпня",
)
_register_months(
    9, "september", "sep", "sept",
    "вересень", "вересня",
)
_register_months(
    10, "october", "oct",
    "жовтень", "жовтня",
)
_register_months(
    11, "november", "nov",
    "листопад", "листопада",
)
_register_months(
    12, "december", "dec",
    "грудень", "грудня",
)

#: Weekday names (Monday = 0) in English and Ukrainian.
_WEEKDAYS: Dict[str, int] = {}
for _number, _names in enumerate(
    (
        ("monday", "mon", "понеділок", "пн"),
        ("tuesday", "tue", "tues", "вівторок", "вт"),
        ("wednesday", "wed", "weds", "середа", "сер", "ср"),
        ("thursday", "thu", "thur", "thurs", "четвер", "чт"),
        ("friday", "fri", "п'ятниця", "п’ятниця", "пʼятниця", "пт"),
        ("saturday", "sat", "субота", "сб"),
        ("sunday", "sun", "неділя", "нд"),
    )
):
    for _name in _names:
        _WEEKDAYS[_name] = _number

_MONTH_PATTERN = "|".join(
    sorted((re.escape(name) for name in _MONTHS), key=len, reverse=True)
)
_WEEKDAY_PATTERN = "|".join(
    sorted((re.escape(name) for name in _WEEKDAYS), key=len, reverse=True)
)

_FLAGS = re.IGNORECASE | re.UNICODE

_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DOTTED_DATE_RE = re.compile(
    r"(?<![\d.,])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?![\d.,])"
)
_DAY_MONTH_RE = re.compile(
    r"(?<!\w)(\d{1,2})(?:st|nd|rd|th)?\s+("
    + _MONTH_PATTERN
    + r")(?:\s+(\d{4}))?(?!\w)",
    _FLAGS,
)
_MONTH_DAY_RE = re.compile(
    r"(?<!\w)("
    + _MONTH_PATTERN
    + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?(?!\w)",
    _FLAGS,
)
_DAYS_AHEAD_RE = re.compile(
    r"(?<!\w)(?:in|через)\s+(\d{1,2})\s+(?:days?|дні|днів|день)(?!\w)",
    _FLAGS,
)
_WEEKDAY_RE = re.compile(
    r"(?<!\w)(?:(?:next|this|on|by|у|в|на|до|цього|наступного)\s+)?("
    + _WEEKDAY_PATTERN
    + r")(?!\w)",
    _FLAGS,
)

_DURATION_RE = re.compile(
    r"(?<!\w)(?:(?:for|протягом)\s+)?(\d{1,3})\s*"
    r"(minutes?|mins?|m|hours?|hrs?|h|"
    r"хвилин(?:а|и|у)?|хв|годин(?:а|и)?|год)(?!\w)",
    _FLAGS,
)
_ALL_DAY_RE = re.compile(
    r"(?<!\w)(?:all[\s-]?day|whole\s+day|увесь\s+день|цілий\s+день)(?!\w)",
    _FLAGS,
)

_RANGE_RE = re.compile(
    r"(?<!\w)(?:(from|between|з|від)\s+)?"
    r"(?<!\d)(\d{1,2})(?:[:.](\d{2}))?\s*([ap]m\s*\.?)?"
    r"\s*(?:-|–|—|to|until|до|по)\s*"
    r"(?<!\d)(\d{1,2})(?:[:.](\d{2}))?\s*([ap]m\s*\.?)?(?!\w)",
    _FLAGS,
)
_SINGLE_TIME_RE = re.compile(
    r"(?<!\d)(\d{1,2}):(\d{2})(?:\s*([ap]m\s*\.?))?(?!\d)",
    _FLAGS,
)
_AMPM_TIME_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*([ap]m\s*\.?)(?!\w)",
    _FLAGS,
)
_CONNECTOR_TIME_RE = re.compile(
    r"(?<!\w)(?:at|o|ob|о|об|в|у)\s+(\d{1,2})(?:[.:](\d{2}))?(?![\d:])",
    _FLAGS,
)


@dataclass(frozen=True)
class QuickEvent:
    """The result of :func:`parse_quick_event`.

    ``time``/``end_time`` are ``None`` for an all-day event.
    """

    title: str
    date_iso: str
    time: Optional[str]
    end_time: Optional[str]

    @property
    def all_day(self) -> bool:
        return self.time is None


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _nearest_year(month: int, day: int, today: date) -> Optional[date]:
    """A day/month pair in the future; roll to next year when it passed."""
    candidate = _safe_date(today.year, month, day)
    if candidate is None:
        return None
    if candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def _extend_date_span(text: str, start: int, end: int) -> Tuple[int, int]:
    """Include "on"/"на"/"до" directly before a date expression."""
    match = re.search(
        r"(?<!\w)(?:on|by|from|until|till|до|на|у|в|з|із)\s+$",
        text[:start],
        re.IGNORECASE,
    )
    if match is not None:
        return match.start(), end
    return start, end


def _overlaps(start: int, end: int, spans: List[Tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _find_date(
    text: str, today: date
) -> Tuple[date, List[Tuple[int, int]]]:
    """Return the first explicit date (or *today*) and all date spans."""
    candidates: List[Tuple[int, int, date]] = []

    for match in _ISO_DATE_RE.finditer(text):
        year, month, day = (int(part) for part in match.groups())
        parsed = _safe_date(year, month, day)
        if parsed is None:
            raise ValueError(tr("Invalid date: {value}", value=match.group(0)))
        candidates.append((match.start(), match.end(), parsed))

    for match in _DAY_MONTH_RE.finditer(text):
        day = int(match.group(1))
        month = _MONTHS[match.group(2).lower()]
        year_text = match.group(3)
        parsed = (
            _safe_date(int(year_text), month, day)
            if year_text
            else _nearest_year(month, day, today)
        )
        if parsed is None:
            raise ValueError(tr("Invalid date: {value}", value=match.group(0)))
        candidates.append((match.start(), match.end(), parsed))

    for match in _MONTH_DAY_RE.finditer(text):
        month = _MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        year_text = match.group(3)
        parsed = (
            _safe_date(int(year_text), month, day)
            if year_text
            else _nearest_year(month, day, today)
        )
        if parsed is None:
            raise ValueError(tr("Invalid date: {value}", value=match.group(0)))
        candidates.append((match.start(), match.end(), parsed))

    for match in _DOTTED_DATE_RE.finditer(text):
        day = int(match.group(1))
        month = int(match.group(2))
        year_text = match.group(3)
        if not 1 <= month <= 12:
            continue
        # A yearless "3.11" is much more likely to be a version number.
        # Requiring a day above 12 keeps "20.11" useful without eating
        # titles such as "Python 3.11".
        if year_text is None and day <= 12:
            continue
        parsed = (
            _safe_date(int(year_text), month, day)
            if year_text
            else _nearest_year(month, day, today)
        )
        if parsed is None:
            raise ValueError(tr("Invalid date: {value}", value=match.group(0)))
        candidates.append((match.start(), match.end(), parsed))

    relative = (
        (re.compile(r"(?<!\w)(?:day\s+after\s+tomorrow|післязавтра)(?!\w)", _FLAGS), 2),
        (re.compile(r"(?<!\w)(?:tomorrow|завтра)(?!\w)", _FLAGS), 1),
        (re.compile(r"(?<!\w)(?:today|сьогодні)(?!\w)", _FLAGS), 0),
    )
    for pattern, offset in relative:
        for match in pattern.finditer(text):
            candidates.append((match.start(), match.end(), today + timedelta(days=offset)))

    for match in _DAYS_AHEAD_RE.finditer(text):
        candidates.append(
            (
                match.start(),
                match.end(),
                today + timedelta(days=int(match.group(1))),
            )
        )

    for match in _WEEKDAY_RE.finditer(text):
        target = _WEEKDAYS[match.group(1).lower()]
        delta = (target - today.weekday()) % 7 or 7
        candidates.append((match.start(), match.end(), today + timedelta(days=delta)))

    if not candidates:
        return today, []

    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    spans = [
        _extend_date_span(text, start, end)
        for start, end, _parsed in candidates
    ]
    return candidates[0][2], spans


def _meridiem(value: Optional[str]) -> str:
    return (value or "").replace(".", "").replace(" ", "").lower()


def _clock_minutes(
    hour_text: str,
    minute_text: Optional[str],
    meridiem: Optional[str],
    fallback_meridiem: str = "",
) -> int:
    hour = int(hour_text)
    minute = int(minute_text or 0)
    marker = _meridiem(meridiem) or fallback_meridiem
    if marker in {"am", "pm"}:
        if not 1 <= hour <= 12:
            raise ValueError(tr("Invalid time: {value}", value=f"{hour_text}:{minute_text or '00'}"))
        if marker == "pm" and hour != 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(tr("Invalid time: {value}", value=f"{hour_text}:{minute_text or '00'}"))
    return hour * 60 + minute


def _format_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _extend_time_span(text: str, start: int, end: int) -> Tuple[int, int]:
    """Include a directly preceding preposition in a time span.

    ``"Lunch at 13:00"`` must lose the whole ``at 13:00`` phrase, otherwise
    the title would become ``"Lunch at"``.
    """
    match = re.search(
        r"(?<!\w)(?:at|from|o|ob|о|об|в|у)\s+$",
        text[:start],
        re.IGNORECASE,
    )
    if match is not None:
        return match.start(), end
    return start, end


def _find_time(
    text: str, blocked: List[Tuple[int, int]]
) -> Tuple[Optional[int], Optional[int], List[Tuple[int, int]]]:
    """Return ``(start_minutes, end_minutes, spans)`` for the first time."""
    spans: List[Tuple[int, int]] = []
    start_minutes: Optional[int] = None
    end_minutes: Optional[int] = None

    # Ranges first: "13:00-14:30", "from 9 to 11", "з 10:00 до 11:00".
    for match in _RANGE_RE.finditer(text):
        if _overlaps(match.start(), match.end(), blocked + spans):
            continue
        prefix = (match.group(1) or "").lower()
        start_hour, start_minute, start_mer = match.group(2, 3, 4)
        end_hour, end_minute, end_mer = match.group(5, 6, 7)
        token = match.group(0)
        if (
            not prefix
            and ":" not in token
            and "." not in token
            and not _meridiem(start_mer)
            and not _meridiem(end_mer)
        ):
            # Bare "10-11" is too easy to confuse with a range of numbers.
            continue
        try:
            start = _clock_minutes(start_hour, start_minute, start_mer)
            end = _clock_minutes(end_hour, end_minute, end_mer)
        except ValueError:
            continue
        if _meridiem(start_mer) and not _meridiem(end_mer) and end <= 12 * 60:
            try:
                end = _clock_minutes(end_hour, end_minute, start_mer)
            except ValueError:
                pass
        elif _meridiem(end_mer) and not _meridiem(start_mer) and start <= 12 * 60:
            try:
                start = _clock_minutes(start_hour, start_minute, end_mer)
            except ValueError:
                pass
        if end <= start:
            candidate = end + 12 * 60
            crosses_midnight = (
                start >= 12 * 60 and end < 12 * 60 and candidate <= 23 * 60 + 59
            )
            if (
                not _meridiem(end_mer)
                and candidate <= 23 * 60 + 59
                and not crosses_midnight
            ):
                end = candidate
            if end <= start:
                raise ValueError(tr("End time must be after the start time"))
        start_minutes, end_minutes = start, end
        spans.append(_extend_time_span(text, match.start(), match.end()))
        break

    if start_minutes is None:
        # Colon times: 09:30, 9:30pm, 13:00.
        for match in _SINGLE_TIME_RE.finditer(text):
            if _overlaps(match.start(), match.end(), blocked + spans):
                continue
            try:
                start_minutes = _clock_minutes(
                    match.group(1), match.group(2), match.group(3)
                )
            except ValueError:
                raise
            spans.append(_extend_time_span(text, match.start(), match.end()))
            break

    if start_minutes is None:
        # Compact meridiem times: 9am, 9 pm.
        for match in _AMPM_TIME_RE.finditer(text):
            if _overlaps(match.start(), match.end(), blocked + spans):
                continue
            start_minutes = _clock_minutes(
                match.group(1), match.group(2), match.group(3)
            )
            spans.append(_extend_time_span(text, match.start(), match.end()))
            break

    if start_minutes is None:
        # "at 9", "о 15", "в 8": a bare hour needs an explicit connector.
        for match in _CONNECTOR_TIME_RE.finditer(text):
            if _overlaps(match.start(), match.end(), blocked + spans):
                continue
            start_minutes = _clock_minutes(match.group(1), match.group(2), None)
            spans.append(_extend_time_span(text, match.start(), match.end()))
            break

    return start_minutes, end_minutes, spans


def _clean_title(text: str, spans: List[Tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = " "
    cleaned = re.sub(r"\s+", " ", "".join(chars)).strip()
    cleaned = re.sub(r"^[\s,;:.\-–—]+|[\s,;:.\-–—]+$", "", cleaned)
    return cleaned


def parse_quick_event(
    text: str, today: Optional[date] = None
) -> QuickEvent:
    """Parse one natural-language line into a :class:`QuickEvent`.

    Raises :class:`ValueError` for an empty line, an impossible date/time or
    an end time that is not after the start.
    """
    value = str(text or "").strip()
    if not value:
        raise ValueError(tr("Type an event, for example: Lunch tomorrow at 13:00"))
    base_day = today or date.today()

    event_date, date_spans = _find_date(value, base_day)

    all_day_match = _ALL_DAY_RE.search(value)
    all_day_spans = (
        [(all_day_match.start(), all_day_match.end())]
        if all_day_match
        else []
    )

    duration_spans: List[Tuple[int, int]] = []
    duration_minutes = 0
    for match in _DURATION_RE.finditer(value):
        if _overlaps(match.start(), match.end(), date_spans + all_day_spans):
            continue
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith(("h", "г")):
            duration_minutes += amount * 60
        else:
            duration_minutes += amount
        duration_spans.append((match.start(), match.end()))
    duration_minutes = min(duration_minutes, 24 * 60)

    start, end, time_spans = _find_time(
        value, date_spans + all_day_spans + duration_spans
    )

    spans = date_spans + all_day_spans + duration_spans + time_spans
    title = _clean_title(value, spans)

    if start is None:
        return QuickEvent(
            title=title,
            date_iso=event_date.isoformat(),
            time=None,
            end_time=None,
        )

    if end is None:
        end = min(start + (duration_minutes or 60), 23 * 60 + 59)
    if end <= start:
        raise ValueError(tr("End time must be after the start time"))

    return QuickEvent(
        title=title,
        date_iso=event_date.isoformat(),
        time=_format_clock(start),
        end_time=_format_clock(end),
    )


def meeting_note_markdown(
    event: Dict[str, Any], *, category_name: str = ""
) -> Tuple[str, str]:
    """Build a ready-to-edit meeting note from a calendar event.

    Returns ``(title, markdown)``.  The caller decides where the note lives
    and which module opens it.
    """
    event_title = str(event.get("title") or "").strip() or tr("Meeting")
    title = f"{event_title} — {tr('Meeting notes')}"

    event_date = str(event.get("date") or "").strip()
    start = str(event.get("time") or "").strip()
    end = str(event.get("end_time") or "").strip()
    if start:
        time_text = f"{start}–{end}" if end else start
    else:
        time_text = tr("All day")

    lines = [
        f"# {title}",
        "",
        f"- **{tr('Date')}:** {event_date}",
        f"- **{tr('Time')}:** {time_text}",
    ]
    location = str(event.get("location") or "").strip()
    if location:
        lines.append(f"- **{tr('Location')}:** {location}")
    if category_name:
        lines.append(f"- **{tr('Layer')}:** {category_name}")

    notes = str(event.get("notes") or "").strip()
    lines.extend(
        [
            "",
            f"## {tr('Agenda')}",
            notes or "- ",
            "",
            f"## {tr('Attendees')}",
        ]
    )
    raw_attendees = event.get("attendees") or ""
    if isinstance(raw_attendees, (list, tuple)):
        raw_attendees = ", ".join(str(item) for item in raw_attendees)
    attendees = [
        item.strip()
        for item in re.split(r"[,;\n]", str(raw_attendees))
        if item.strip()
    ]
    if attendees:
        lines.extend(f"- {item}" for item in attendees)
    else:
        lines.append("- ")
    lines.extend(
        [
            "",
            f"## {tr('Action items')}",
            "- [ ] ",
            "- [ ] ",
            "",
        ]
    )
    return title, "\n".join(lines)
