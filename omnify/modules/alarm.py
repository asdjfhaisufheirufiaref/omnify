"""Alarm clock module: pure alarm collection + a compact GTK cockpit."""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Dict, List, Optional, Set

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .alarm_engine import (
    ALL_DAYS,
    DAY_LONG,
    DAY_SHORT,
    Alarm,
    AlarmClock,
    parse_time,
)

#: Default system sounds, in preference order.
SOUND_CANDIDATES = (
    "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/kde/freedesktop/stereo/complete.oga",
)


class AlarmModule(BaseModule):
    """A simple, persistent alarm clock with weekday repeats and snooze."""

    id = "alarm"
    name = "Alarm clock"
    icon = "⏰"
    description = (
        "A persistent alarm clock with weekday repeats, custom sounds and snooze."
    )
    group = "Tools"

    STATE_KEY = "alarms"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._root = None
        self._clock_widgets: Dict[str, Any] = {}
        self._ringing_ids: List[str] = []
        self._ringing_active: Optional[str] = None
        self._updating = False
        self._player = None
        self._notifier = None
        self._clock = AlarmClock()
        self._clock.load(context.storage.load(self.STATE_KEY, {}))
        self._save()

    # ------------------------------------------------------------------
    # Persistence and helpers
    # ------------------------------------------------------------------
    def _save(self) -> None:
        self.ctx.storage.save(self.STATE_KEY, self._clock.to_dict())

    def _resolve_sound(self, alarm: Alarm) -> str:
        if alarm.sound and os.path.exists(alarm.sound):
            return alarm.sound
        for path in SOUND_CANDIDATES:
            if os.path.exists(path):
                return path
        return ""

    @staticmethod
    def _days_text(alarm: Alarm) -> str:
        """Localise a day selection without changing the pure model."""
        days = set(alarm.days)
        if not days:
            return tr("Once")
        if days == set(ALL_DAYS):
            return tr("Every day")
        if days == {0, 1, 2, 3, 4}:
            return tr("Weekdays")
        if days == {5, 6}:
            return tr("Weekend")
        return ", ".join(tr(DAY_SHORT[day]) for day in range(7) if day in days)

    def _format_occurrence(self, occurrence: Optional[_dt.datetime]) -> str:
        if occurrence is None:
            return tr("Off")
        today = _dt.date.today()
        time_text = occurrence.strftime("%H:%M")
        if occurrence.date() == today:
            return tr("Today · {time}", time=time_text)
        if occurrence.date() == today + _dt.timedelta(days=1):
            return tr("Tomorrow · {time}", time=time_text)
        return occurrence.strftime("%a, %d %b · %H:%M")

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        from ..core.alerts import Notifier, SoundPlayer

        self._player = SoundPlayer()
        self._notifier = Notifier()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self._root = root

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Alarm clock", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        subtitle = Gtk.Label(label=self.description, xalign=0.0)
        subtitle.set_wrap(True)
        subtitle.get_style_context().add_class("omnify-dim")
        title_box.append(title)
        title_box.append(subtitle)
        header.append(title_box)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)
        add_button = Gtk.Button(label="+ Add alarm")
        add_button.get_style_context().add_class("omnify-accent")
        add_button.connect("clicked", self._on_add_clicked)
        header.append(add_button)
        root.append(header)

        clock_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        clock_card.get_style_context().add_class("omnify-card")
        self._clock_label = Gtk.Label(label="--:--:--", xalign=0.0)
        self._clock_label.get_style_context().add_class("alarm-clock")
        self._clock_label.get_style_context().add_class("omnify-mono")
        clock_card.append(self._clock_label)
        self._date_label = Gtk.Label(label="", xalign=0.0)
        self._date_label.get_style_context().add_class("omnify-dim")
        clock_card.append(self._date_label)
        self._next_label = Gtk.Label(label="", xalign=0.0)
        self._next_label.get_style_context().add_class("alarm-next")
        clock_card.append(self._next_label)
        root.append(clock_card)

        self._ring_banner = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )
        self._ring_banner.get_style_context().add_class("alarm-ring")
        self._ring_banner.set_visible(False)
        self._ring_label = Gtk.Label(label="", xalign=0.0)
        self._ring_label.set_hexpand(True)
        self._ring_label.set_wrap(True)
        self._ring_banner.append(self._ring_label)
        self._snooze_button = Gtk.Button(label="Snooze")
        self._snooze_button.connect("clicked", self._on_snooze_clicked)
        self._ring_banner.append(self._snooze_button)
        self._stop_button = Gtk.Button(label="Stop")
        self._stop_button.get_style_context().add_class("omnify-accent")
        self._stop_button.connect("clicked", self._on_stop_clicked)
        self._ring_banner.append(self._stop_button)
        root.append(self._ring_banner)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.append(self._list)
        self._render_alarms()
        self._update_header()
        self.pump()
        return root

    # ------------------------------------------------------------------
    # Alarm list
    # ------------------------------------------------------------------
    def _render_alarms(self) -> None:
        from gi.repository import Gtk

        if self._list is None:
            return
        child = self._list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._list.remove(child)
            child = following
        alarms = self._clock.all()
        if not alarms:
            empty = Gtk.Label(
                label=tr("No alarms yet. Press “+ Add alarm” to create one."),
                xalign=0.0,
            )
            empty.get_style_context().add_class("omnify-dim")
            empty.get_style_context().add_class("omnify-card")
            self._list.append(empty)
            return
        for alarm in alarms:
            self._list.append(self._build_alarm_card(alarm))

    def _build_alarm_card(self, alarm: Alarm) -> Any:
        from gi.repository import Gtk

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.get_style_context().add_class("omnify-card")
        time_label = Gtk.Label(label=alarm.time_text, xalign=0.0)
        time_label.get_style_context().add_class("alarm-time")
        time_label.get_style_context().add_class("omnify-mono")
        time_label.set_size_request(120, -1)
        card.append(time_label)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.set_hexpand(True)
        name = Gtk.Label(label=alarm.label, xalign=0.0)
        name.get_style_context().add_class("alarm-name")
        info.append(name)
        days = Gtk.Label(
            label=self._days_text(alarm),
            xalign=0.0,
        )
        days.get_style_context().add_class("omnify-dim")
        info.append(days)
        occurrence = self._clock.next_occurrence(alarm)
        next_text = (
            tr("Snoozed until {time}", time=_dt.datetime.fromtimestamp(alarm.snoozed_until).strftime("%H:%M"))
            if alarm.snoozed_until and alarm.snoozed_until > _dt.datetime.now().timestamp()
            else self._format_occurrence(occurrence)
        )
        next_label = Gtk.Label(label=next_text, xalign=0.0)
        next_label.get_style_context().add_class("alarm-next")
        info.append(next_label)
        card.append(info)

        switch = Gtk.Switch()
        switch.set_active(bool(alarm.enabled))
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_enabled_toggled, alarm.id)
        card.append(switch)

        edit = Gtk.Button(label=tr("Edit"))
        edit.connect("clicked", lambda _button: self._open_editor(alarm))
        card.append(edit)
        delete = Gtk.Button(label=tr("Delete"))
        delete.get_style_context().add_class("omnify-danger")
        delete.connect("clicked", lambda _button: self._delete_alarm(alarm.id))
        card.append(delete)
        return card

    def _on_enabled_toggled(self, switch, _pspec, alarm_id: str) -> None:
        if self._updating:
            return
        try:
            self._clock.set_enabled(alarm_id, bool(switch.get_active()))
        except KeyError:
            return
        self._save()
        self._render_alarms()
        self._update_header()

    def _delete_alarm(self, alarm_id: str) -> None:
        self._clock.remove(alarm_id)
        if alarm_id in self._ringing_ids:
            self._ringing_ids.remove(alarm_id)
        if self._ringing_active == alarm_id:
            self._stop_audio()
        self._save()
        self._render_alarms()
        self._update_header()
        self.ctx.notify(tr("Alarm deleted"))

    def _on_add_clicked(self, _button) -> None:
        self._open_editor(None)

    # ------------------------------------------------------------------
    # Editor dialog
    # ------------------------------------------------------------------
    def _open_editor(self, alarm: Optional[Alarm]) -> None:
        from gi.repository import Gtk

        if self._root is None or self._root.get_root() is None:
            return
        now = _dt.datetime.now()
        default = now + _dt.timedelta(minutes=5)
        hour = alarm.hour if alarm else default.hour
        minute = alarm.minute if alarm else default.minute
        win = Gtk.Window()
        win.set_title(tr("Edit alarm") if alarm else tr("New alarm"))
        win.set_default_size(520, 560)
        win.set_modal(True)
        main = self._root.get_root()
        if main is not None:
            win.set_transient_for(main)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hour_spin = Gtk.SpinButton.new_with_range(0, 23, 1)
        hour_spin.set_value(hour)
        minute_spin = Gtk.SpinButton.new_with_range(0, 59, 1)
        minute_spin.set_value(minute)
        time_row.append(Gtk.Label(label=tr("Time"), xalign=0.0))
        time_row.append(hour_spin)
        time_row.append(Gtk.Label(label=":", xalign=0.5))
        time_row.append(minute_spin)
        box.append(time_row)

        label_entry = Gtk.Entry()
        label_entry.set_placeholder_text(tr("Alarm label"))
        label_entry.set_text(alarm.label if alarm else "")
        box.append(label_entry)

        days_label = Gtk.Label(label=tr("Repeat on"), xalign=0.0)
        box.append(days_label)
        days_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        day_buttons: Dict[int, Any] = {}
        selected_days: Set[int] = set(alarm.days) if alarm else set()
        for index, short in enumerate(DAY_SHORT):
            button = Gtk.ToggleButton(label=tr(short))
            button.set_active(index in selected_days)
            day_buttons[index] = button
            days_row.append(button)
        box.append(days_row)
        repeat_hint = Gtk.Label(
            label=tr("Leave all days off for a one-time alarm."),
            xalign=0.0,
        )
        repeat_hint.set_wrap(True)
        repeat_hint.get_style_context().add_class("omnify-dim")
        box.append(repeat_hint)

        snooze_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        snooze_row.append(Gtk.Label(label=tr("Snooze duration (minutes)"), xalign=0.0))
        snooze_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        snooze_spin.set_value(alarm.snooze_minutes if alarm else 5)
        snooze_row.append(snooze_spin)
        box.append(snooze_row)

        sound_value = [alarm.sound if alarm else ""]
        sound_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sound_label = Gtk.Label(
            label=sound_value[0] or tr("default (system alarm)"),
            xalign=0.0,
        )
        sound_label.set_ellipsize(3)
        sound_label.set_hexpand(True)
        sound_row.append(sound_label)
        choose = Gtk.Button(label=tr("Choose…"))

        def choose_sound(_button) -> None:
            dialog = Gtk.FileChooserNative(
                title=tr("Choose a sound file"),
                action=Gtk.FileChooserAction.OPEN,
            )

            def chosen(_dialog, response, dialog) -> None:
                if response != Gtk.ResponseType.ACCEPT:
                    dialog.destroy()
                    return
                file = dialog.get_file()
                dialog.destroy()
                if file is None:
                    return
                path = file.get_path()
                sound_value[0] = path or ""
                sound_label.set_text(path or tr("default (system alarm)"))

            dialog.connect("response", chosen, dialog)
            dialog.show()

        choose.connect("clicked", choose_sound)
        sound_row.append(choose)

        def test_sound(_button) -> None:
            if self._player is None:
                return
            sound = self._resolve_sound(
                Alarm(label="Preview", sound=sound_value[0])
            )
            if sound:
                self._player.play(sound, duration=2.5)
                self.ctx.notify(tr("Playing a sound preview"))
            else:
                self.ctx.notify(tr("No system sound is available"))

        preview = Gtk.Button(label=tr("Test"))
        preview.connect("clicked", test_sound)
        sound_row.append(preview)
        box.append(Gtk.Label(label=tr("Sound"), xalign=0.0))
        box.append(sound_row)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=tr("Cancel"))
        cancel.connect("clicked", lambda _button: win.destroy())
        actions.append(cancel)
        save = Gtk.Button(label=tr("Save"))
        save.get_style_context().add_class("omnify-accent")
        actions.append(save)
        box.append(actions)

        def on_save(_button) -> None:
            selected = {
                index for index, button in day_buttons.items() if button.get_active()
            }
            label = label_entry.get_text().strip() or tr("Alarm")
            try:
                if alarm is None:
                    self._clock.add(
                        label=label,
                        hour=int(hour_spin.get_value()),
                        minute=int(minute_spin.get_value()),
                        days=selected,
                        sound=sound_value[0],
                        snooze_minutes=int(snooze_spin.get_value()),
                    )
                else:
                    self._clock.update(
                        alarm.id,
                        label=label,
                        hour=int(hour_spin.get_value()),
                        minute=int(minute_spin.get_value()),
                        days=selected,
                        sound=sound_value[0],
                        snooze_minutes=int(snooze_spin.get_value()),
                    )
            except (KeyError, ValueError) as exc:
                self.ctx.notify(str(exc))
                return
            self._save()
            self._render_alarms()
            self._update_header()
            win.destroy()
            self.ctx.notify(tr("Alarm saved"))

        save.connect("clicked", on_save)
        win.set_child(box)
        win.present()

    # ------------------------------------------------------------------
    # Ringing / header updates
    # ------------------------------------------------------------------
    def _update_header(self) -> None:
        if not hasattr(self, "_clock_label"):
            return
        now = _dt.datetime.now()
        self._clock_label.set_text(now.strftime("%H:%M:%S"))
        self._date_label.set_text(now.strftime("%A, %d %B"))
        next_alarm = self._clock.next_alarm(now)
        if next_alarm is None:
            self._next_label.set_text(tr("No alarms scheduled"))
        else:
            alarm, occurrence = next_alarm
            date_text = occurrence.strftime("%a, %d %b")
            if occurrence.date() == now.date():
                date_text = tr("Today")
            elif occurrence.date() == now.date() + _dt.timedelta(days=1):
                date_text = tr("Tomorrow")
            self._next_label.set_text(
                tr(
                    "Next: {time} · {label} ({date})",
                    time=occurrence.strftime("%H:%M"),
                    label=alarm.label,
                    date=date_text,
                )
            )

    def _start_audio(self, alarm: Alarm) -> None:
        sound = self._resolve_sound(alarm)
        if self._player is not None and sound:
            self._player.play(sound, duration=3600)
        if self._notifier is not None:
            self._notifier.notify(
                tr("Alarm"),
                f"{alarm.label} · {alarm.time_text}",
                urgency="critical",
                timeout_ms=60_000,
                icon="io.github.omnify",
            )
        self.ctx.notify(tr("Alarm: {label}", label=alarm.label))

    def _stop_audio(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass

    def _show_ringing(self) -> None:
        if not hasattr(self, "_ring_banner"):
            return
        if self._ringing_active is None:
            self._ring_banner.set_visible(False)
            return
        alarm = self._clock.get(self._ringing_active)
        if alarm is None:
            self._ring_banner.set_visible(False)
            return
        self._ring_banner.set_visible(True)
        extra = len(self._ringing_ids) - 1
        label = tr(
            "Alarm · {time} — {label}",
            time=alarm.time_text,
            label=alarm.label,
        )
        if extra > 0:
            label += " " + tr("(+{count} more)", count=extra)
        self._ring_label.set_text(label)
        self._snooze_button.set_label(
            tr("Snooze {minutes} min", minutes=alarm.snooze_minutes)
        )

    def _on_snooze_clicked(self, _button) -> None:
        if self._ringing_active is None:
            return
        alarm = self._clock.get(self._ringing_active)
        if alarm is None:
            return
        self._clock.snooze(alarm.id)
        self._stop_audio()
        if alarm.id in self._ringing_ids:
            self._ringing_ids.remove(alarm.id)
        self._ringing_active = None
        self._save()
        self._render_alarms()
        self._update_header()
        self.ctx.notify(
            tr("Snoozed for {minutes} minutes", minutes=alarm.snooze_minutes)
        )
        self._start_next_ringing()

    def _on_stop_clicked(self, _button) -> None:
        if self._ringing_active is None:
            return
        self._clock.mark_fired(self._ringing_active)
        if self._ringing_active in self._ringing_ids:
            self._ringing_ids.remove(self._ringing_active)
        self._stop_audio()
        self._ringing_active = None
        self._save()
        self._render_alarms()
        self._update_header()
        self._start_next_ringing()

    def _start_next_ringing(self) -> None:
        if not self._ringing_ids:
            self._ringing_active = None
            self._show_ringing()
            return
        alarm_id = self._ringing_ids[0]
        alarm = self._clock.get(alarm_id)
        if alarm is None:
            self._ringing_ids.pop(0)
            self._start_next_ringing()
            return
        self._ringing_active = alarm_id
        self._start_audio(alarm)
        self._show_ringing()

    # ------------------------------------------------------------------
    # BaseModule hooks
    # ------------------------------------------------------------------
    def pump(self) -> None:
        if not hasattr(self, "_clock_label"):
            return
        self._update_header()
        now = _dt.datetime.now()
        due = self._clock.due(now)
        new_ids = [alarm.id for alarm in due if alarm.id not in self._ringing_ids]
        for alarm_id in new_ids:
            if alarm_id not in self._ringing_ids:
                self._ringing_ids.append(alarm_id)
        # Drop stale entries (deleted/disabled/edited alarms).
        active_ids = {alarm.id for alarm in self._clock.all() if alarm.enabled}
        self._ringing_ids = [
            alarm_id for alarm_id in self._ringing_ids if alarm_id in active_ids
        ]
        if self._ringing_active is None and self._ringing_ids:
            self._start_next_ringing()
        elif self._ringing_active is not None and self._ringing_active not in self._ringing_ids:
            self._stop_audio()
            self._ringing_active = None
            self._start_next_ringing()
        else:
            self._show_ringing()

    def retranslate(self) -> None:
        super().retranslate()
        self._render_alarms()
        self._update_header()
        self._show_ringing()

    def on_show(self) -> None:
        self._render_alarms()
        self._update_header()

    def save_state(self) -> None:
        self._save()
