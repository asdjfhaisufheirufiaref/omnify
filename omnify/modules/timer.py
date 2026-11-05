"""Timer module — multiple independent countdown timers.

Model
-----
:class:`TimerManager` holds any number of :class:`TimerEntry` timers,
each with its own duration (weeks/days/hours/minutes/seconds), state and
optional action trigger. The manager is pure — no GTK, no real clock: it
takes an injectable ``clock`` (default ``time.time``) so the tests can
drive time by hand (see ``tests/test_timer.py``).

Why wall time: every run anchors on ``end_at = clock() + remaining`` in
*wall* time, so a running timer's deadline is a real timestamp that
survives closing the app, suspending, even a reboot. On load, a timer
whose deadline has already passed is reported as finished with
``missed=True`` — the time that elapsed while the machine was off counts,
which is exactly what "it must keep working while the computer is off"
means in practice.

Triggers
--------
Each timer can start itself through an action instead of the Start
button (see :meth:`TimerManager.trigger_action` and :meth:`tick`):

* ``clicks`` — after N left mouse clicks *anywhere on the desktop*;
* ``keys``   — after N presses of a recorded key combo *anywhere*;
* ``at``     — at a wall-clock timestamp (a delay or a chosen time).

"Anywhere" means the clicks/keys are counted by the process-wide
:class:`~omnify.core.input_monitor.InputMonitor`, which reads the raw
input devices (``/dev/input``) in a background thread — so a trigger
fires even while another application has the focus. That requires the
app to be running (it is, thanks to the tray + autostart) and read
access to the input devices; without it the monitor degrades gracefully
and only the ``at`` trigger works.

Persistence
-----------
:meth:`TimerManager.to_dict` / :meth:`TimerManager.load` round-trip the
whole state (plus the last used duration) so the GUI can keep it in the
shared :class:`~omnify.core.storage.Storage` after every change.
:class:`PresetStore` keeps the named one-click durations.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional

from ..core.i18n import tr
from ..core.module_context import BaseModule

#: Default duration for a fresh timer — 5 minutes, so Start is always
#: immediately usable.
DEFAULT_DURATION: Dict[str, int] = {
    "weeks": 0,
    "days": 0,
    "hours": 0,
    "minutes": 5,
    "seconds": 0,
}

#: Compact duration-field captions.  They are English source strings for the
#: translation catalogue, not user content.
DURATION_UNITS: Dict[str, str] = {
    "weeks": "W",
    "days": "D",
    "hours": "H",
    "minutes": "M",
    "seconds": "S",
}

#: Trigger kinds.
TRIGGER_MANUAL = "manual"
TRIGGER_CLICKS = "clicks"
TRIGGER_KEYS = "keys"
TRIGGER_AT = "at"
TRIGGER_KINDS = (TRIGGER_MANUAL, TRIGGER_CLICKS, TRIGGER_KEYS, TRIGGER_AT)

#: Human labels for the trigger combo box. The clicks/keys triggers
#: count input *anywhere on the desktop* (see the module docstring).
TRIGGER_LABELS = (
    (TRIGGER_MANUAL, "Manual (Start button)"),
    (TRIGGER_CLICKS, "After N mouse clicks (anywhere)"),
    (TRIGGER_KEYS, "After N key presses (anywhere)"),
    (TRIGGER_AT, "At a time (delay)"),
)


def duration_seconds(
    weeks: int, days: int, hours: int, minutes: int, seconds: int
) -> int:
    """Total seconds of a duration; validates every field.

    Raises :class:`ValueError` for non-int values (bools included),
    out-of-range values (``0 <= weeks/days <= 3650``, ``0 <= hours <=
    99``, ``0 <= minutes/seconds <= 59``) or a total of zero.
    """
    for name, value, limit in (
        ("weeks", weeks, 3650),
        ("days", days, 3650),
        ("hours", hours, 99),
        ("minutes", minutes, 59),
        ("seconds", seconds, 59),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an int, got {value!r}")
        if value < 0 or value > limit:
            raise ValueError(f"{name} out of range: {value}")
    total = weeks * 604800 + days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError("Timer duration must be positive")
    return total


def format_timer(total: float) -> str:
    """Render *total* seconds as ``HH:MM:SS`` (zero-padded).

    The seconds field is rounded up, so 0.4 s left still shows
    ``00:00:01``; 0 (and negative input) shows ``00:00:00``.
    """
    seconds = max(0, math.ceil(total))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass
class TimerEntry:
    """One timer row: duration, state and trigger configuration.

    ``state`` is one of ``"idle"``, ``"running"``, ``"paused"``,
    ``"finished"``. While running, the countdown is derived from the
    wall-clock anchor ``end_at``; ``remaining`` is authoritative in all
    other states. ``missed`` marks a timer that finished while the app
    was closed (set by :meth:`TimerManager.load`, consumed by the GUI).
    """

    id: str
    name: str = "Timer"
    weeks: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 5
    seconds: int = 0
    state: str = "idle"
    end_at: float = 0.0
    remaining: float = 0.0
    missed: bool = False
    trigger_kind: str = TRIGGER_MANUAL
    trigger_count: int = 1
    trigger_key: str = ""
    trigger_at: float = 0.0
    trigger_progress: int = 0

    @property
    def total(self) -> int:
        """Configured length in seconds (0 when the fields are all 0)."""
        if not any((self.weeks, self.days, self.hours, self.minutes, self.seconds)):
            return 0
        return duration_seconds(
            self.weeks, self.days, self.hours, self.minutes, self.seconds
        )

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "TimerEntry":
        """Rebuild an entry from a stored dict, ignoring unknown keys."""
        known = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in data.items() if k in known and v is not None}
        return cls(**clean)


class TimerManager:
    """A pure collection of countdown timers on a wall-clock anchor.

    ``clock`` must be a zero-argument callable returning seconds
    (default ``time.time`` — *wall* time, on purpose: deadlines then
    survive process restarts and machine downtime). ``on_finish`` is
    invoked with the timer id, exactly once, when a run reaches zero
    inside :meth:`tick`.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        on_finish: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._clock = clock
        self.on_finish = on_finish
        self._timers: Dict[str, TimerEntry] = {}
        self._last_duration: Dict[str, int] = dict(DEFAULT_DURATION)

    # -- collection ---------------------------------------------------------
    def add(
        self,
        name: Optional[str] = None,
        weeks: Optional[int] = None,
        days: Optional[int] = None,
        hours: Optional[int] = None,
        minutes: Optional[int] = None,
        seconds: Optional[int] = None,
    ) -> str:
        """Create a timer and return its id.

        With no duration fields the timer starts with the last used
        duration (so "remember last set time" is free); with explicit
        fields they are validated and become the new "last" duration.
        """
        if all(v is None for v in (weeks, days, hours, minutes, seconds)):
            weeks, days, hours, minutes, seconds = (
                self._last_duration[k]
                for k in ("weeks", "days", "hours", "minutes", "seconds")
            )
        else:
            weeks = weeks or 0
            days = days or 0
            hours = hours or 0
            minutes = minutes or 0
            seconds = seconds or 0
            duration_seconds(weeks, days, hours, minutes, seconds)
            self._last_duration = {
                "weeks": weeks,
                "days": days,
                "hours": hours,
                "minutes": minutes,
                "seconds": seconds,
            }
        tid = uuid.uuid4().hex[:8]
        entry = TimerEntry(
            id=tid,
            name=name or f"Timer {len(self._timers) + 1}",
            weeks=weeks,
            days=days,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )
        entry.remaining = float(entry.total)
        self._timers[tid] = entry
        return tid

    def remove(self, tid: str) -> None:
        if tid not in self._timers:
            raise KeyError(tid)
        del self._timers[tid]

    def get(self, tid: str) -> TimerEntry:
        return self._timers[tid]

    def all(self) -> List[TimerEntry]:
        """All timers in display order (insertion order)."""
        return list(self._timers.values())

    def __len__(self) -> int:
        return len(self._timers)

    def reorder(self, ordered_ids: List[str]) -> None:
        """Reorder the timers to match *ordered_ids* (drag-and-drop).

        Unknown ids and duplicates are ignored; ids missing from the
        list are appended at the end in their previous order, so a
        partial order can never lose a timer.
        """
        known = set(self._timers)
        seen: set = set()
        ordered: List[str] = []
        for tid in ordered_ids:
            if tid in known and tid not in seen:
                ordered.append(tid)
                seen.add(tid)
        for tid in self._timers:
            if tid not in seen:
                ordered.append(tid)
        self._timers = {tid: self._timers[tid] for tid in ordered}

    @property
    def last_duration(self) -> Dict[str, int]:
        """Copy of the last duration used (for new timers/presets)."""
        return dict(self._last_duration)

    def rename(self, tid: str, name: str) -> None:
        self.get(tid).name = name or "Timer"

    def set_duration(
        self,
        tid: str,
        weeks: int,
        days: int,
        hours: int,
        minutes: int,
        seconds: int,
    ) -> None:
        """Change a timer's duration; only while not running.

        Also resets the timer to ``idle`` with the new full clock and
        records the duration as the new "last" one.
        """
        entry = self.get(tid)
        if entry.state == "running":
            raise ValueError("Pause the timer before changing its duration")
        duration_seconds(weeks, days, hours, minutes, seconds)
        entry.weeks = weeks
        entry.days = days
        entry.hours = hours
        entry.minutes = minutes
        entry.seconds = seconds
        entry.remaining = float(entry.total)
        entry.state = "idle"
        entry.missed = False
        self._last_duration = {
            "weeks": weeks,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }

    # -- state transitions -----------------------------------------------------
    def start(self, tid: str) -> None:
        """Start (or restart) a timer from ``idle`` or ``finished``."""
        entry = self.get(tid)
        if entry.state not in ("idle", "finished"):
            raise ValueError(f"Cannot start from state {entry.state!r}")
        if entry.total <= 0:
            raise ValueError("Set a timer duration first")
        if entry.state == "finished":
            entry.remaining = float(entry.total)  # restart the full duration
        entry.end_at = self._clock() + entry.remaining
        entry.state = "running"
        entry.missed = False
        entry.trigger_progress = 0

    def pause(self, tid: str) -> None:
        """Freeze a running timer."""
        entry = self.get(tid)
        if entry.state != "running":
            raise ValueError(f"Cannot pause from state {entry.state!r}")
        entry.remaining = max(0.0, entry.end_at - self._clock())
        entry.state = "paused"

    def resume(self, tid: str) -> None:
        """Continue a paused timer."""
        entry = self.get(tid)
        if entry.state != "paused":
            raise ValueError(f"Cannot resume from state {entry.state!r}")
        entry.end_at = self._clock() + entry.remaining
        entry.state = "running"

    def reset(self, tid: str) -> None:
        """Back to ``idle`` with a full clock; valid from any state."""
        entry = self.get(tid)
        entry.state = "idle"
        entry.remaining = float(entry.total) if entry.total > 0 else 0.0
        entry.end_at = 0.0
        entry.missed = False
        entry.trigger_progress = 0

    def remaining_of(self, tid: str) -> float:
        """Seconds left (derived from the anchor while running)."""
        entry = self.get(tid)
        if entry.state == "running":
            return max(0.0, entry.end_at - self._clock())
        return entry.remaining

    # -- triggers -----------------------------------------------------------------
    def set_trigger(
        self,
        tid: str,
        kind: str,
        count: int = 1,
        key: str = "",
        at: float = 0.0,
    ) -> None:
        """Configure the auto-start trigger of a timer (never raises
        except for a bad *kind* or a bad count)."""
        if kind not in TRIGGER_KINDS:
            raise ValueError(f"Unknown trigger kind {kind!r}")
        entry = self.get(tid)
        if kind in (TRIGGER_CLICKS, TRIGGER_KEYS):
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("Trigger count must be a positive int")
            entry.trigger_count = count
        entry.trigger_kind = kind
        entry.trigger_key = key if kind == TRIGGER_KEYS else ""
        entry.trigger_at = float(at) if kind == TRIGGER_AT and at else 0.0
        entry.trigger_progress = 0

    def trigger_action(self, tid: str, kind: str = TRIGGER_CLICKS) -> bool:
        """Report one user action (a click, or a matching key press).

        For ``clicks``/``keys`` triggers this advances the progress
        counter; when it reaches the configured count the timer starts,
        the trigger disarms and ``True`` is returned (once per arming).
        Any other situation returns ``False``.
        """
        entry = self.get(tid)
        if entry.state not in ("idle", "finished"):
            return False
        if entry.trigger_kind not in (TRIGGER_CLICKS, TRIGGER_KEYS):
            return False
        if kind != entry.trigger_kind:
            return False
        entry.trigger_progress += 1
        if entry.trigger_progress >= entry.trigger_count:
            self.start(tid)
            entry.trigger_kind = TRIGGER_MANUAL
            entry.trigger_progress = 0
            return True
        return False

    # -- ticking ---------------------------------------------------------------------
    def tick(self) -> List[str]:
        """Advance every timer; return the ids that finished now.

        A running timer whose deadline has passed becomes ``finished``
        (``on_finish`` fires, once). Also handles ``at``-triggered
        auto-starts: a timer in ``idle`` or ``finished`` state whose
        ``trigger_at`` has passed starts itself and is disarmed.
        """
        now = self._clock()
        finished: List[str] = []
        for entry in self._timers.values():
            if entry.state == "running" and now >= entry.end_at:
                entry.state = "finished"
                entry.remaining = 0.0
                finished.append(entry.id)
                if self.on_finish is not None:
                    self.on_finish(entry.id)
            elif (
                entry.state in ("idle", "finished")
                and entry.trigger_kind == TRIGGER_AT
                and entry.trigger_at > 0
                and now >= entry.trigger_at
            ):
                entry.trigger_kind = TRIGGER_MANUAL
                entry.trigger_at = 0.0
                if entry.total > 0:
                    self.start(entry.id)
        return finished

    # -- persistence -------------------------------------------------------------------
    def to_dict(self) -> Dict:
        """The whole manager state as a JSON-friendly dict."""
        return {
            "timers": [entry.to_dict() for entry in self._timers.values()],
            "last_duration": dict(self._last_duration),
        }

    def load(self, data: Dict) -> List[str]:
        """Restore a saved state; return ids that finished while away.

        A saved ``running`` timer whose ``end_at`` has already passed is
        treated as finished with ``missed=True`` (it completed while the
        app was closed); otherwise it simply resumes counting. Corrupt
        or unknown entries are skipped.
        """
        self._timers.clear()
        if not isinstance(data, dict):
            return []
        last = data.get("last_duration")
        if isinstance(last, dict):
            clean_last = {}
            for key, fallback in DEFAULT_DURATION.items():
                try:
                    clean_last[key] = int(last.get(key, fallback))
                except (TypeError, ValueError):
                    clean_last[key] = fallback
            self._last_duration = clean_last
        missed: List[str] = []
        for item in data.get("timers", []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            try:
                entry = TimerEntry.from_dict(item)
            except (TypeError, ValueError, KeyError):
                continue
            self._timers[entry.id] = entry
            if entry.state == "running":
                if entry.end_at and self._clock() >= entry.end_at:
                    entry.state = "finished"
                    entry.remaining = 0.0
                    entry.missed = True
                    missed.append(entry.id)
        return missed


class PresetStore:
    """Named one-click durations, persisted in the shared Storage."""

    KEY = "timer_presets"

    def __init__(self, storage) -> None:
        self._storage = storage

    def all(self) -> List[Dict]:
        data = self._storage.load(self.KEY, [])
        if not isinstance(data, list):
            return []
        return [p for p in data if isinstance(p, dict) and p.get("label")]

    def add(
        self,
        label: str,
        weeks: int = 0,
        days: int = 0,
        hours: int = 0,
        minutes: int = 5,
        seconds: int = 0,
    ) -> Dict:
        """Store a new preset; returns it. Raises :class:`ValueError`
        for an empty label or a zero/invalid duration."""
        label = (label or "").strip()
        if not label:
            raise ValueError("Preset needs a name")
        duration_seconds(weeks, days, hours, minutes, seconds)
        preset = {
            "id": uuid.uuid4().hex[:8],
            "label": label[:24],
            "weeks": weeks,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }
        items = self.all()
        items.append(preset)
        self._storage.save(self.KEY, items)
        return preset

    def upsert(
        self,
        label: str,
        weeks: int = 0,
        days: int = 0,
        hours: int = 0,
        minutes: int = 5,
        seconds: int = 0,
    ) -> Dict:
        """Store a preset, replacing any existing preset with the same
        label (compared case-insensitively). Returns the stored preset.

        This is the "save this timer as a preset" path: saving the same
        timer twice updates the preset in place instead of accumulating
        duplicates. Raises :class:`ValueError` for an empty label or an
        invalid duration.
        """
        label = (label or "").strip()
        if not label:
            raise ValueError("Preset needs a name")
        duration_seconds(weeks, days, hours, minutes, seconds)
        items = self.all()
        for existing in items:
            if str(existing.get("label", "")).lower() == label.lower():
                existing.update(
                    {
                        "weeks": weeks,
                        "days": days,
                        "hours": hours,
                        "minutes": minutes,
                        "seconds": seconds,
                    }
                )
                self._storage.save(self.KEY, items)
                return existing
        preset = {
            "id": uuid.uuid4().hex[:8],
            "label": label[:24],
            "weeks": weeks,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }
        items.append(preset)
        self._storage.save(self.KEY, items)
        return preset

    def remove(self, preset_id: str) -> None:
        items = [p for p in self.all() if p.get("id") != preset_id]
        self._storage.save(self.KEY, items)


# ---------------------------------------------------------------------------
# GTK view
# ---------------------------------------------------------------------------
class TimerModule(BaseModule):
    """The timer tool: a list of independent countdown timer cards."""

    id = "timer"
    name = "Timer"
    icon = "⏱"
    description = (
        "Multiple countdown timers with presets, action triggers, "
        "sound and background notifications."
    )
    group = "Tools"

    _TICK_MS = 500
    _STATE_KEY = "timers"
    _BG_NOTIFY_ID = 1001  # the quiet persistent "time left" notification
    _FINISH_NOTIFY_ID = 1002
    _BG_UPDATE_SECONDS = 60.0  # how often the background note refreshes

    #: Sound files probed when the user never chose one themselves.
    _DEFAULT_SOUND_CANDIDATES = (
        "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
        "/usr/share/sounds/freedesktop/stereo/complete.oga",
        "/usr/share/sounds/kde/freedesktop/stereo/complete.oga",
    )

    def __init__(self, context) -> None:
        super().__init__(context)
        from ..core.alerts import Notifier, SoundPlayer

        self._player = SoundPlayer()
        self._notifier = Notifier()

    # -- construction -------------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        self._manager = TimerManager(on_finish=lambda tid: None)
        self._presets = PresetStore(self.ctx.storage)
        self._missed: List[str] = self._manager.load(
            self.ctx.storage.load(self._STATE_KEY, {})
        )
        self._cards: Dict[str, Dict] = {}
        self._recording_for: Optional[str] = None
        self._trigger_grace_until = 0.0
        self._bg_visible = False
        self._last_bg_notify = 0.0
        self._root = None
        self._drag_state: Optional[Dict] = None  # active card drag, if any

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self._root = root

        # --- toolbar ---
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_button = Gtk.Button(label="+ Add timer")
        add_button.get_style_context().add_class("omnify-accent")
        add_button.connect("clicked", self._on_add_timer)
        toolbar.append(add_button)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)
        hint = Gtk.Label(
            label=(
                "Timers keep counting while the app is closed — "
                "click/key triggers count input anywhere on the desktop."
            ),
            xalign=1.0,
        )
        hint.get_style_context().add_class("omnify-dim")
        toolbar.append(hint)
        root.append(toolbar)

        # --- timer cards ---
        self._timers_list = Gtk.ListBox()
        self._timers_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._timers_list)
        root.append(scroll)

        # --- presets --- (chips; the 💾 button on each card saves that
        # timer's duration as a preset)
        presets_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        caption = Gtk.Label(label="Presets:", xalign=0.0)
        caption.get_style_context().add_class("omnify-dim")
        presets_row.append(caption)
        self._presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        presets_row.append(self._presets_box)
        presets_row.append(Gtk.Box())
        preset_hint = Gtk.Label(
            label="💾 on a timer card saves it as a preset", xalign=1.0
        )
        preset_hint.get_style_context().add_class("omnify-dim")
        presets_row.append(preset_hint)
        root.append(presets_row)

        # --- finish alerts settings ---
        root.append(self._build_alerts_expander())

        # key capture for "keys" triggers (and Escape while recording);
        # GTK4 EventControllerKey emits "key-pressed", not the GTK3
        # "key-press-event" name. Note: this handler only *records*
        # combos — the counting itself happens in the process-wide input
        # monitor below, which also sees presses outside this window.
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_press)
        root.add_controller(key_controller)

        # global input counting (clicks/keys anywhere on the desktop);
        # the monitor is process-wide and the stopwatch reuses it
        from ..core.input_monitor import get_default_monitor

        monitor = get_default_monitor()
        monitor.subscribe_click(self._handle_global_click)
        monitor.subscribe_key_combo(self._handle_global_key)
        if not monitor.start() and monitor.error:
            self.ctx.notify(monitor.error)

        if len(self._manager) == 0:
            self._add_card(self._manager.add())
        else:
            for entry in self._manager.all():
                self._add_card(entry.id)
        self._refresh_presets()
        self._refresh_all()
        self._start_ticker()
        return root

    # -- card construction ------------------------------------------------------
    def _add_card(self, tid: str) -> None:
        from gi.repository import Gtk

        entry = self._manager.get(tid)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")

        # row 1: drag handle, name, remaining time, state, save preset,
        # delete
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        handle = Gtk.Label(label="⋮⋮")
        handle.get_style_context().add_class("omnify-drag-handle")
        handle.set_tooltip_text("Drag to reorder timers")
        self._attach_drag_gesture(handle, tid)
        top.append(handle)
        name_entry = Gtk.Entry()
        name_entry.set_text(entry.name)
        name_entry.set_width_chars(12)
        name_entry.connect("changed", self._on_name_changed, tid)
        top.append(name_entry)
        remaining_label = Gtk.Label(label=format_timer(entry.remaining))
        remaining_label.get_style_context().add_class("omnify-mono")
        remaining_label.get_style_context().add_class("omnify-card-title")
        remaining_label.set_hexpand(True)
        top.append(remaining_label)
        state_label = Gtk.Label(label=tr(entry.state))
        state_label.get_style_context().add_class("omnify-dim")
        top.append(state_label)
        save_preset_button = Gtk.Button(label="💾")
        save_preset_button.set_tooltip_text(
            "Save this timer's duration as a preset "
            "(the same name updates the existing preset)"
        )
        save_preset_button.connect("clicked", self._on_save_preset, tid)
        top.append(save_preset_button)
        delete_button = Gtk.Button(label="✕")
        delete_button.set_tooltip_text("Delete this timer")
        delete_button.connect("clicked", self._on_delete_timer, tid)
        top.append(delete_button)
        card.append(top)

        # row 2: duration fields; weeks/days are hidden until the "+"
        # button is pressed (a few-minute timer does not need them)
        dur = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        spins: Dict[str, Gtk.SpinButton] = {}
        dw_widgets: List[Gtk.Widget] = []
        for key, value, upper in (
            ("weeks", entry.weeks, 3650),
            ("days", entry.days, 3650),
            ("hours", entry.hours, 99),
            ("minutes", entry.minutes, 59),
            ("seconds", entry.seconds, 59),
        ):
            caption = Gtk.Label(label=tr(DURATION_UNITS.get(key, key[0].upper())))
            caption.get_style_context().add_class("omnify-dim")
            dur.append(caption)
            spin = Gtk.SpinButton.new_with_range(0, upper, 1)
            spin.set_value(value)
            spin.connect("value-changed", self._on_duration_changed, tid)
            spins[key] = spin
            dur.append(spin)
            if key in ("weeks", "days"):
                dw_widgets.append(caption)
                dw_widgets.append(spin)
        dw_open = bool(entry.weeks or entry.days)
        for widget in dw_widgets:
            widget.set_visible(dw_open)
        expand_button = Gtk.Button(label="−" if dw_open else "+")
        expand_button.set_tooltip_text("Show or hide weeks and days")
        expand_button.connect("clicked", self._on_expand_dw, tid)
        dur.append(expand_button)
        card.append(dur)

        # row 3: controls + trigger toggle
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start_button = Gtk.Button(label="Start")
        start_button.get_style_context().add_class("omnify-accent")
        start_button.connect("clicked", self._on_start, tid)
        pause_button = Gtk.Button(label="Pause")
        pause_button.connect("clicked", self._on_pause, tid)
        resume_button = Gtk.Button(label="Resume")
        resume_button.connect("clicked", self._on_resume, tid)
        reset_button = Gtk.Button(label="Reset")
        reset_button.connect("clicked", self._on_reset, tid)
        for button in (start_button, pause_button, resume_button, reset_button):
            controls.append(button)
        controls.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        trigger_button = Gtk.ToggleButton(label=tr("⚡ Trigger"))
        trigger_button.set_tooltip_text("Start automatically after an action")
        trigger_button.connect("toggled", self._on_trigger_toggled, tid)
        controls.append(trigger_button)
        card.append(controls)

        # trigger settings (revealer)
        trigger_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        kind_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        kind_row.append(Gtk.Label(label="Start automatically:", xalign=0.0))
        kind_combo = Gtk.ComboBoxText()
        for kind, label in TRIGGER_LABELS:
            kind_combo.append(kind, label)
        kind_combo.connect("changed", self._on_trigger_kind_changed, tid)
        kind_row.append(kind_combo)
        trigger_box.append(kind_row)

        count_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        count_row.append(Gtk.Label(label="N ="))
        count_spin = Gtk.SpinButton.new_with_range(1, 99, 1)
        count_spin.set_value(entry.trigger_count or 1)
        count_spin.connect("value-changed", self._on_trigger_kind_changed, tid)
        count_row.append(count_spin)
        count_row.append(Gtk.Label(label="actions"))
        trigger_box.append(count_row)

        key_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        key_row.append(Gtk.Label(label="Key:"))
        record_button = Gtk.Button(label="Record key…")
        record_button.connect("clicked", self._on_record_key, tid)
        key_row.append(record_button)
        key_label = Gtk.Label(label=entry.trigger_key or "—")
        key_label.get_style_context().add_class("omnify-dim")
        key_row.append(key_label)
        trigger_box.append(key_row)

        at_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        at_row.append(Gtk.Label(label="Start in (min):"))
        at_spin = Gtk.SpinButton.new_with_range(1, 1440, 1)
        at_row.append(at_spin)
        arm_button = Gtk.Button(label="Arm")
        arm_button.connect("clicked", self._on_arm_at, tid)
        at_row.append(arm_button)
        armed_label = Gtk.Label(label="", xalign=0.0)
        armed_label.get_style_context().add_class("omnify-dim")
        at_row.append(armed_label)
        trigger_box.append(at_row)

        progress_label = Gtk.Label(label=tr("armed"), xalign=0.0)
        progress_label.get_style_context().add_class("omnify-dim")
        trigger_box.append(progress_label)

        trigger_revealer = Gtk.Revealer()
        trigger_revealer.set_child(trigger_box)
        card.append(trigger_revealer)

        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_child(card)
        self._timers_list.append(row)

        self._cards[tid] = {
            "row": row,
            "card": card,
            "handle": handle,
            "name": name_entry,
            "remaining": remaining_label,
            "state": state_label,
            "spins": spins,
            "dw_widgets": dw_widgets,
            "expand_button": expand_button,
            "save_preset": save_preset_button,
            "start": start_button,
            "pause": pause_button,
            "resume": resume_button,
            "reset": reset_button,
            "trigger_button": trigger_button,
            "trigger_revealer": trigger_revealer,
            "kind_combo": kind_combo,
            "count_row": count_row,
            "count_spin": count_spin,
            "key_row": key_row,
            "key_label": key_label,
            "record_button": record_button,
            "at_row": at_row,
            "at_spin": at_spin,
            "armed_label": armed_label,
            "progress_label": progress_label,
        }

    # -- drag & drop reordering ---------------------------------------------------
    def _attach_drag_gesture(self, handle, tid: str) -> None:
        """Manual drag-reorder: this GTK build has no ``Gtk.Reorderer``,
        so the ⋮⋮ handle carries a :class:`Gtk.GestureDrag` that live-
        reorders the listbox with ``ListBox.reorder_row`` while the
        pointer moves. Silently skipped when the build cannot reorder.
        """
        from gi.repository import Gtk

        if not hasattr(Gtk.ListBox, "reorder_row"):
            return
        gesture = Gtk.GestureDrag.new()
        gesture.connect("began", self._on_drag_began, tid)
        gesture.connect("changed", self._on_drag_changed, tid)
        gesture.connect("ended", self._on_drag_ended, tid)
        gesture.connect("cancelled", self._on_drag_cancelled, tid)
        handle.add_controller(gesture)

    def _on_drag_began(self, _gesture, _x: float, _y: float, tid: str) -> None:
        if self._drag_state is not None:
            return  # one drag at a time
        self._drag_state = {"tid": tid}
        widgets = self._cards.get(tid)
        if widgets is not None:
            widgets["card"].get_style_context().add_class("omnify-dragging")

    def _on_drag_changed(self, _gesture, _dx: float, dy: float, tid: str) -> None:
        if self._drag_state is None or self._drag_state.get("tid") != tid:
            return
        widgets = self._cards.get(tid)
        if widgets is None:
            return
        # Pointer Y in the listbox's coordinate space: the handle's
        # offset (translated) plus the drag delta (gesture coordinates
        # are relative to the pressed point on the handle).
        ok, hx, hy = widgets["handle"].translate_coordinates(
            self._timers_list, 0, 0
        )
        if not ok:
            return
        pointer_y = hy + dy
        order = [entry.id for entry in self._manager.all()]
        rows = [
            self._timers_list.get_row_at_index(i) for i in range(len(order))
        ]
        row = widgets["row"]
        if row not in rows:
            return
        current = rows.index(row)
        target = len(rows) - 1
        for i, candidate in enumerate(rows):
            alloc = candidate.get_allocation()
            if pointer_y < alloc.y + alloc.height:
                target = i
                break
        if target == current:
            return
        self._timers_list.reorder_row(row, rows[target])
        # keep the model's order in step with the visual one
        order.remove(tid)
        order.insert(target, tid)
        self._manager.reorder(order)

    def _on_drag_ended(self, _gesture, tid: str) -> None:
        self._finish_drag(tid)
        self.save_state()

    def _on_drag_cancelled(self, _gesture, tid: str) -> None:
        self._finish_drag(tid)

    def _finish_drag(self, tid: str) -> None:
        if self._drag_state is not None and self._drag_state.get("tid") == tid:
            widgets = self._cards.get(tid)
            if widgets is not None:
                widgets["card"].get_style_context().remove_class(
                    "omnify-dragging"
                )
            self._drag_state = None

    # -- alerts expander ----------------------------------------------------------
    def _build_alerts_expander(self):
        from gi.repository import Gtk

        settings = self.ctx.settings.get("alerts", {}) or {}
        expander = Gtk.Expander(label="Sounds & notifications")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)

        sound_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sound_row.append(Gtk.Label(label="Play sound on finish:"))
        self._sound_switch = Gtk.Switch()
        self._sound_switch.set_active(bool(settings.get("sound_enabled", True)))
        self._sound_switch.connect("state-set", self._on_sound_switch)
        sound_row.append(self._sound_switch)
        box.append(sound_row)

        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        file_row.append(Gtk.Label(label="Sound file:"))
        self._sound_file_label = Gtk.Label(
            label=settings.get("sound_file") or "default (system alarm)", xalign=0.0
        )
        self._sound_file_label.get_style_context().add_class("omnify-dim")
        file_row.append(self._sound_file_label)
        choose_button = Gtk.Button(label="Choose…")
        choose_button.connect("clicked", self._on_choose_sound)
        file_row.append(choose_button)
        test_button = Gtk.Button(label="Test")
        test_button.connect("clicked", self._on_test_sound)
        file_row.append(test_button)
        box.append(file_row)

        length_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        length_row.append(Gtk.Label(label="Sound length (s, 0 = play once):"))
        self._sound_length_spin = Gtk.SpinButton.new_with_range(0, 600, 1)
        self._sound_length_spin.set_value(int(settings.get("sound_seconds", 5)))
        self._sound_length_spin.connect("value-changed", self._on_sound_length)
        length_row.append(self._sound_length_spin)
        box.append(length_row)

        timeout_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        timeout_row.append(Gtk.Label(label="Finish notification auto-close:"))
        self._timeout_combo = Gtk.ComboBoxText()
        for value, label in (("5000", "5 seconds"), ("10000", "10 seconds"), ("-1", "Never")):
            self._timeout_combo.append(value, label)
        # this GTK build lacks ComboBoxText.find; match by item id instead
        stored_timeout = str(int(settings.get("notify_timeout", 5000)))
        if stored_timeout in ("5000", "10000", "-1"):
            self._timeout_combo.set_active_id(stored_timeout)
        self._timeout_combo.connect("changed", self._on_timeout_changed)
        timeout_row.append(self._timeout_combo)
        box.append(timeout_row)

        # --- start sound (one-shot; manual start or any trigger) ---
        start_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        start_row.append(Gtk.Label(label="Play sound on start:"))
        self._start_sound_switch = Gtk.Switch()
        self._start_sound_switch.set_active(
            bool(settings.get("start_sound_enabled", False))
        )
        self._start_sound_switch.connect(
            "state-set", self._on_start_sound_switch
        )
        start_row.append(self._start_sound_switch)
        box.append(start_row)

        start_file_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        start_file_row.append(Gtk.Label(label="Start sound file:"))
        self._start_sound_file_label = Gtk.Label(
            label=settings.get("start_sound_file") or "same as the finish sound",
            xalign=0.0,
        )
        self._start_sound_file_label.get_style_context().add_class("omnify-dim")
        start_file_row.append(self._start_sound_file_label)
        choose_start = Gtk.Button(label="Choose…")
        choose_start.connect("clicked", self._on_choose_start_sound)
        start_file_row.append(choose_start)
        test_start = Gtk.Button(label="Test")
        test_start.connect("clicked", self._on_test_start_sound)
        start_file_row.append(test_start)
        box.append(start_file_row)

        expander.set_child(box)
        return expander

    # -- UI sync -------------------------------------------------------------------
    def _sync_card(self, tid: str) -> None:
        widgets = self._cards.get(tid)
        if widgets is None:
            return
        entry = self._manager.get(tid)
        widgets["remaining"].set_label(
            format_timer(self._manager.remaining_of(tid))
        )
        state_text = tr(entry.state)
        if entry.missed:
            state_text += " " + tr("(finished away)")
        widgets["state"].set_label(state_text)
        running = entry.state == "running"
        widgets["start"].set_sensitive(
            entry.state in ("idle", "finished") and entry.total > 0
        )
        widgets["pause"].set_sensitive(running)
        widgets["resume"].set_sensitive(entry.state == "paused")
        widgets["reset"].set_sensitive(entry.state != "idle")
        for spin in widgets["spins"].values():
            spin.set_sensitive(entry.state in ("idle", "finished"))
        # trigger UI
        index = (
            TRIGGER_KINDS.index(entry.trigger_kind)
            if entry.trigger_kind in TRIGGER_KINDS
            else 0
        )
        if widgets["kind_combo"].get_active() != index:
            widgets["kind_combo"].set_active(index)
        self._sync_trigger_rows(tid, entry)
        if entry.trigger_kind in (TRIGGER_CLICKS, TRIGGER_KEYS):
            widgets["progress_label"].set_label(
                tr(
                    "counting: {current} / {target}",
                    current=entry.trigger_progress,
                    target=entry.trigger_count,
                )
            )
        elif entry.trigger_kind == TRIGGER_AT and entry.trigger_at > 0:
            when = time.strftime("%H:%M", time.localtime(entry.trigger_at))
            widgets["armed_label"].set_label(
                tr("will start at {when}", when=when)
            )

    def _sync_trigger_rows(self, tid: str, entry: "TimerEntry") -> None:
        widgets = self._cards[tid]
        needs_count = entry.trigger_kind in (TRIGGER_CLICKS, TRIGGER_KEYS)
        widgets["count_row"].set_visible(needs_count)
        widgets["key_row"].set_visible(entry.trigger_kind == TRIGGER_KEYS)
        widgets["at_row"].set_visible(entry.trigger_kind == TRIGGER_AT)
        widgets["progress_label"].set_visible(needs_count)
        widgets["kind_combo"].set_sensitive(entry.state != "running")

    def _refresh_all(self) -> None:
        for tid in list(self._cards):
            try:
                self._sync_card(tid)
            except KeyError:
                pass

    def _refresh_presets(self) -> None:
        from gi.repository import Gtk

        while True:
            child = self._presets_box.get_first_child()
            if child is None:
                break
            self._presets_box.remove(child)
        for preset in self._presets.all():
            chip = Gtk.Button(label=f"⏱ {preset['label']}")
            text = (
                f"{preset.get('weeks', 0)}w {preset.get('days', 0)}d "
                f"{preset.get('hours', 0)}h {preset.get('minutes', 0)}m "
                f"{preset.get('seconds', 0)}s"
            )
            chip.set_tooltip_text(f"{text} — start now with one click")
            chip.connect("clicked", self._on_preset_clicked, preset)
            self._presets_box.append(chip)
            delete = Gtk.Button(label="✕")
            delete.set_tooltip_text("Delete this preset")
            delete.connect("clicked", self._on_preset_delete, preset["id"])
            self._presets_box.append(delete)

    # -- ticker -----------------------------------------------------------------------
    def _start_ticker(self) -> None:
        from gi.repository import GLib

        if getattr(self, "_timeout_id", None) is None:
            self._timeout_id = GLib.timeout_add(self._TICK_MS, self._on_tick)

    def _stop_ticker(self) -> None:
        from gi.repository import GLib

        if getattr(self, "_timeout_id", None) is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def on_show(self) -> None:
        if getattr(self, "_manager", None) is None:
            return
        self._refresh_all()
        self._refresh_presets()
        self._start_ticker()

    def on_hide(self) -> None:
        # The model keeps running (wall-clock anchors); the shell's global
        # ticker keeps pumping it, so only the local redraw stops.
        self._stop_ticker()

    def _on_tick(self) -> bool:
        self.pump()
        self._refresh_all()
        return True

    # -- shell integration ------------------------------------------------------------
    def pump(self) -> bool:
        """Advance all timers and handle finishes; True if one finished.

        Called by the local ticker (view visible) and by the shell's
        global 1-second ticker (always), so timers complete even while
        another module is on screen. Also notices "at" auto-starts
        (timers that ``tick()`` started by itself) and plays the start
        sound for them.
        """
        if getattr(self, "_manager", None) is None:
            return False
        running_before = {
            entry.id for entry in self._manager.all()
            if entry.state == "running"
        }
        finished = self._manager.tick()
        for entry in self._manager.all():
            if entry.state == "running" and entry.id not in running_before:
                self._play_start_sound(entry.name)
                self.ctx.notify(f"{entry.name} started (time trigger)")
        if self._missed:
            finished = self._missed + finished
            self._missed = []
        for tid in finished:
            self._handle_finished(tid)
        self._update_background_notify()
        return bool(finished)

    def save_state(self) -> None:
        """Persist timers (the shell also calls this on shutdown)."""
        if getattr(self, "_manager", None) is None:
            return
        self.ctx.storage.save(self._STATE_KEY, self._manager.to_dict())

    def tray_snapshot(self) -> Optional[str]:
        """One line for the system tray, or None when nothing runs."""
        if getattr(self, "_manager", None) is None:
            return None
        running = [e for e in self._manager.all() if e.state == "running"]
        if not running:
            return None
        shortest = min(
            running, key=lambda e: self._manager.remaining_of(e.id)
        )
        text = format_timer(self._manager.remaining_of(shortest.id))
        if len(running) == 1:
            return f"⏱ {text}"
        return f"⏱ {len(running)} timers · next {text}"

    def mini_lines(self) -> List[str]:
        """Rows for the floating mini-window (running/paused timers)."""
        if getattr(self, "_manager", None) is None:
            return []
        lines = []
        for entry in self._manager.all():
            if entry.state in ("running", "paused"):
                lines.append(
                    f"{entry.name}  {format_timer(self._manager.remaining_of(entry.id))}"
                )
        return lines

    # -- finish handling ------------------------------------------------------------------
    def _alerts_settings(self) -> Dict:
        return dict(self.ctx.settings.get("alerts", {}) or {})

    def _save_alerts(self) -> None:
        settings = self._alerts_settings()
        settings["sound_enabled"] = bool(self._sound_switch.get_active())
        settings["sound_seconds"] = int(self._sound_length_spin.get_value())
        settings["notify_timeout"] = int(
            self._timeout_combo.get_active_id() or 5000
        )
        settings["start_sound_enabled"] = bool(
            self._start_sound_switch.get_active()
        )
        self.ctx.settings.set("alerts", settings)

    def _resolve_sound_file(self, settings: Dict) -> str:
        path = settings.get("sound_file") or ""
        if path and os.path.exists(path):
            return path
        for candidate in self._DEFAULT_SOUND_CANDIDATES:
            if os.path.exists(candidate):
                return candidate
        return ""

    def _play_finish_sound(self, settings: Dict) -> bool:
        """Play the configured finish sound; True when playback started."""
        if not settings.get("sound_enabled", True):
            return False
        sound = self._resolve_sound_file(settings)
        if not sound:
            return False
        duration = settings.get("sound_seconds", 0)
        try:
            return bool(self._player.play(sound, duration=duration or None))
        except Exception:
            return False

    def _play_start_sound(self, name: str) -> None:
        """Play the optional one-shot start sound (trigger auto-starts).

        Uses ``start_sound_file`` when it exists, otherwise falls back
        to the finish sound's file (including the system default).
        """
        settings = self._alerts_settings()
        if not settings.get("start_sound_enabled", False):
            return
        path = settings.get("start_sound_file") or ""
        if not path or not os.path.exists(path):
            path = self._resolve_sound_file(settings)
        if not path:
            return
        try:
            ok = self._player.play(path, duration=None)
        except Exception:
            ok = False
        from ..core.logfile import log

        log(f"start sound for {name!r}: ok={ok} file={path!r}")

    def _handle_finished(self, tid: str) -> None:
        from ..core.logfile import log

        try:
            entry = self._manager.get(tid)
        except KeyError:
            return
        name = entry.name
        missed = entry.missed
        entry.missed = False
        settings = self._alerts_settings()
        sound_ok = self._play_finish_sound(settings)
        notify_ok = False
        if self._notifier.available:
            body = f"{name} ran out"
            if missed:
                body += " (it finished while Omnify was closed)"
            try:
                timeout = int(settings.get("notify_timeout", 5000))
            except (TypeError, ValueError):
                timeout = 5000
            try:
                notify_ok = self._notifier.notify(
                    "Timer finished",
                    body,
                    urgency="normal",
                    timeout_ms=timeout,
                    replace_id=self._FINISH_NOTIFY_ID,
                )
            except Exception:
                notify_ok = False
        log(
            f"timer {name!r} finished: sound_ok={sound_ok} "
            f"notify_ok={notify_ok} missed={missed}"
        )
        self.ctx.notify(f"{tr('Timer finished:')} {name}")
        self._sync_card(tid)
        self.save_state()

    def _update_background_notify(self) -> None:
        """The quiet persistent 'x left' note (one entry, updated in place)."""
        if not self._notifier.available:
            return
        active = [
            e for e in self._manager.all() if e.state in ("running", "paused")
        ]
        now = time.time()
        if not active:
            if self._bg_visible:
                self._notifier.dismiss(self._BG_NOTIFY_ID)
                self._bg_visible = False
            return
        if self._bg_visible and now - self._last_bg_notify < self._BG_UPDATE_SECONDS:
            return
        self._bg_visible = True
        self._last_bg_notify = now
        running = [e for e in active if e.state == "running"]
        if running:
            shortest = min(running, key=lambda e: self._manager.remaining_of(e.id))
            text = format_timer(self._manager.remaining_of(shortest.id))
            if len(running) == 1:
                body = tr(
                    "{name}: {time} left",
                    name=shortest.name,
                    time=text,
                )
            else:
                body = tr(
                    "{count} timers running — next: {name}, {time}",
                    count=len(running),
                    name=shortest.name,
                    time=text,
                )
            title = tr("Timer running")
        else:
            entry = active[0]
            body = tr(
                "{name} paused — {time} left",
                name=entry.name,
                time=format_timer(self._manager.remaining_of(entry.id)),
            )
            title = "Timer paused"
        self._notifier.notify(
            title, body, urgency="low", timeout_ms=-1, replace_id=self._BG_NOTIFY_ID
        )

    # -- handlers ------------------------------------------------------------------------
    def _on_add_timer(self, _button) -> None:
        tid = self._manager.add()
        self._add_card(tid)
        self._refresh_all()
        self.save_state()

    def _on_delete_timer(self, _button, tid: str) -> None:
        self._manager.remove(tid)
        widgets = self._cards.pop(tid, None)
        if widgets is not None:
            self._timers_list.remove(widgets["row"])
        self.save_state()

    def _on_name_changed(self, entry, tid: str) -> None:
        try:
            self._manager.rename(tid, entry.get_text())
        except KeyError:
            pass

    def _on_duration_changed(self, _spin, tid: str) -> None:
        try:
            self._manager.set_duration(
                tid,
                int(self._cards[tid]["spins"]["weeks"].get_value()),
                int(self._cards[tid]["spins"]["days"].get_value()),
                int(self._cards[tid]["spins"]["hours"].get_value()),
                int(self._cards[tid]["spins"]["minutes"].get_value()),
                int(self._cards[tid]["spins"]["seconds"].get_value()),
            )
        except (KeyError, ValueError):
            return
        self._sync_card(tid)
        self.save_state()

    def _on_expand_dw(self, button, tid: str) -> None:
        """+ / − — show or hide the weeks and days fields of this card."""
        widgets = self._cards[tid]
        dw = widgets["dw_widgets"]
        if not dw:
            return
        currently_open = dw[0].get_visible()
        for widget in dw:
            widget.set_visible(not currently_open)
        button.set_label("+" if currently_open else "−")

    def _on_start(self, _button, tid: str) -> None:
        try:
            self._manager.start(tid)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._play_start_sound(self._manager.get(tid).name)
        self._sync_card(tid)
        self.save_state()

    def start_named_timer(self, name: str) -> bool:
        """Start an idle/finished timer by its (case-insensitive) name.

        Used by calendar event actions. Returns ``True`` when a timer was
        actually started; a missing or busy timer leaves everything as is.
        """
        target = (name or "").strip().lower()
        if not target:
            return False
        for entry in self._manager.all():
            if (entry.name or "").strip().lower() != target:
                continue
            if entry.state not in ("idle", "finished"):
                return False
            try:
                self._manager.start(entry.id)
            except ValueError:
                return False
            self._play_start_sound(entry.name)
            self._sync_card(entry.id)
            self.save_state()
            return True
        return False

    def _on_pause(self, _button, tid: str) -> None:
        try:
            self._manager.pause(tid)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._last_bg_notify = 0.0  # refresh the quiet note right away
        self._sync_card(tid)
        self.save_state()

    def _on_resume(self, _button, tid: str) -> None:
        try:
            self._manager.resume(tid)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._last_bg_notify = 0.0
        self._sync_card(tid)
        self.save_state()

    def _on_reset(self, _button, tid: str) -> None:
        self._manager.reset(tid)
        self._sync_card(tid)
        self.save_state()

    # -- triggers -----------------------------------------------------------------------------
    def _on_trigger_toggled(self, button, tid: str) -> None:
        widgets = self._cards[tid]
        widgets["trigger_revealer"].set_reveal_child(bool(button.get_active()))

    def _on_trigger_kind_changed(self, _widget, tid: str) -> None:
        widgets = self._cards[tid]
        index = widgets["kind_combo"].get_active()
        if index < 0:
            return
        kind = TRIGGER_KINDS[index]
        count = int(widgets["count_spin"].get_value())
        entry = self._manager.get(tid)
        key = entry.trigger_key if kind == TRIGGER_KEYS else ""
        # The very click that changed this spin/combo is seen by the
        # global monitor too — do not let it count as a trigger action.
        self._arm_trigger_grace()
        try:
            self._manager.set_trigger(tid, kind, count=count, key=key)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        if kind != TRIGGER_AT:
            widgets["armed_label"].set_label("")
        self._sync_card(tid)
        self.save_state()

    def _on_arm_at(self, _button, tid: str) -> None:
        minutes = int(self._cards[tid]["at_spin"].get_value())
        self._manager.set_trigger(tid, TRIGGER_AT, at=time.time() + minutes * 60)
        self.ctx.notify(
            tr("Timer will start in {minutes} min", minutes=minutes)
        )
        self._sync_card(tid)
        self.save_state()

    # -- global input (arrives on the monitor's background thread) -------------
    def _to_main(self, fn) -> None:
        """Run *fn* on the GTK main loop.

        The input monitor fires callbacks from its own reader thread;
        widget updates must happen on the main one. Falls back to an
        inline call when GLib is unavailable (headless tests).
        """
        try:
            from gi.repository import GLib

            GLib.idle_add(fn)
        except Exception:
            fn()

    def _app_has_focus(self) -> bool:
        """True while one of Omnify's own windows is focused (main thread).

        Input used to configure triggers must not count itself; the
        feature is about clicking/typing in *other* applications.
        """
        try:
            from gi.repository import Gtk

            app = Gtk.Application.get_default()
            active = app.get_active_window() if app is not None else None
            return active is not None and active.get_visible()
        except Exception:
            return False

    def _arm_trigger_grace(self) -> None:
        """Ignore desktop input for a moment (the user is still editing)."""
        self._trigger_grace_until = time.monotonic() + 1.2

    def _handle_global_click(self) -> None:
        """One left click *anywhere* — count it for armed 'clicks' triggers."""
        if getattr(self, "_manager", None) is None:
            return
        if time.monotonic() < self._trigger_grace_until:
            return
        if not any(
            entry.trigger_kind == TRIGGER_CLICKS
            for entry in self._manager.all()
        ):
            return
        self._to_main(self._process_global_click)

    def _process_global_click(self) -> None:
        if self._app_has_focus():
            return
        fired = []
        for entry in list(self._manager.all()):
            if entry.trigger_kind != TRIGGER_CLICKS:
                continue
            if self._manager.trigger_action(entry.id, TRIGGER_CLICKS):
                fired.append(entry)
        if not fired:
            if any(
                e.trigger_kind == TRIGGER_CLICKS for e in self._manager.all()
            ):
                self._refresh_all()  # update the n/N progress display
            return
        for entry in fired:
            self._play_start_sound(entry.name)
            self.ctx.notify(f"{entry.name} started (clicks trigger)")
            self._sync_card(entry.id)
        self._refresh_all()
        self.save_state()

    def _handle_global_key(self, combo: str) -> None:
        """One key press *anywhere* (combo string) — count the matches."""
        if getattr(self, "_manager", None) is None:
            return
        if time.monotonic() < self._trigger_grace_until:
            return
        lowered = combo.lower()
        if not any(
            entry.trigger_kind == TRIGGER_KEYS
            and entry.trigger_key
            and entry.trigger_key.lower() == lowered
            for entry in self._manager.all()
        ):
            return
        self._to_main(lambda: self._process_global_key(lowered))

    def _process_global_key(self, lowered: str) -> None:
        if self._app_has_focus():
            return
        fired = []
        for entry in list(self._manager.all()):
            if entry.trigger_kind != TRIGGER_KEYS or not entry.trigger_key:
                continue
            if entry.trigger_key.lower() != lowered:
                continue
            if self._manager.trigger_action(entry.id, TRIGGER_KEYS):
                fired.append(entry)
        if not fired:
            if any(
                e.trigger_kind == TRIGGER_KEYS for e in self._manager.all()
            ):
                self._refresh_all()  # update the n/N progress display
            return
        for entry in fired:
            self._play_start_sound(entry.name)
            self.ctx.notify(f"{entry.name} started (keys trigger)")
            self._sync_card(entry.id)
        self._refresh_all()
        self.save_state()

    def _on_record_key(self, _button, tid: str) -> None:
        if self._recording_for is not None:
            self._cancel_recording()
            return
        self._recording_for = tid
        self._cards[tid]["record_button"].set_label("Press a key… (Esc cancels)")

    def _cancel_recording(self) -> None:
        from gi.repository import Gdk

        if self._recording_for is not None and self._recording_for in self._cards:
            self._cards[self._recording_for]["record_button"].set_label("Record key…")
        self._recording_for = None

    def _on_key_press(self, _controller, keyval: int, _hardware_keycode: int,
                      state) -> bool:
        """Record key combos for 'keys' triggers (in-app only).

        The *counting* of key presses happens in the process-wide input
        monitor, which also sees presses outside this window — so this
        handler only records (and consumes keys while recording).
        """
        from gi.repository import Gdk

        key_name = Gdk.keyval_name(keyval) or ""
        modifiers = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            modifiers.append("ctrl")
        if state & Gdk.ModifierType.SHIFT_MASK:
            modifiers.append("shift")
        # This GTK build has no ModifierType.MOD1_MASK — fall back to the
        # raw bit value (Alt) so Alt-combos can be recorded at all.
        if state & int(getattr(Gdk.ModifierType, "MOD1_MASK", 0x8)):
            modifiers.append("alt")
        combo = "+".join(modifiers + [key_name])
        # A lone letter that arrives as an uppercase keyval (some layouts
        # deliver shifted letters without the shift flag) must match the
        # monitor's lower-cased names.
        if (
            not modifiers
            and key_name.isalpha()
            and len(key_name) == 1
            and key_name.isupper()
        ):
            combo = key_name.lower()

        if self._recording_for is not None:
            if key_name == "Escape":
                self._cancel_recording()
            else:
                tid = self._recording_for
                self._cancel_recording()
                if tid in self._cards:
                    self._arm_trigger_grace()
                    self._cards[tid]["key_label"].set_label(combo)
                    self._manager.set_trigger(
                        tid, TRIGGER_KEYS,
                        count=int(self._cards[tid]["count_spin"].get_value()),
                        key=combo,
                    )
                    self.ctx.notify(f"Key recorded: {combo}")
                    self._sync_card(tid)
                    self.save_state()
            return True
        return False  # counting is the monitor's job, not the window's

    # -- presets ---------------------------------------------------------------------------------
    def _on_save_preset(self, _button, tid: str) -> None:
        """💾 — save this timer's duration as a preset named after it."""
        try:
            entry = self._manager.get(tid)
        except KeyError:
            return
        try:
            self._presets.upsert(
                entry.name,
                entry.weeks,
                entry.days,
                entry.hours,
                entry.minutes,
                entry.seconds,
            )
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._refresh_presets()
        self.ctx.notify(f"Preset '{entry.name}' saved")

    def _on_preset_clicked(self, _button, preset: Dict) -> None:
        """Deploy a preset: create a NEW timer identical to it.

        The new timer starts idle (no auto-start) — the user presses
        Start when ready. Fields are passed by keyword on purpose:
        the duration fields are distinct from ``add()``'s ``name``
        parameter and must not bind positionally to it.
        """
        try:
            tid = self._manager.add(
                name=preset.get("label"),
                weeks=int(preset.get("weeks", 0)),
                days=int(preset.get("days", 0)),
                hours=int(preset.get("hours", 0)),
                minutes=int(preset.get("minutes", 0)),
                seconds=int(preset.get("seconds", 0)),
            )
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._add_card(tid)
        self.save_state()
        self.ctx.notify(
            tr(
                "Timer '{name}' created — press Start when ready",
                name=preset.get("label"),
            )
        )

    def _on_preset_delete(self, _button, preset_id: str) -> None:
        self._presets.remove(preset_id)
        self._refresh_presets()

    # -- alert settings handlers --------------------------------------------------------------------
    def _on_sound_switch(self, switch, state: bool) -> bool:
        switch.set_state(state)
        self._save_alerts()
        return False

    def _on_sound_length(self, _spin) -> None:
        self._save_alerts()

    def _on_timeout_changed(self, _combo) -> None:
        self._save_alerts()

    def _on_start_sound_switch(self, switch, state: bool) -> bool:
        switch.set_state(state)
        self._save_alerts()
        return False

    def _on_choose_start_sound(self, _button) -> None:
        from gi.repository import Gtk

        if self._root is None or self._root.get_root() is None:
            return
        dialog = Gtk.FileChooserNative(
            title="Choose a start sound file",
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.connect("response", self._on_start_sound_chosen, dialog)
        dialog.show()

    def _on_start_sound_chosen(self, _dialog, response, dialog) -> None:
        from gi.repository import Gtk

        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        file = dialog.get_file()
        dialog.destroy()
        if file is None:
            return
        path = file.get_path()
        settings = self._alerts_settings()
        settings["start_sound_file"] = path
        self.ctx.settings.set("alerts", settings)
        self._start_sound_file_label.set_label(path)

    def _on_test_start_sound(self, _button) -> None:
        settings = self._alerts_settings()
        path = settings.get("start_sound_file") or ""
        if not path or not os.path.exists(path):
            path = self._resolve_sound_file(settings)
        if not path:
            self.ctx.notify("No sound file available")
            return
        self._player.play(path, duration=None)

    def _on_choose_sound(self, _button) -> None:
        from gi.repository import Gtk

        if self._root is None or self._root.get_root() is None:
            return
        dialog = Gtk.FileChooserNative(
            title="Choose a sound file",
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.connect("response", self._on_sound_chosen, dialog)
        dialog.show()

    def _on_sound_chosen(self, _dialog, response, dialog) -> None:
        from gi.repository import Gtk, Gio

        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        file = dialog.get_file()
        dialog.destroy()
        if file is None:
            return
        path = file.get_path()
        settings = self._alerts_settings()
        settings["sound_file"] = path
        self.ctx.settings.set("alerts", settings)
        self._sound_file_label.set_label(path)

    def _on_test_sound(self, _button) -> None:
        settings = self._alerts_settings()
        sound = self._resolve_sound_file(settings)
        if not sound:
            self.ctx.notify("No sound file available")
            return
        self._player.play(sound, duration=None)
