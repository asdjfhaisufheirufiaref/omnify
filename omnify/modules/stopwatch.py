"""Stopwatch module.

An ever-growing stopwatch with laps. The model (:class:`StopwatchModel`)
is pure — no GTK, no sleeping, no real clock: it takes an injectable
``clock`` (default ``time.time``) so the tests can drive time by hand
(see ``tests/test_stopwatch.py``). While running, the display is
derived from the anchor ``_elapsed_base + (clock() - _start_at``) —
never from tick counting — so the stopwatch keeps running (and stays
accurate) while the GUI is hidden or its ticks lag.

Wall time again on purpose: ``start_at`` is a real timestamp, so a
running stopwatch survives closing the app and a reboot — after
restart it simply shows the full elapsed time, which is what "it must
keep working while the computer is off" means in practice.

Like the timers, the stopwatch can be driven by an action trigger
(N mouse clicks *anywhere* / N key presses *anywhere* / at a time);
for it the trigger *toggles* the run (start if not running, pause if
running). The "anywhere" counting is the process-wide input monitor
(see :mod:`omnify.core.input_monitor`).

States: ``stopped`` → ``running`` → ``paused`` → ``running`` → … →
``stopped`` (after reset). ``start()`` works from ``stopped`` (a fresh
run) and from ``paused`` (a resume); ``reset()`` from any state.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .timer import (
    TRIGGER_AT,
    TRIGGER_CLICKS,
    TRIGGER_KEYS,
    TRIGGER_MANUAL,
    TRIGGER_KINDS,
    TRIGGER_LABELS,
)


class StopwatchModel:
    """A pure stopwatch driven by an injectable wall clock.

    ``clock`` must be a zero-argument callable returning seconds
    (default ``time.time``). While running, :attr:`elapsed` is derived
    from the anchor ``_start_at``; while paused or stopped it is the
    frozen ``_elapsed_base``, so the value never changes (or drifts)
    unless the stopwatch is actually running.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._elapsed_base: float = 0.0
        self._start_at: float = 0.0
        self._state: str = "stopped"
        self._laps: List[float] = []
        self._trigger_kind: str = TRIGGER_MANUAL
        self._trigger_count: int = 1
        self._trigger_key: str = ""
        self._trigger_at: float = 0.0
        self._trigger_progress: int = 0

    # -- properties -------------------------------------------------------
    @property
    def state(self) -> str:
        """One of ``"stopped"``, ``"running"``, ``"paused"``."""
        return self._state

    @property
    def elapsed(self) -> float:
        """Total elapsed seconds; while running, derived from the anchor."""
        if self._state == "running":
            return self._elapsed_base + (self._clock() - self._start_at)
        return self._elapsed_base

    @property
    def laps(self) -> List[float]:
        """Cumulative lap times in seconds, oldest first (a copy)."""
        return list(self._laps)

    @property
    def trigger_kind(self) -> str:
        return self._trigger_kind

    # -- state transitions ----------------------------------------------------
    def start(self) -> None:
        """Begin a fresh run (from ``stopped``) or resume (from ``paused``).

        Re-anchors ``_start_at = clock()``. From ``stopped`` the elapsed
        time starts at 0; from ``paused`` it continues from the frozen
        value. Raises :class:`ValueError` while ``running``.
        """
        if self._state == "running":
            raise ValueError("Cannot start while running")
        if self._state == "stopped":
            self._elapsed_base = 0.0
        self._start_at = self._clock()
        self._state = "running"

    def pause(self) -> None:
        """Freeze the elapsed time; only valid while ``running``.

        Folds the live span into ``_elapsed_base``.
        """
        if self._state != "running":
            raise ValueError(f"Cannot pause from state {self._state!r}")
        self._elapsed_base = self.elapsed
        self._state = "paused"

    def reset(self) -> None:
        """Back to ``stopped`` with elapsed 0 and no laps; any state.

        Never raises.
        """
        self._state = "stopped"
        self._elapsed_base = 0.0
        self._start_at = 0.0
        self._laps = []

    def lap(self) -> float:
        """Record a lap while ``running``; returns the cumulative seconds.

        The cumulative elapsed time is appended to the laps list (oldest
        first) and returned. Raises :class:`ValueError` otherwise.
        """
        if self._state != "running":
            raise ValueError(f"Cannot lap from state {self._state!r}")
        value = self.elapsed
        self._laps.append(value)
        return value

    # -- triggers -------------------------------------------------------------
    def set_trigger(
        self,
        kind: str,
        count: int = 1,
        key: str = "",
        at: float = 0.0,
    ) -> None:
        """Configure the action trigger (same kinds as the timers)."""
        if kind not in TRIGGER_KINDS:
            raise ValueError(f"Unknown trigger kind {kind!r}")
        if kind in (TRIGGER_CLICKS, TRIGGER_KEYS):
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("Trigger count must be a positive int")
            self._trigger_count = count
        self._trigger_kind = kind
        self._trigger_key = key if kind == TRIGGER_KEYS else ""
        self._trigger_at = float(at) if kind == TRIGGER_AT and at else 0.0
        self._trigger_progress = 0

    def trigger_action(self, kind: str = TRIGGER_CLICKS) -> bool:
        """Report one user action.

        For ``clicks``/``keys`` triggers: when the count is reached the
        stopwatch *toggles* (start if not running, pause if running),
        the trigger disarms and ``True`` is returned.
        """
        if self._trigger_kind not in (TRIGGER_CLICKS, TRIGGER_KEYS):
            return False
        if kind != self._trigger_kind:
            return False
        self._trigger_progress += 1
        if self._trigger_progress >= self._trigger_count:
            self._trigger_progress = 0
            self._trigger_kind = TRIGGER_MANUAL
            if self._state == "running":
                self.pause()
            else:
                self.start()
            return True
        return False

    def tick(self) -> bool:
        """``at`` auto-start: start/resume when ``trigger_at`` passes.

        Returns ``True`` exactly when the auto-start happened.
        """
        if (
            self._state in ("stopped", "paused")
            and self._trigger_kind == TRIGGER_AT
            and self._trigger_at > 0
            and self._clock() >= self._trigger_at
        ):
            self._trigger_kind = TRIGGER_MANUAL
            self._trigger_at = 0.0
            self.start()
            return True
        return False

    # -- persistence ---------------------------------------------------------------
    def to_dict(self) -> Dict:
        """The whole state as a JSON-friendly dict."""
        return {
            "state": self._state,
            "elapsed_base": self._elapsed_base,
            "start_at": self._start_at,
            "laps": list(self._laps),
            "trigger_kind": self._trigger_kind,
            "trigger_count": self._trigger_count,
            "trigger_key": self._trigger_key,
            "trigger_at": self._trigger_at,
            "trigger_progress": self._trigger_progress,
        }

    def from_dict(self, data: Dict) -> None:
        """Restore a saved state (never raises on corrupt input).

        A saved ``running`` state simply resumes counting from its wall
        anchor — the elapsed time includes whatever time passed while
        the machine was off, by design.
        """
        if not isinstance(data, dict):
            return
        try:
            state = data.get("state", "stopped")
            if state not in ("stopped", "running", "paused"):
                state = "stopped"
            self._state = state
            self._elapsed_base = float(data.get("elapsed_base", 0.0))
            self._start_at = float(data.get("start_at", 0.0))
            self._laps = [float(x) for x in data.get("laps", [])]
            kind = data.get("trigger_kind", TRIGGER_MANUAL)
            if kind in TRIGGER_KINDS:
                self._trigger_kind = kind
            try:
                self._trigger_count = int(data.get("trigger_count", 1))
            except (TypeError, ValueError):
                self._trigger_count = 1
            self._trigger_key = str(data.get("trigger_key", ""))
            try:
                self._trigger_at = float(data.get("trigger_at", 0.0))
            except (TypeError, ValueError):
                self._trigger_at = 0.0
            try:
                self._trigger_progress = int(data.get("trigger_progress", 0))
            except (TypeError, ValueError):
                self._trigger_progress = 0
        except (TypeError, ValueError):
            self.reset()


