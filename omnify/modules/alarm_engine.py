"""Pure model for Omnify's alarm clock.

The model is deliberately free of GTK, sleeping and real-clock access.  Tests
inject a ``now`` function and drive time by hand.  An alarm has a local wall
time, an optional set of weekdays (empty means one-shot), a label, sound and
snooze duration.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

DAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_LONG = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
ALL_DAYS = frozenset(range(7))
WEEKDAYS = frozenset(range(5))
WEEKEND = frozenset({5, 6})


def _now_default() -> datetime:
    return datetime.now()


def parse_time(text: str) -> Optional[Tuple[int, int]]:
    """Parse ``H:MM``/``HH:MM`` into ``(hour, minute)`` or return ``None``."""
    raw = (text or "").strip()
    if not raw:
        return None
    if ":" not in raw:
        if not raw.isdigit():
            return None
        if len(raw) <= 2:
            raw = raw.zfill(2) + ":00"
        elif len(raw) in (3, 4):
            raw = raw[:-2] + ":" + raw[-2:]
        else:
            return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def format_time(hour: int, minute: int) -> str:
    return f"{int(hour):02d}:{int(minute):02d}"


def parse_days(values: Iterable[Any]) -> Set[int]:
    days: Set[int] = set()
    for value in values or ():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 6:
            days.add(number)
    return days


def format_days(days: Iterable[int]) -> str:
    selected = set(days)
    if not selected:
        return "Once"
    if selected == ALL_DAYS:
        return "Every day"
    if selected == WEEKDAYS:
        return "Weekdays"
    if selected == WEEKEND:
        return "Weekend"
    return ", ".join(DAY_SHORT[day] for day in range(7) if day in selected)


@dataclass
class Alarm:
    """One alarm on the clock."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    label: str = "Alarm"
    hour: int = 7
    minute: int = 0
    days: Set[int] = field(default_factory=set)
    enabled: bool = True
    sound: str = ""
    snooze_minutes: int = 5
    last_fired: str = ""
    snoozed_until: float = 0.0

    def validate(self) -> None:
        if not (0 <= int(self.hour) <= 23):
            raise ValueError("Alarm hour must be between 0 and 23")
        if not (0 <= int(self.minute) <= 59):
            raise ValueError("Alarm minute must be between 0 and 59")
        if not (1 <= int(self.snooze_minutes) <= 60):
            raise ValueError("Snooze duration must be between 1 and 60 minutes")
        if any(day not in ALL_DAYS for day in self.days):
            raise ValueError("Alarm days must be between Monday and Sunday")
        self.hour = int(self.hour)
        self.minute = int(self.minute)
        self.snooze_minutes = int(self.snooze_minutes)
        self.days = set(self.days)

    @property
    def time_text(self) -> str:
        return format_time(self.hour, self.minute)

    @property
    def days_text(self) -> str:
        return format_days(self.days)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "hour": self.hour,
            "minute": self.minute,
            "days": sorted(self.days),
            "enabled": bool(self.enabled),
            "sound": self.sound,
            "snooze_minutes": self.snooze_minutes,
            "last_fired": self.last_fired,
            "snoozed_until": self.snoozed_until,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Alarm"]:
        if not isinstance(data, dict):
            return None
        try:
            hour = int(data.get("hour", 7))
            minute = int(data.get("minute", 0))
        except (TypeError, ValueError):
            return None
        try:
            snooze = int(data.get("snooze_minutes", 5) or 5)
            snoozed_until = float(data.get("snoozed_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            snooze = 5
            snoozed_until = 0.0
        alarm = cls(
            id=str(data.get("id") or uuid.uuid4().hex[:10]),
            label=str(data.get("label", "Alarm") or "Alarm"),
            hour=hour,
            minute=minute,
            days=parse_days(data.get("days", ())),
            enabled=bool(data.get("enabled", True)),
            sound=str(data.get("sound", "") or ""),
            snooze_minutes=snooze,
            last_fired=str(data.get("last_fired", "") or ""),
            snoozed_until=snoozed_until,
        )
        try:
            alarm.validate()
        except (TypeError, ValueError):
            return None
        return alarm


class AlarmClock:
    """A collection of alarms with a deterministic due/next calculation."""

    def __init__(self, now: Callable[[], datetime] = _now_default) -> None:
        self._now = now
        self._alarms: List[Alarm] = []

    # -- persistence ----------------------------------------------------
    def load(self, data: Any) -> None:
        self._alarms = []
        if isinstance(data, dict):
            values = data.get("alarms", [])
        else:
            values = data or []
        if not isinstance(values, list):
            return
        for item in values:
            alarm = Alarm.from_dict(item)
            if alarm is not None:
                self._alarms.append(alarm)

    def to_dict(self) -> Dict[str, Any]:
        return {"alarms": [alarm.to_dict() for alarm in self._alarms]}

    # -- collection -----------------------------------------------------
    def all(self) -> List[Alarm]:
        return list(self._alarms)

    def get(self, alarm_id: str) -> Optional[Alarm]:
        for alarm in self._alarms:
            if alarm.id == alarm_id:
                return alarm
        return None

    def add(
        self,
        label: str = "Alarm",
        hour: int = 7,
        minute: int = 0,
        days: Optional[Iterable[int]] = None,
        sound: str = "",
        snooze_minutes: int = 5,
        enabled: bool = True,
    ) -> Alarm:
        alarm = Alarm(
            label=(label or "Alarm").strip() or "Alarm",
            hour=hour,
            minute=minute,
            days=parse_days(days or ()),
            sound=sound,
            snooze_minutes=snooze_minutes,
            enabled=enabled,
        )
        alarm.validate()
        self._alarms.append(alarm)
        self._sort()
        return alarm

    def update(self, alarm_id: str, **fields: Any) -> Alarm:
        alarm = self.get(alarm_id)
        if alarm is None:
            raise KeyError(alarm_id)
        if "days" in fields:
            fields["days"] = parse_days(fields["days"])
        for key in (
            "label",
            "hour",
            "minute",
            "days",
            "enabled",
            "sound",
            "snooze_minutes",
        ):
            if key in fields:
                setattr(alarm, key, fields[key])
        alarm.validate()
        # A changed schedule must not keep "already fired today" state.
        if any(key in fields for key in ("hour", "minute", "days")):
            alarm.last_fired = ""
            alarm.snoozed_until = 0.0
        self._sort()
        return alarm

    def remove(self, alarm_id: str) -> bool:
        before = len(self._alarms)
        self._alarms = [alarm for alarm in self._alarms if alarm.id != alarm_id]
        return len(self._alarms) != before

    def set_enabled(self, alarm_id: str, enabled: bool) -> Alarm:
        alarm = self.get(alarm_id)
        if alarm is None:
            raise KeyError(alarm_id)
        alarm.enabled = bool(enabled)
        if alarm.enabled:
            alarm.last_fired = ""
            alarm.snoozed_until = 0.0
        return alarm

    def _sort(self) -> None:
        self._alarms.sort(key=lambda alarm: (alarm.hour, alarm.minute, alarm.label.lower()))

    # -- time calculations ---------------------------------------------
    def due(self, now: Optional[datetime] = None) -> List[Alarm]:
        """Alarms that should ring at *now* (side-effect free)."""
        current = now or self._now()
        stamp = current.timestamp()
        result: List[Alarm] = []
        today = current.date().isoformat()
        for alarm in self._alarms:
            if not alarm.enabled:
                continue
            if alarm.snoozed_until:
                if stamp >= alarm.snoozed_until:
                    result.append(alarm)
                continue
            if alarm.days and current.weekday() not in alarm.days:
                continue
            scheduled = current.replace(
                hour=alarm.hour, minute=alarm.minute, second=0, microsecond=0
            )
            if current < scheduled:
                continue
            if alarm.last_fired == today:
                continue
            result.append(alarm)
        return result

    def mark_fired(self, alarm_id: str, now: Optional[datetime] = None) -> Optional[Alarm]:
        """Mark a ringing alarm as handled; one-shot alarms become disabled."""
        alarm = self.get(alarm_id)
        if alarm is None:
            return None
        current = now or self._now()
        alarm.last_fired = current.date().isoformat()
        alarm.snoozed_until = 0.0
        if not alarm.days:
            alarm.enabled = False
        return alarm

    def snooze(
        self,
        alarm_id: str,
        minutes: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Alarm]:
        alarm = self.get(alarm_id)
        if alarm is None:
            return None
        current = now or self._now()
        duration = int(minutes if minutes is not None else alarm.snooze_minutes)
        duration = max(1, min(60, duration))
        alarm.snooze_minutes = duration
        alarm.snoozed_until = current.timestamp() + duration * 60
        return alarm

    def next_occurrence(
        self, alarm: Alarm, now: Optional[datetime] = None
    ) -> Optional[datetime]:
        """The next future trigger for *alarm*, or ``None`` when disabled."""
        if not alarm.enabled:
            return None
        current = now or self._now()
        if alarm.snoozed_until and alarm.snoozed_until > current.timestamp():
            return datetime.fromtimestamp(alarm.snoozed_until)
        for offset in range(0, 9):
            day = current.date() + timedelta(days=offset)
            if alarm.days and day.weekday() not in alarm.days:
                continue
            candidate = datetime.combine(day, dt_time(alarm.hour, alarm.minute))
            if candidate <= current:
                continue
            return candidate
        return None

    def next_alarm(self, now: Optional[datetime] = None) -> Optional[Tuple[Alarm, datetime]]:
        current = now or self._now()
        pairs: List[Tuple[Alarm, datetime]] = []
        for alarm in self._alarms:
            occurrence = self.next_occurrence(alarm, current)
            if occurrence is not None:
                pairs.append((alarm, occurrence))
        if not pairs:
            return None
        return min(pairs, key=lambda pair: pair[1])

    def enabled_count(self) -> int:
        return sum(1 for alarm in self._alarms if alarm.enabled)

    def ringing_count(self, now: Optional[datetime] = None) -> int:
        return len(self.due(now))
