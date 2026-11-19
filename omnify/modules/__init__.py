"""Tool modules of Omnify.

Each file in this package is one self-contained tool: a pure model plus a
thin GTK view behind :meth:`~omnify.core.BaseModule.build_view`.
``ALL_MODULES`` is the single registry the app shell reads; adding a new tool
means adding one file here and appending its class to the list.
"""

from __future__ import annotations

from .alarm import AlarmModule
from .browser import BrowserModule
from .calculator import CalculatorModule
from .calendar import CalendarModule
from .dashboard import DashboardModule
from .stopwatch import StopwatchModule
from .sysinfo import SystemInfoModule
from .system import SystemMonitorModule
from .taskmanager import TaskManagerModule
from .timer import TimerModule
from .notes import NotesModule

#: Every tool the app shell shows in the sidebar, in display order.
#:
#: ``SystemMonitorModule`` is intentionally not listed: the System
#: Information module (hardware/software report) and the Task Manager
#: (performance graphs) cover the old live CPU/RAM/GPU view, so the
#: sidebar shows two focused system tools instead of three.  The module
#: itself stays importable for reuse.
ALL_MODULES = [
    DashboardModule,
    CalculatorModule,
    BrowserModule,
    CalendarModule,
    TimerModule,
    StopwatchModule,
    AlarmModule,
    TaskManagerModule,
    SystemInfoModule,
    NotesModule,
]

__all__ = [
    "ALL_MODULES",
    "AlarmModule",
    "BrowserModule",
    "CalculatorModule",
    "DashboardModule",
    "CalendarModule",
    "StopwatchModule",
    "SystemInfoModule",
    "SystemMonitorModule",
    "TaskManagerModule",
    "TimerModule",
    "NotesModule",
]