def format_stopwatch(ms: float) -> str:
    """Render *ms* milliseconds as ``mm:ss.cc`` (zero-padded).

    ``cc`` is centiseconds (10 ms units) and is truncated (floored), so
    elapsed time is never rounded up. Negative input clamps to
    ``00:00.00``; minutes grow beyond two digits if needed.
    """
    if ms < 0:
        ms = 0.0
    centis = int(ms // 10)
    minutes, rem = divmod(centis, 6000)
    seconds, csecs = divmod(rem, 100)
    return f"{minutes:02d}:{seconds:02d}.{csecs:02d}"


# ---------------------------------------------------------------------------
# GTK view
# ---------------------------------------------------------------------------
class StopwatchModule(BaseModule):
    """The stopwatch tool: elapsed time, laps and an action trigger."""

    id = "stopwatch"
    name = "Stopwatch"
    icon = "⏲"
    description = "Stopwatch with laps and action triggers; keeps running in the background."
    group = "Tools"

    _TICK_MS = 50
    _STATE_KEY = "stopwatch"

    #: Sound files probed when the user never chose a start sound.
    _DEFAULT_SOUND_CANDIDATES = (
        "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
        "/usr/share/sounds/freedesktop/stereo/complete.oga",
        "/usr/share/sounds/kde/freedesktop/stereo/complete.oga",
    )

    def __init__(self, context) -> None:
        super().__init__(context)
        from ..core.alerts import SoundPlayer

        self._player = SoundPlayer()

    def build_view(self):
        from gi.repository import Gtk

        self._model = StopwatchModel()
        self._model.from_dict(self.ctx.storage.load(self._STATE_KEY, {}))
        self._timeout_id = None
        self._recording_for = False
        self._trigger_grace_until = 0.0
        self._syncing_trigger_ui = False
        self._root = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self._root = root

        # --- big status label ---
        self._status_label = Gtk.Label(label="00:00.00")
        self._status_label.get_style_context().add_class("omnify-mono")
        self._status_label.get_style_context().add_class("omnify-card-title")
        self._status_label.set_hexpand(True)
        root.append(self._status_label)

        # --- controls ---
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._start_button = Gtk.Button(label="Start")
        self._start_button.get_style_context().add_class("omnify-accent")
        self._start_button.connect("clicked", self._on_start)
        self._pause_button = Gtk.Button(label="Pause")
        self._pause_button.connect("clicked", self._on_pause)
        self._lap_button = Gtk.Button(label="Lap")
        self._lap_button.connect("clicked", self._on_lap)
        self._reset_button = Gtk.Button(label="Reset")
        self._reset_button.connect("clicked", self._on_reset)
        for button in (
            self._start_button,
            self._pause_button,
            self._lap_button,
            self._reset_button,
        ):
            controls.append(button)
        root.append(controls)

        # --- trigger settings ---
        trigger_expander = Gtk.Expander(label="⚡ Activate by action")
        trigger_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        trigger_box.set_margin_top(8)

        kind_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        kind_row.append(Gtk.Label(label="Start/stop after:", xalign=0.0))
        self._kind_combo = Gtk.ComboBoxText()
        for kind, label in TRIGGER_LABELS:
            self._kind_combo.append(kind, label)
        self._kind_combo.connect("changed", self._on_trigger_kind_changed)
        kind_row.append(self._kind_combo)
        trigger_box.append(kind_row)

        self._count_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._count_row.append(Gtk.Label(label="N ="))
        self._count_spin = Gtk.SpinButton.new_with_range(1, 99, 1)
        self._count_spin.connect("value-changed", self._on_trigger_kind_changed)
        self._count_row.append(self._count_spin)
        self._count_row.append(Gtk.Label(label="actions"))
        trigger_box.append(self._count_row)

        self._key_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._key_row.append(Gtk.Label(label="Key:"))
        self._record_button = Gtk.Button(label="Record key…")
        self._record_button.connect("clicked", self._on_record_key)
        self._key_row.append(self._record_button)
        self._key_label = Gtk.Label(label="—")
        self._key_label.get_style_context().add_class("omnify-dim")
        self._key_row.append(self._key_label)
        trigger_box.append(self._key_row)

        self._at_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._at_row.append(Gtk.Label(label="Start in (min):"))
        self._at_spin = Gtk.SpinButton.new_with_range(1, 1440, 1)
        self._at_row.append(self._at_spin)
        arm_button = Gtk.Button(label="Arm")
        arm_button.connect("clicked", self._on_arm_at)
        self._at_row.append(arm_button)
        self._armed_label = Gtk.Label(label="", xalign=0.0)
        self._armed_label.get_style_context().add_class("omnify-dim")
        self._at_row.append(self._armed_label)
        trigger_box.append(self._at_row)

        # click/key counting happens anywhere on the desktop (see the
        # monitor wiring below); this just shows the n/N progress
        self._progress_label = Gtk.Label(label=tr("armed"), xalign=0.0)
        self._progress_label.get_style_context().add_class("omnify-dim")
        trigger_box.append(self._progress_label)

        # --- start sound (one-shot, on any start) ---
        sw_settings = self.ctx.settings.get("stopwatch", {}) or {}
        start_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        start_row.append(Gtk.Label(label="Play sound on start:"))
        self._start_sound_switch = Gtk.Switch()
        self._start_sound_switch.set_active(
            bool(sw_settings.get("start_sound_enabled", False))
        )
        self._start_sound_switch.connect(
            "state-set", self._on_start_sound_switch
        )
        start_row.append(self._start_sound_switch)
        trigger_box.append(start_row)

        start_file_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        start_file_row.append(Gtk.Label(label="Start sound file:"))
        self._start_sound_file_label = Gtk.Label(
            label=sw_settings.get("start_sound_file") or "system default",
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
        trigger_box.append(start_file_row)

        trigger_expander.set_child(trigger_box)
        root.append(trigger_expander)

        # --- laps (newest first) ---
        self._laps_list = Gtk.ListBox()
        self._laps_list.set_selection_mode(Gtk.SelectionMode.NONE)
        laps_scroll = Gtk.ScrolledWindow()
        laps_scroll.set_child(self._laps_list)
        laps_scroll.set_vexpand(True)
        laps_scroll.add_css_class("omnify-scroll")
        self._laps_list.add_css_class("omnify-laps")
        root.append(laps_scroll)

        # key capture for "keys" triggers; GTK4 EventControllerKey emits
        # "key-pressed", not the GTK3 "key-press-event" name. Like in the
        # timer module, this handler only *records* combos — the counting
        # happens in the process-wide input monitor below.
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_press)
        root.add_controller(key_controller)

        # global input counting (clicks/keys anywhere on the desktop);
        # the same process-wide monitor the timer module subscribes to
        from ..core.input_monitor import get_default_monitor

        monitor = get_default_monitor()
        monitor.subscribe_click(self._handle_global_click)
        monitor.subscribe_key_combo(self._handle_global_key)
        if not monitor.start() and monitor.error:
            self.ctx.notify(monitor.error)

        self._sync_trigger_ui()
        self._refresh_label()
        self._sync_buttons()
        self._refresh_laps()
        self._sync_ticker()
        return root

    # -- UI sync -------------------------------------------------------------
    def _refresh_label(self) -> None:
        self._status_label.set_label(format_stopwatch(self._model.elapsed * 1000))

    def _sync_buttons(self) -> None:
        state = self._model.state
        self._start_button.set_sensitive(state in ("stopped", "paused"))
        self._pause_button.set_sensitive(state == "running")
        self._lap_button.set_sensitive(state == "running")
        self._reset_button.set_sensitive(state in ("running", "paused"))

    def _refresh_laps(self) -> None:
        from gi.repository import Gtk

        # Gtk.ListBox has no get_children() in GTK4; drain row 0 until empty.
        while True:
            row = self._laps_list.get_row_at_index(0)
            if row is None:
                break
            self._laps_list.remove(row)
        laps = self._model.laps
        for number, cumulative in reversed(list(enumerate(laps, 1))):
            previous = laps[number - 2] if number > 1 else 0.0
            delta = cumulative - previous
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            lap_label = Gtk.Label(label=tr("Lap {number}", number=number))
            lap_label.get_style_context().add_class("omnify-dim")
            cum_label = Gtk.Label(label=format_stopwatch(cumulative * 1000))
            cum_label.get_style_context().add_class("omnify-mono")
            delta_label = Gtk.Label(label=f"+{format_stopwatch(delta * 1000)}")
            delta_label.get_style_context().add_class("omnify-mono")
            delta_label.get_style_context().add_class("omnify-dim")
            row_box.append(lap_label)
            row_box.append(cum_label)
            row_box.append(delta_label)
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_child(row_box)
            self._laps_list.append(row)

    def _sync_trigger_ui(self) -> None:
        index = (
            TRIGGER_KINDS.index(self._model.trigger_kind)
            if self._model.trigger_kind in TRIGGER_KINDS
            else 0
        )
        self._syncing_trigger_ui = True
        try:
            if self._kind_combo.get_active() != index:
                self._kind_combo.set_active(index)
            if int(self._count_spin.get_value()) != self._model._trigger_count:
                self._count_spin.set_value(self._model._trigger_count)
        finally:
            self._syncing_trigger_ui = False
        kind = self._model.trigger_kind
        needs_count = kind in (TRIGGER_CLICKS, TRIGGER_KEYS)
        self._count_row.set_visible(needs_count)
        self._key_row.set_visible(kind == TRIGGER_KEYS)
        self._at_row.set_visible(kind == TRIGGER_AT)
        self._progress_label.set_visible(needs_count)
        if needs_count:
            self._progress_label.set_label(
                tr(
                    "counting: {current} / {target} (anywhere on the desktop)",
                    current=self._model._trigger_progress,
                    target=self._model._trigger_count,
                )
            )
        if kind == TRIGGER_KEYS:
            self._key_label.set_label(self._model._trigger_key or "—")
        if kind == TRIGGER_AT and self._model._trigger_at > 0:
            when = time.strftime("%H:%M", time.localtime(self._model._trigger_at))
            self._armed_label.set_label(
                tr("will start at {when}", when=when)
            )
        else:
            self._armed_label.set_label("")

    # -- ticker -----------------------------------------------------------------
    def _start_ticker(self) -> None:
        from gi.repository import GLib

        if getattr(self, "_timeout_id", None) is None:
            self._timeout_id = GLib.timeout_add(self._TICK_MS, self._on_tick)

    def _stop_ticker(self) -> None:
        from gi.repository import GLib

        if getattr(self, "_timeout_id", None) is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _sync_ticker(self) -> None:
        if self._model.state == "running":
            self._start_ticker()
        else:
            self._stop_ticker()

    def on_show(self) -> None:
        if getattr(self, "_model", None) is None:
            return  # the view has not been built yet
        self._refresh_label()
        self._sync_buttons()
        self._refresh_laps()
        self._sync_ticker()

    def on_hide(self) -> None:
        # Only the tick stops; the model keeps running (wall anchor) and
        # the shell's global ticker keeps pumping it.
        self._stop_ticker()

    def _on_tick(self) -> bool:
        # A stopwatch never finishes; the tick only redraws and applies
        # a pending "at" auto-start.
        self.pump()
        self._refresh_label()
        self._refresh_laps()
        return True

    # -- shell integration -----------------------------------------------------------
    def pump(self) -> bool:
        """Apply a pending 'at' auto-start; True when it happened."""
        if getattr(self, "_model", None) is None:
            return False
        started = self._model.tick()
        if started:
            self._play_start_sound()
            self.ctx.notify("Stopwatch started (time trigger)")
            self._sync_buttons()
            self._sync_ticker()
            self.save_state()
        return started

    def save_state(self) -> None:
        """Persist the stopwatch (the shell also calls this on shutdown)."""
        if getattr(self, "_model", None) is None:
            return
        self.ctx.storage.save(self._STATE_KEY, self._model.to_dict())

    def tray_snapshot(self) -> Optional[str]:
        """One line for the system tray, or None when not running."""
        if getattr(self, "_model", None) is None or self._model.state != "running":
            return None
        return f"⏲ {format_stopwatch(self._model.elapsed * 1000)}"

    def mini_lines(self) -> List[str]:
        """Rows for the floating mini-window."""
        if getattr(self, "_model", None) is None:
            return []
        if self._model.state in ("running", "paused"):
            return [f"Stopwatch  {format_stopwatch(self._model.elapsed * 1000)}"]
        return []

    # -- handlers ----------------------------------------------------------------------
    def _on_start(self, _btn) -> None:
        try:
            self._model.start()
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._play_start_sound()
        self._sync_buttons()
        self._refresh_label()
        self._sync_ticker()
        self.save_state()

    def start_from_action(self) -> bool:
        """Start the stopwatch from a calendar event action.

        Returns ``False`` when it is already running (no double start).
        """
        if self._model.state == "running":
            return False
        try:
            self._model.start()
        except ValueError:
            return False
        self._play_start_sound()
        self._sync_buttons()
        self._refresh_label()
        self._sync_ticker()
        self.save_state()
        return True

    def _on_pause(self, _btn) -> None:
        try:
            self._model.pause()
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._sync_buttons()
        self._refresh_label()
        self._stop_ticker()
        self.save_state()

    def _on_lap(self, _btn) -> None:
        try:
            self._model.lap()
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._refresh_label()
        self._refresh_laps()
        self.save_state()

    def _on_reset(self, _btn) -> None:
        self._model.reset()
        self._sync_buttons()
        self._refresh_label()
        self._refresh_laps()
        self._stop_ticker()
        self.save_state()

    # -- trigger handlers -------------------------------------------------------------------
    def _on_trigger_kind_changed(self, _widget) -> None:
        if self._syncing_trigger_ui:
            return
        index = self._kind_combo.get_active()
        if index < 0:
            return
        kind = TRIGGER_KINDS[index]
        count = int(self._count_spin.get_value())
        key = self._model._trigger_key if kind == TRIGGER_KEYS else ""
        # The click/key that changed this setting is seen by the global
        # monitor as well — do not count it as a trigger action.
        self._arm_trigger_grace()
        try:
            self._model.set_trigger(kind, count=count, key=key)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._sync_trigger_ui()
        self.save_state()

    def _on_arm_at(self, _btn) -> None:
        minutes = int(self._at_spin.get_value())
        self._model.set_trigger(TRIGGER_AT, at=time.time() + minutes * 60)
        self.ctx.notify(
            tr("Stopwatch will start in {minutes} min", minutes=minutes)
        )
        self._sync_trigger_ui()
        self.save_state()

    # -- start sound ------------------------------------------------------------
    def _resolve_start_sound(self) -> str:
        """The configured start-sound file, or a system default."""
        settings = self.ctx.settings.get("stopwatch", {}) or {}
        path = settings.get("start_sound_file") or ""
        if path and os.path.exists(path):
            return path
        for candidate in self._DEFAULT_SOUND_CANDIDATES:
            if os.path.exists(candidate):
                return candidate
        return ""

    def _play_start_sound(self) -> None:
        """One-shot start sound (manual start, trigger, or "at")."""
        settings = self.ctx.settings.get("stopwatch", {}) or {}
        if not settings.get("start_sound_enabled", False):
            return
        path = self._resolve_start_sound()
        if not path:
            return
        try:
            ok = self._player.play(path, duration=None)
        except Exception:
            ok = False
        from ..core.logfile import log

        log(f"stopwatch start sound: ok={ok} file={path!r}")

    # -- global input (arrives on the monitor's background thread) --------------
    def _to_main(self, fn) -> None:
        """Run *fn* on the GTK main loop (monitor callbacks are not)."""
        try:
            from gi.repository import GLib

            GLib.idle_add(fn)
        except Exception:
            fn()  # headless fallback: run inline

    def _app_has_focus(self) -> bool:
        """True while one of Omnify's own windows is focused (main thread)."""
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
        """One left click *anywhere* — count it for a clicks trigger."""
        if getattr(self, "_model", None) is None:
            return
        if time.monotonic() < self._trigger_grace_until:
            return
        if self._model.trigger_kind != TRIGGER_CLICKS:
            return
        self._to_main(self._process_global_click)

    def _process_global_click(self) -> None:
        if self._app_has_focus():
            return
        if self._model.trigger_action(TRIGGER_CLICKS):
            self._on_trigger_fired("clicks trigger")
        else:
            self._sync_trigger_ui()  # update the n/N progress display

    def _handle_global_key(self, combo: str) -> None:
        """One key press *anywhere* (combo string) — count the match."""
        if getattr(self, "_model", None) is None:
            return
        if time.monotonic() < self._trigger_grace_until:
            return
        if (
            self._model.trigger_kind != TRIGGER_KEYS
            or not self._model._trigger_key
            or self._model._trigger_key.lower() != combo.lower()
        ):
            return
        self._to_main(self._process_global_key)

    def _process_global_key(self) -> None:
        if self._app_has_focus():
            return
        if self._model.trigger_action(TRIGGER_KEYS):
            self._on_trigger_fired("keys trigger")
        else:
            self._sync_trigger_ui()  # update the n/N progress display

    def _on_trigger_fired(self, source: str) -> None:
        """The trigger reached its count: the stopwatch toggled."""
        self._play_start_sound()
        if self._model.state == "running":
            self.ctx.notify(
                tr("Stopwatch started ({source})", source=source)
            )
        else:
            self.ctx.notify(
                tr("Stopwatch paused ({source})", source=source)
            )
        self._sync_trigger_ui()
        self._sync_buttons()
        self._sync_ticker()
        self.save_state()

    # -- start-sound settings -------------------------------------------------------
    def _save_stopwatch_settings(self) -> None:
        settings = self.ctx.settings.get("stopwatch", {}) or {}
        settings["start_sound_enabled"] = bool(
            self._start_sound_switch.get_active()
        )
        self.ctx.settings.set("stopwatch", settings)

    def _on_start_sound_switch(self, switch, state: bool) -> bool:
        switch.set_state(state)
        self._save_stopwatch_settings()
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
        settings = self.ctx.settings.get("stopwatch", {}) or {}
        settings["start_sound_file"] = path
        self.ctx.settings.set("stopwatch", settings)
        self._start_sound_file_label.set_label(path)

    def _on_test_start_sound(self, _button) -> None:
        path = self._resolve_start_sound()
        if not path:
            self.ctx.notify("No sound file available")
            return
        self._player.play(path, duration=None)

    def _on_record_key(self, _btn) -> None:
        if self._recording_for:
            self._recording_for = False
            self._record_button.set_label("Record key…")
            return
        self._recording_for = True
        self._record_button.set_label("Press a key… (Esc cancels)")

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

        if self._recording_for:
            if key_name == "Escape":
                self._recording_for = False
                self._record_button.set_label("Record key…")
            else:
                self._recording_for = False
                self._record_button.set_label("Record key…")
                self._arm_trigger_grace()
                self._key_label.set_label(combo)
                self._model.set_trigger(
                    TRIGGER_KEYS,
                    count=int(self._count_spin.get_value()),
                    key=combo,
                )
                self.ctx.notify(
                    tr("Key recorded: {combo}", combo=combo)
                )
                self._sync_trigger_ui()
                self.save_state()
            return True
        return False  # counting is the monitor's job, not the window's
