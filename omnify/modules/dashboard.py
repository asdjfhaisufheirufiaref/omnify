"""The Omnify home dashboard.

This is the landing page: a live clock, quick actions, at-a-glance status
from the other modules and a card grid that opens every tool.  It owns no
persistent state and never imports GTK at module level.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Optional, Tuple

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .alarm import AlarmModule
from .browser import BrowserModule
from .browser_engine import detect_browser
from .calculator import CalculatorModule
from .calendar import CalendarModule
from .notes import NotesModule
from .stopwatch import StopwatchModule
from .taskmanager import TaskManagerModule
from .timer import TimerModule

#: Tiles in display order.  The dashboard itself is not repeated.
TOOL_MODULES = (
    NotesModule,
    CalculatorModule,
    BrowserModule,
    CalendarModule,
    TimerModule,
    StopwatchModule,
    AlarmModule,
    TaskManagerModule,
)


class DashboardModule(BaseModule):
    """Landing page with a clock, quick actions and module cards."""

    id = "dashboard"
    name = "Home"
    icon = "⌂"
    description = "Your starting point: live clock, next alarm and every Omnify tool."
    group = "Home"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._browser_info = detect_browser()
        self._greeting_cache = ""
        self._status_values: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self._root = root

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.get_style_context().add_class("dashboard-hero")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left.set_hexpand(True)
        self._greeting_label = Gtk.Label(label="Welcome", xalign=0.0)
        self._greeting_label.get_style_context().add_class("dashboard-greeting")
        left.append(self._greeting_label)
        self._date_label = Gtk.Label(label="", xalign=0.0)
        self._date_label.get_style_context().add_class("omnify-dim")
        left.append(self._date_label)
        self._intro_label = Gtk.Label(
            label="Everything in one place. Pick a tool below or jump straight in.",
            xalign=0.0,
        )
        self._intro_label.set_wrap(True)
        self._intro_label.get_style_context().add_class("dashboard-intro")
        left.append(self._intro_label)
        hero.append(left)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right.set_valign(Gtk.Align.CENTER)
        self._clock_label = Gtk.Label(label="--:--", xalign=1.0)
        self._clock_label.get_style_context().add_class("dashboard-clock")
        self._clock_label.get_style_context().add_class("omnify-mono")
        right.append(self._clock_label)
        self._clock_seconds = Gtk.Label(label="", xalign=1.0)
        self._clock_seconds.get_style_context().add_class("omnify-dim")
        right.append(self._clock_seconds)
        hero.append(right)
        root.append(hero)

        quick = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        journal_button = Gtk.Button(label=tr("Today's journal"))
        journal_button.get_style_context().add_class("dashboard-quick")
        journal_button.connect("clicked", lambda _button: self._open_journal())
        quick.append(journal_button)
        for label, module_id in (
            ("New calculation", "calculator"),
            ("Today's calendar", "calendar"),
            ("New timer", "timer"),
            ("Open browser", "browser"),
        ):
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("dashboard-quick")
            button.connect(
                "clicked", lambda _button, target=module_id: self.ctx.open_module(target)
            )
            quick.append(button)
        root.append(quick)

        root.append(self._build_glance(Gtk))
        root.append(self._build_tools(Gtk))

        tip = Gtk.Label(
            label="Tip: use the sidebar to move between tools; your timer and stopwatch keep running in the background.",
            xalign=0.0,
        )
        tip.set_wrap(True)
        tip.get_style_context().add_class("omnify-dim")
        root.append(tip)

        self._refresh_clock()
        self._refresh_glance()
        return root

    def _open_journal(self) -> None:
        """Open Notes and jump straight to today's journal note."""
        self.ctx.open_module("notes")
        module = self.ctx.get_module("notes")
        opener = getattr(module, "open_today_journal", None)
        if callable(opener):
            opener()

    def _build_glance(self, Gtk) -> Any:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("omnify-card")
        title = Gtk.Label(label="At a glance", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        card.append(title)

        self._glance_values: Dict[str, Any] = {}
        for key, label in (
            ("alarm", "Next alarm"),
            ("timers", "Timers"),
            ("stopwatch", "Stopwatch"),
            ("browser", "Browser"),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            name = Gtk.Label(label=label, xalign=0.0)
            name.set_size_request(130, -1)
            name.get_style_context().add_class("omnify-dim")
            row.append(name)
            value = Gtk.Label(label="—", xalign=0.0)
            value.set_hexpand(True)
            value.get_style_context().add_class("dashboard-value")
            row.append(value)
            self._glance_values[key] = value
            card.append(row)
        return card

    def _build_tools(self, Gtk) -> Any:
        title = Gtk.Label(label="Tools", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        title.set_margin_top(4)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrapper.append(title)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_column_homogeneous(True)
        self._tool_grid = grid
        for index, module_cls in enumerate(TOOL_MODULES):
            tile = Gtk.Button()
            tile.get_style_context().add_class("dashboard-tile")
            tile.set_hexpand(True)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_hexpand(True)
            content.set_margin_top(12)
            content.set_margin_bottom(12)
            content.set_margin_start(12)
            content.set_margin_end(12)
            icon = self._make_icon(Gtk, module_cls.icon)
            if icon is not None:
                content.append(icon)
            name = Gtk.Label(label=tr(module_cls.name), xalign=0.0)
            name.get_style_context().add_class("dashboard-tile-title")
            content.append(name)
            description = Gtk.Label(label=tr(module_cls.description), xalign=0.0)
            description.set_wrap(True)
            description.set_max_width_chars(26)
            description.set_hexpand(True)
            description.get_style_context().add_class("omnify-dim")
            content.append(description)
            tile.set_child(content)
            tile.connect(
                "clicked",
                lambda _button, target=module_cls.id: self.ctx.open_module(target),
            )
            grid.attach(tile, index % 3, index // 3, 1, 1)
        wrapper.append(grid)
        return wrapper

    # ------------------------------------------------------------------
    # Live data
    # ------------------------------------------------------------------
    def _refresh_clock(self) -> None:
        if not hasattr(self, "_clock_label"):
            return
        now = _dt.datetime.now()
        self._clock_label.set_text(now.strftime("%H:%M"))
        self._clock_seconds.set_text(now.strftime(":%S"))
        greeting = self._greeting(now.hour)
        if greeting != self._greeting_cache:
            self._greeting_cache = greeting
            self._greeting_label.set_text(tr(greeting))
        self._date_label.set_text(now.strftime("%A, %d %B"))

    @staticmethod
    def _make_icon(Gtk, icon: str):
        """Use an emoji label or an icon-theme image, never a raw icon name."""
        if not icon:
            return None
        if not icon.isascii():
            label = Gtk.Label(label=icon, xalign=0.0)
            label.get_style_context().add_class("dashboard-tile-icon")
            return label
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(30)
        image.set_halign(Gtk.Align.START)
        return image

    @staticmethod
    def _greeting(hour: int) -> str:
        if hour < 5:
            return "Good night"
        if hour < 12:
            return "Good morning"
        if hour < 18:
            return "Good afternoon"
        return "Good evening"

    def _module(self, module_id: str):
        try:
            return self.ctx.get_module(module_id)
        except Exception:
            return None

    def _next_alarm_text(self) -> str:
        module = self._module("alarm")
        clock = getattr(module, "_clock", None)
        if clock is None:
            return tr("No alarms scheduled")
        pair = clock.next_alarm()
        if pair is None:
            return tr("No alarms scheduled")
        alarm, occurrence = pair
        time_text = occurrence.strftime("%H:%M")
        today = _dt.date.today()
        if occurrence.date() == today:
            date_text = tr("Today")
        elif occurrence.date() == today + _dt.timedelta(days=1):
            date_text = tr("Tomorrow")
        else:
            date_text = occurrence.strftime("%a, %d %b")
        return tr(
            "{time} · {label} ({date})",
            time=time_text,
            label=alarm.label,
            date=date_text,
        )

    def _timer_text(self) -> str:
        module = self._module("timer")
        manager = getattr(module, "_manager", None)
        if manager is None:
            return tr("No timers yet")
        timers = manager.all()
        if not timers:
            return tr("No timers yet")
        running = sum(1 for entry in timers if entry.state == "running")
        return tr(
            "{running} running · {total} total",
            running=running,
            total=len(timers),
        )

    def _stopwatch_text(self) -> str:
        module = self._module("stopwatch")
        model = getattr(module, "_model", None)
        if model is None:
            return tr("Stopped")
        states = {
            "running": tr("Running"),
            "paused": tr("Paused"),
            "stopped": tr("Stopped"),
        }
        return states.get(model.state, tr("Stopped"))

    def _browser_text(self) -> str:
        self._browser_info = detect_browser()
        info = self._browser_info
        if info is None:
            return tr("Not found")
        if info.is_librewolf:
            return tr("LibreWolf ready")
        if info.key == "zen":
            return tr("Zen Browser detected")
        return tr("Firefox fallback")

    def _refresh_glance(self) -> None:
        if not hasattr(self, "_glance_values"):
            return
        self._glance_values["alarm"].set_text(self._next_alarm_text())
        self._glance_values["timers"].set_text(self._timer_text())
        self._glance_values["stopwatch"].set_text(self._stopwatch_text())
        self._glance_values["browser"].set_text(self._browser_text())

    # ------------------------------------------------------------------
    # BaseModule hooks
    # ------------------------------------------------------------------
    def pump(self) -> None:
        self._refresh_clock()
        self._refresh_glance()

    def on_show(self) -> None:
        self._refresh_clock()
        self._refresh_glance()

    def retranslate(self) -> None:
        super().retranslate()
        self._greeting_cache = ""
        self._refresh_clock()
        self._refresh_glance()
