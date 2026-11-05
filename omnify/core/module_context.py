"""Shared plumbing for tool modules.

:class:`ModuleContext` is the only object a module receives from the core.
It bundles everything a module needs without letting modules reach into the
application internals:

* ``storage`` — the shared :class:`~omnify.core.storage.Storage`.
* ``theme``   — the shared :class:`~omnify.core.theme.Theme`.
* ``settings``— a tiny dict-backed, persisted preferences object.
* ``notify``  — show a toast in the app status bar (no-op in tests).
* ``open_module`` — navigate to another module by id (no-op in tests).

This indirection is what makes the modules unit-testable headlessly and keeps
the architecture ready for future additions (AI assistant, automation, …).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class Settings:
    """A persisted dict of application preferences (stored under ``app.json``)."""

    KEY = "app"

    def __init__(self, storage) -> None:
        self._storage = storage
        self._data: Dict[str, Any] = storage.load(self.KEY, {}) or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) != value:
            self._data[key] = value
            self._storage.save(self.KEY, self._data)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._data)


class ModuleContext:
    """The handle handed to every module on construction."""

    def __init__(
        self,
        storage,
        theme=None,
        notify: Optional[Callable[[str], None]] = None,
        open_module: Optional[Callable[[str], None]] = None,
        get_module: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.storage = storage
        self.theme = theme
        self.settings = Settings(storage)
        self.notify = notify or (lambda message: None)
        self.open_module = open_module or (lambda module_id: None)
        #: Look up another live module instance by id (``None`` when absent).
        #: Used by the calendar to start timers/stopwatches from events.
        self.get_module = get_module or (lambda module_id: None)


class BaseModule:
    """Interface every tool in :mod:`omnify.modules` implements.

    Subclasses must define the class attributes :attr:`id`, :attr:`name`,
    :attr:`icon` and implement :meth:`build_view`. They may also expose an
    optional :meth:`on_show` hook (called each time the module is displayed)
    and an optional :meth:`on_hide` hook.
    """

    #: Stable identifier, used for navigation and persistence namespacing.
    id: str = ""
    #: Human-friendly name shown in the sidebar.
    name: str = ""
    #: Icon name (Adwaita icon name) or emoji fallback shown in the sidebar.
    icon: str = ""
    #: Short description shown on the dashboard.
    description: str = ""
    #: Optional group for sidebar layout (e.g. "Tools", "System").
    group: str = "Tools"

    def __init__(self, context: ModuleContext) -> None:
        self.ctx = context
        self._view = None

    def build_view(self):
        """Return the GTK widget that displays this module. Override."""
        raise NotImplementedError

    def on_show(self) -> None:
        """Called before the view is shown. Override for live updates."""

    def on_hide(self) -> None:
        """Called when another module becomes active. Override to stop timers."""

    def on_window_closed(self) -> None:
        """Called when the main window is hidden (closed to the tray).

        Modules that schedule work for "when the user stops looking"
        (the calendar's event actions) hook in here.
        """

    def retranslate(self) -> None:
        """Apply the active UI language to the already-built view."""
        if self._view is None:
            return
        try:
            from .i18n import translate_widget_tree

            translate_widget_tree(self._view)
        except Exception:
            pass

    @property
    def view(self):
        if self._view is None:
            self._view = self.build_view()
        return self._view
