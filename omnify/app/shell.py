"""The Omnify application shell.

This is the first layer that creates GTK widgets: it builds a single
``Gtk.ApplicationWindow`` (1000x700) containing

* a draggable ``Gtk.Paned`` separating
  - a sidebar with grouped toggle buttons (one per registered module)
    plus Settings and About buttons at the bottom, and
  - a ``Gtk.Stack`` with one eagerly built view per module and a
    Settings placeholder,
* a dim status bar fed by :meth:`ModuleContext.notify` that auto-clears.

The theme (mode + accent) is read from persisted settings before the window
is shown and applied to the default screen. Modules are constructed with a
:class:`~omnify.core.ModuleContext` whose ``notify`` and ``open_module``
callbacks are wired to the status bar and the sidebar respectively.

Only this file imports GTK at module level; the model code in
:mod:`omnify.core` and the tools in :mod:`omnify.modules` stay headless.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from omnify import __version__
from omnify.core import BaseModule, ModuleContext, Storage, Theme
from omnify.core import i18n
from omnify.core.logfile import log
from omnify.core.theme import (
    COLOR_TOKENS,
    normalize_custom,
    resolve_color,
    theme_palette,
    valid_hex,
)
from omnify.modules import ALL_MODULES

#: How long a status-bar message stays visible before it fades away.
_STATUS_CLEAR_MS = 5000


class OmnifyApp(Gtk.Application):
    """The GTK application: one window holding sidebar, module stack, status bar."""

    def __init__(self, initial_module: Optional[str] = None) -> None:
        super().__init__(
            application_id="io.github.omnify",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._window: Optional[Gtk.ApplicationWindow] = None
        self._modules: Dict[str, BaseModule] = {}
        self._nav_buttons: Dict[str, Gtk.ToggleButton] = {}
        self._current_id: Optional[str] = None
        self._initial_module: Optional[str] = initial_module
        self._status_timeout_id: Optional[int] = None
        # Populated by :meth:`_build_window`, which runs before any of the
        # callbacks below can fire.
        self.ctx: Optional[ModuleContext] = None
        self.theme: Optional[Theme] = None
        self._paned: Optional[Gtk.Paned] = None
        self._stack: Optional[Gtk.Stack] = None
        self._status_label: Optional[Gtk.Label] = None
        # Background surfaces (set up in do_activate, see
        # :meth:`_start_tray_and_ticker`).
        self._sni = None  # StatusNotifierItem (system tray)
        self._settings_page: Optional[Gtk.Widget] = None
        self._language_combo: Optional[Gtk.ComboBoxText] = None
        self._theme_combo: Optional[Gtk.ComboBoxText] = None
        self._accent_combo: Optional[Gtk.ComboBoxText] = None
        self._sidebar_combo: Optional[Gtk.ComboBoxText] = None
        self._sidebar_width_spin: Optional[Gtk.SpinButton] = None
        self._bg_image_label: Optional[Gtk.Label] = None
        self._color_buttons: Dict[str, Any] = {}
        self._custom_colors: Dict[str, str] = {}
        self._custom_options: Dict[str, Any] = {}
        self._sidebar_widget: Optional[Gtk.Widget] = None
        self._sidebar_side = "left"
        self._sidebar_width = 220
        self._settings_updating = False
        self._mini_window: Optional[Gtk.Window] = None
        self._mini_list: Optional[Gtk.Box] = None
        self._global_tick_id: Optional[int] = None
        self._tray_title_cache: str = ""
        self._tray_theme_key: Optional[tuple] = None

    # ------------------------------------------------------------------
    # Gtk.Application
    # ------------------------------------------------------------------
    def do_command_line(self, command_line) -> int:
        """Handle ``omnify --module <id>`` in the running instance."""
        args = list(command_line.get_arguments() or [])
        module_id: Optional[str] = None
        index = 0
        while index < len(args):
            arg = str(args[index])
            if arg == "--module" and index + 1 < len(args):
                module_id = str(args[index + 1])
                index += 2
                continue
            if arg.startswith("--module="):
                module_id = arg.split("=", 1)[1]
            index += 1
        known = {module.id for module in ALL_MODULES} | {"settings"}
        if module_id in known:
            self._initial_module = module_id
        if self._window is None:
            self.activate()
        else:
            self._window.present()
            if self._current_id != self._initial_module:
                self._switch_to(self._initial_module or ALL_MODULES[0].id)
            elif self._current_id is not None:
                self._switch_to(self._current_id)
        return 0

    def do_activate(self) -> None:
        """Create the main window on first activation, then present it."""
        Gtk.Window.set_default_icon_name("io.github.omnify")
        if self._window is None:
            self._window = self._build_window()
            i18n.translate_widget_tree(self._window)
            self._start_tray_and_ticker()
            self._window.present()
            # Wayland ignores a maximize request made before the surface is
            # mapped.  Queue it for the next main-loop iteration instead.
            GLib.idle_add(self._maximize_main_window)
        else:
            self._window.present()

    def _maximize_main_window(self) -> bool:
        """Best-effort maximize after the first frame is mapped."""
        if self._window is not None:
            self._window.maximize()
        return False

    def do_shutdown(self) -> None:
        """Persist module state and tear down the tray on quit."""
        if self._global_tick_id is not None:
            GLib.source_remove(self._global_tick_id)
            self._global_tick_id = None
        for module in self._modules.values():
            saver = getattr(module, "save_state", None)
            if callable(saver):
                try:
                    saver()
                except Exception:
                    pass
        if self._sni is not None:
            try:
                self._sni.shutdown()
            except Exception:
                pass
            self._sni = None
        if self._mini_window is not None:
            self._mini_window.destroy()
            self._mini_window = None
            self._mini_list = None
        # Chain to the default vfunc explicitly; this PyGObject build does not
        # expose the convenience ``Application.shutdown()`` wrapper.
        try:
            Gtk.Application.do_shutdown(self)
        except (AttributeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # System tray (StatusNotifierItem) + floating mini-window
    # ------------------------------------------------------------------
    def _start_tray_and_ticker(self) -> None:
        """Register the tray icon (graceful if no watcher) and start the
        global 1-second ticker that keeps timers running while hidden.

        The tray speaks raw D-Bus (see :mod:`omnify.core.sni`) because
        no ``gi`` path on this build can receive method calls; any
        failure here simply leaves the floating window as the only
        background surface.
        """
        try:
            from omnify.core.sni import StatusNotifierItem

            # The pixel fallback is drawn in the theme foreground colour so
            # it stays visible whether the tray is light or dark.
            icon_rgb = resolve_color(
                self.theme.mode, self.theme.accent, "fg", self.theme.custom
            ) if self.theme is not None else (1.0, 1.0, 1.0)
            sni = StatusNotifierItem(
                item_id="omnify",
                title="Omnify",
                on_activate=self._on_tray_activate,
                icon_rgb=icon_rgb,
            )
            if sni.start():
                self._sni = sni
                self._tray_theme_key = (
                    self.theme.mode if self.theme else "",
                    self.theme.accent if self.theme else "",
                )
        except Exception:
            # Tray is a convenience, not a dependency.
            pass
        self._start_global_ticker()

    def _start_global_ticker(self) -> None:
        if self._global_tick_id is None:
            self._global_tick_id = GLib.timeout_add(1000, self._on_global_tick)

    def _on_global_tick(self) -> bool:
        """1 s heartbeat: pump every module, refresh tray + mini window."""
        for module in self._modules.values():
            pump = getattr(module, "pump", None)
            if callable(pump):
                try:
                    pump()
                except Exception as exc:
                    # Never let one module's pump break the others — but
                    # record the failure (a silent pass hid the sound/
                    # notification regression for days).
                    log(f"global tick: {module.id} pump failed: {exc!r}")
        title = self._tray_title()
        if self._sni is not None and title != self._tray_title_cache:
            self._sni.update_title(title)
            self._tray_title_cache = title
        if self._sni is not None and self.theme is not None:
            key = (
                self.theme.mode,
                self.theme.accent,
                tuple(sorted(self.theme.custom.items())),
            )
            if key != self._tray_theme_key:
                self._sni.set_icon_rgb(
                    resolve_color(
                        self.theme.mode,
                        self.theme.accent,
                        "fg",
                        self.theme.custom,
                    )
                )
                self._tray_theme_key = key
        if self._mini_window is not None and self._mini_window.is_visible():
            self._refresh_mini()
        return True

    def _tray_title(self) -> str:
        """The tray text: running timers/stopwatch, or just 'Omnify'."""
        parts = []
        for module_id in ("timer", "stopwatch"):
            module = self._modules.get(module_id)
            snapshot = getattr(module, "tray_snapshot", None)
            if callable(snapshot):
                try:
                    text = snapshot()
                except Exception:
                    text = None
                if text:
                    parts.append(text)
        return "  ".join(parts) if parts else "Omnify"

    def _on_tray_activate(self, _x: int, _y: int) -> None:
        """Tray click: toggle the floating mini-window.

        Arrives on the D-Bus reader thread — hop to the main thread
        before touching widgets.
        """
        GLib.idle_add(self._tray_activate_main)

    def _on_main_close_request(self, _window: Gtk.Window) -> bool:
        """Hide the main window instead of quitting (keep timers alive)."""
        self._window.hide()
        for module in self._modules.values():
            hook = getattr(module, "on_window_closed", None)
            if callable(hook):
                try:
                    hook()
                except Exception as exc:
                    log(f"on_window_closed: {module.id} failed: {exc!r}")
        return True  # do not destroy the window

    def _tray_activate_main(self) -> bool:
        if self._mini_window is None:
            self._mini_window = self._build_mini_window()
        if self._mini_window.is_visible():
            self._mini_window.hide()
        else:
            self._refresh_mini()
            self._mini_window.present()
        return False

    def _build_mini_window(self) -> Gtk.Window:
        win = Gtk.Window(application=self)
        win.set_title("Omnify")
        win.set_resizable(False)
        # GTK 4.24 on this system has no set_skip_taskbar_hint (that was a
        # GTK3 API); calling it killed the window builder and made the
        # tray icon look dead. Keep the call only if a future build has it.
        if hasattr(win, "set_skip_taskbar_hint"):
            win.set_skip_taskbar_hint(True)
        win.set_default_size(300, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Omnify", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        header.append(title)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)
        close_button = Gtk.Button(label="✕")
        close_button.set_tooltip_text("Hide (timers keep running)")
        close_button.connect("clicked", lambda _b: self._mini_window.hide())
        header.append(close_button)
        box.append(header)

        self._mini_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._mini_list.set_hexpand(True)
        box.append(self._mini_list)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_button = Gtk.Button(label="Open Omnify")
        open_button.set_hexpand(True)
        open_button.connect("clicked", self._on_mini_open)
        buttons.append(open_button)
        quit_button = Gtk.Button(label="Quit")
        quit_button.set_tooltip_text("Stop Omnify completely (timers are saved)")
        quit_button.connect("clicked", lambda _b: self.quit())
        buttons.append(quit_button)
        box.append(buttons)

        win.set_child(box)
        return win

    def _refresh_mini(self) -> None:
        if self._mini_window is None or self._mini_list is None:
            return
        while True:
            child = self._mini_list.get_first_child()
            if child is None:
                break
            self._mini_list.remove(child)
        lines = []
        for module_id in ("timer", "stopwatch"):
            module = self._modules.get(module_id)
            get_lines = getattr(module, "mini_lines", None)
            if callable(get_lines):
                try:
                    lines.extend(get_lines())
                except Exception:
                    pass
        if not lines:
            note = Gtk.Label(label="Nothing running", xalign=0.0)
            note.get_style_context().add_class("omnify-dim")
            self._mini_list.append(note)
        for line in lines:
            label = Gtk.Label(label=line, xalign=0.0)
            label.get_style_context().add_class("omnify-mono")
            self._mini_list.append(label)

    def _on_mini_open(self, _button) -> None:
        if self._mini_window is not None:
            self._mini_window.hide()
        if self._window is not None:
            self._window.present()

    # ------------------------------------------------------------------
    # Public API used by modules (via ModuleContext)
    # ------------------------------------------------------------------
    def open_module(self, module_id: str) -> None:
        """Navigate to *module_id*; wired into ``ModuleContext.open_module``."""
        button = self._nav_buttons.get(module_id)
        if button is not None and not button.get_active():
            button.set_active(True)

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------
    def _build_window(self) -> Gtk.ApplicationWindow:
        self.theme = Theme()
        self.ctx = ModuleContext(
            Storage(),
            theme=self.theme,
            notify=self._notify,
            open_module=self.open_module,
            get_module=lambda module_id: self._modules.get(module_id),
        )
        self._custom_colors = normalize_custom(
            self.ctx.settings.get("theme_custom", {})
        )
        self._custom_options = {
            "font_family": self.ctx.settings.get("theme_font", ""),
            "radius": self.ctx.settings.get("theme_radius", 8),
            "background_image": self.ctx.settings.get("theme_background", ""),
        }
        self._sidebar_side = self.ctx.settings.get("sidebar_side", "left")
        self._sidebar_width = int(self.ctx.settings.get("sidebar_width", 220) or 220)
        self.theme.set(
            self.ctx.settings.get("theme_mode", "dark"),
            self.ctx.settings.get("theme_accent", "blue"),
        )
        self.theme.set_custom(self._custom_colors, self._custom_options)
        i18n.set_language(self.ctx.settings.get("language", i18n.DEFAULT_LANGUAGE))
        display = Gdk.Display.get_default()
        if display is not None:
            self.theme.apply(display)

        window = Gtk.ApplicationWindow(application=self)
        window.set_title("Omnify")
        window.set_icon_name("io.github.omnify")
        # Wide enough for the Task Manager tables at their natural size;
        # every module now also fits its own (much smaller) minimum.
        window.set_default_size(1280, 800)
        # Closing the window hides it (timers/stopwatch keep running and
        # the tray icon stays); quitting is explicit via the tray window.
        window.connect("close-request", self._on_main_close_request)

        # Sidebar and content live in a draggable Paned so the user can
        # give either side more room (and move the sidebar to the right).
        self._stack = self._build_stack()
        self._sidebar_widget = self._build_sidebar()
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_vexpand(True)
        self._paned.set_resize_start_child(False)
        self._paned.set_shrink_start_child(False)
        self._apply_sidebar_layout()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self._paned)
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._status_label = Gtk.Label(label="", xalign=0.0)
        self._status_label.get_style_context().add_class("omnify-dim")
        self._status_label.set_margin_top(4)
        self._status_label.set_margin_bottom(6)
        self._status_label.set_margin_start(12)
        self._status_label.set_margin_end(12)
        outer.append(self._status_label)

        window.set_child(outer)

        # Initial view: the requested module (for per-app desktop launchers),
        # otherwise the first registered module (Dashboard).
        initial = self._initial_module
        if initial in self._nav_buttons:
            self._nav_buttons[initial].set_active(True)
        elif initial == "settings":
            self._switch_to("settings")
            self._clear_nav_selection()
        else:
            self._nav_buttons[ALL_MODULES[0].id].set_active(True)
        return window

    def _build_sidebar(self) -> Gtk.Box:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.get_style_context().add_class("omnify-sidebar")
        sidebar.set_size_request(max(160, min(400, self._sidebar_width)), -1)
        sidebar.set_margin_top(12)
        sidebar.set_margin_bottom(12)
        sidebar.set_margin_start(8)
        sidebar.set_margin_end(8)

        header = Gtk.Label(label="Omnify", xalign=0.0)
        header.get_style_context().add_class("omnify-card-title")
        header.set_margin_top(4)
        header.set_margin_start(8)
        header.set_margin_end(8)
        sidebar.append(header)

        # One listbox per group with a title label above it (GTK 4 on this
        # system has no ListBoxSection/ListBoxFilterSection class).
        list_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        groups: Dict[str, List[Type[BaseModule]]] = {}
        for module_cls in ALL_MODULES:
            groups.setdefault(module_cls.group or "General", []).append(module_cls)

        first_button: Optional[Gtk.ToggleButton] = None
        for group_name, module_classes in groups.items():
            title = Gtk.Label(label=i18n.tr(group_name), xalign=0.0)
            title.get_style_context().add_class("omnify-group-title")
            title.set_margin_top(8)
            title.set_margin_start(10)
            list_area.append(title)

            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            for module_cls in module_classes:
                row = Gtk.ListBoxRow()
                row.set_activatable(False)
                button = self._build_nav_button(module_cls)
                if first_button is None:
                    first_button = button
                else:
                    # Radio semantics: GTK keeps exactly one button active.
                    button.set_group(first_button)
                row.set_child(button)
                listbox.append(row)
            list_area.append(listbox)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(list_area)
        sidebar.append(scroller)

        sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        settings_button = Gtk.Button(label="⚙ Settings")
        settings_button.get_style_context().add_class("omnify-nav-button")
        settings_button.set_hexpand(True)
        settings_button.connect("clicked", self._on_settings_clicked)
        sidebar.append(settings_button)

        about_button = Gtk.Button(label="About")
        about_button.get_style_context().add_class("omnify-nav-button")
        about_button.set_hexpand(True)
        about_button.connect("clicked", self._on_about_clicked)
        sidebar.append(about_button)

        return sidebar

    def _apply_sidebar_layout(self) -> None:
        """Place the sidebar left/right and restore its saved width."""
        if self._paned is None or self._stack is None or self._sidebar_widget is None:
            return
        width = max(160, min(400, int(self._sidebar_width or 220)))
        self._sidebar_widget.set_size_request(width, -1)
        if self._sidebar_side == "right":
            self._paned.set_start_child(self._stack)
            self._paned.set_end_child(self._sidebar_widget)
            self._paned.set_resize_start_child(True)
        else:
            self._paned.set_start_child(self._sidebar_widget)
            self._paned.set_end_child(self._stack)
            self._paned.set_resize_start_child(False)
        self._paned.set_shrink_start_child(False)
        if self._window is not None and self._window.get_width() > 0:
            available = max(320, self._window.get_width() - width)
            self._paned.set_position(
                available if self._sidebar_side == "right" else width
            )
        else:
            self._paned.set_position(
                max(320, 1280 - width) if self._sidebar_side == "right" else width
            )

    def _build_nav_button(self, module_cls: Type[BaseModule]) -> Gtk.ToggleButton:
        button = Gtk.ToggleButton()
        button.get_style_context().add_class("omnify-nav-button")
        button.set_hexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(8)
        box.set_margin_end(8)

        box.append(self._make_glyph(module_cls.icon))

        name = Gtk.Label(label=i18n.tr(module_cls.name), xalign=0.0)
        name.set_hexpand(True)
        box.append(name)

        button.set_child(box)
        self._nav_buttons[module_cls.id] = button
        button.connect("toggled", self._on_nav_toggled, module_cls.id)
        return button

    @staticmethod
    def _make_glyph(icon: str) -> Gtk.Widget:
        """Create the icon widget for a sidebar button.

        Emoji icons are shown as labels; ASCII names are resolved against the
        icon theme and fall back to a generic executable icon.
        """
        if icon and not icon.isascii():
            return Gtk.Label(label=icon)
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
        if icon and theme is not None and theme.has_icon(icon):
            return Gtk.Image.new_from_icon_name(icon)
        return Gtk.Image.new_from_icon_name("application-x-executable-symbolic")

    def _build_stack(self) -> Gtk.Stack:
        stack = Gtk.Stack()
        stack.set_hexpand(True)
        stack.set_vexpand(True)
        # A homogeneous stack (the default) requests the size of its
        # WIDEST child for every page, so the Task Manager tables forced
        # a ~1100 px minimum width on the Calculator, the Timer, etc., and
        # the window's right edge clipped content until the user dragged
        # the window wider. With non-homogeneous sizing each module only
        # asks for what it needs.
        stack.set_hhomogeneous(False)
        stack.set_vhomogeneous(False)

        assert self.ctx is not None  # set at the top of _build_window
        for module_cls in ALL_MODULES:
            module = module_cls(self.ctx)
            self._modules[module.id] = module
            scroller = Gtk.ScrolledWindow()
            # Horizontal AUTOMATIC: wide content (Task Manager tables)
            # scrolls instead of stretching the window past the screen.
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            # A long settings/browser page must not force the toplevel window
            # taller than the monitor.  The scroller owns the overflow; the
            # window keeps its normal 1280×800/maximized geometry.
            scroller.set_propagate_natural_width(False)
            scroller.set_propagate_natural_height(False)
            scroller.set_hexpand(True)
            scroller.set_vexpand(True)
            scroller.set_child(module.view)
            i18n.translate_widget_tree(module.view)
            stack.add_titled(
                scroller, module.id, i18n.tr(module.name)
            )

        self._settings_page = self._build_settings_page()
        i18n.translate_widget_tree(self._settings_page)
        stack.add_titled(
            self._settings_page, "settings", i18n.tr("Settings")
        )
        return stack

    def _build_settings_page(self) -> Gtk.Box:
        """Build the appearance, customization, language and data page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        heading = Gtk.Label(label="Settings", xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        box.append(heading)

        appearance = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        appearance.get_style_context().add_class("omnify-card")
        appearance_title = Gtk.Label(label="Appearance", xalign=0.0)
        appearance_title.get_style_context().add_class("omnify-card-title")
        appearance.append(appearance_title)

        self._theme_combo = self._settings_combo()
        self._accent_combo = self._settings_combo()
        self._language_combo = self._settings_combo()
        for label_text, combo in (
            ("Theme mode", self._theme_combo),
            ("Accent colour", self._accent_combo),
            ("Language", self._language_combo),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label = Gtk.Label(label=label_text, xalign=0.0)
            label.set_size_request(170, -1)
            row.append(label)
            combo.set_hexpand(True)
            row.append(combo)
            appearance.append(row)

        language_note = Gtk.Label(
            label="Language applies immediately to every module.", xalign=0.0
        )
        language_note.get_style_context().add_class("omnify-dim")
        appearance.append(language_note)
        appearance.append(self._build_custom_theme_controls())
        box.append(appearance)

        files = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        files.get_style_context().add_class("omnify-card")
        files_title = Gtk.Label(label="Themes and backup", xalign=0.0)
        files_title.get_style_context().add_class("omnify-card-title")
        files.append(files_title)
        files_note = Gtk.Label(
            label=(
                "Export your colours, font, radius and background as a portable "
                "JSON file; import it on another machine."
            ),
            xalign=0.0,
        )
        files_note.set_wrap(True)
        files_note.get_style_context().add_class("omnify-dim")
        files.append(files_note)
        files_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        export_button = Gtk.Button(label="Export theme…")
        export_button.connect("clicked", self._on_export_theme)
        files_row.append(export_button)
        import_button = Gtk.Button(label="Import theme…")
        import_button.connect("clicked", self._on_import_theme)
        files_row.append(import_button)
        files.append(files_row)
        box.append(files)

        data_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        data_card.get_style_context().add_class("omnify-card")
        data_title = Gtk.Label(label="Data", xalign=0.0)
        data_title.get_style_context().add_class("omnify-card-title")
        data_card.append(data_title)
        data_note = Gtk.Label(
            label="Settings are stored locally in ~/.local/share/omnify.",
            xalign=0.0,
        )
        data_note.get_style_context().add_class("omnify-dim")
        data_card.append(data_note)
        data_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_data = Gtk.Button(label="Open data folder")
        open_data.connect("clicked", self._on_open_data_folder)
        data_row.append(open_data)
        clear_button = Gtk.Button(label="Clear all data")
        clear_button.get_style_context().add_class("omnify-danger")
        clear_button.connect("clicked", self._on_clear_all_data)
        data_row.append(clear_button)
        data_card.append(data_row)
        box.append(data_card)

        self._refresh_settings_combos()
        self._refresh_color_buttons()
        self._theme_combo.connect("changed", self._on_theme_combo_changed)
        self._accent_combo.connect("changed", self._on_accent_combo_changed)
        self._language_combo.connect("changed", self._on_language_combo_changed)
        return box

    def _build_custom_theme_controls(self) -> Gtk.Widget:
        """An expander with colour swatches, font, radius and layout controls."""
        expander = Gtk.Expander(label="Custom colours, font and layout")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(8)

        intro = Gtk.Label(
            label=(
                "Start from the current palette. Each swatch changes one "
                "theme token immediately; Reset restores the selected mode."
            ),
            xalign=0.0,
        )
        intro.set_wrap(True)
        intro.get_style_context().add_class("omnify-dim")
        body.append(intro)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)
        for index, (alias, label_text) in enumerate(COLOR_TOKENS):
            label = Gtk.Label(label=i18n.tr(label_text), xalign=0.0)
            label.set_hexpand(True)
            button = Gtk.ColorButton()
            button.set_use_alpha(False)
            button.set_tooltip_text(i18n.tr(label_text))
            button.connect("color-set", self._on_color_button_set, alias)
            self._color_buttons[alias] = button
            grid.attach(label, 0, index, 1, 1)
            grid.attach(button, 1, index, 1, 1)
        body.append(grid)

        reset = Gtk.Button(label="Reset custom colours")
        reset.connect("clicked", self._on_reset_custom_colours)
        body.append(reset)

        layout_title = Gtk.Label(label="Font and layout", xalign=0.0)
        layout_title.get_style_context().add_class("omnify-card-title")
        layout_title.set_margin_top(8)
        body.append(layout_title)

        font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        font_row.append(Gtk.Label(label="UI font", xalign=0.0))
        self._font_entry = Gtk.Entry()
        self._font_entry.set_placeholder_text("Fira Sans")
        self._font_entry.set_text(str(self._custom_options.get("font_family", "") or ""))
        self._font_entry.set_hexpand(True)
        self._font_entry.connect("activate", self._on_layout_controls_changed)
        font_row.append(self._font_entry)
        apply_font = Gtk.Button(label="Apply")
        apply_font.connect("clicked", self._on_layout_controls_changed)
        font_row.append(apply_font)
        body.append(font_row)

        radius_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        radius_row.append(Gtk.Label(label="Corner radius (px)", xalign=0.0))
        self._radius_spin = Gtk.SpinButton.new_with_range(0, 24, 1)
        self._radius_spin.set_value(int(self._custom_options.get("radius", 8) or 8))
        self._radius_spin.connect("value-changed", self._on_layout_controls_changed)
        radius_row.append(self._radius_spin)
        body.append(radius_row)

        sidebar_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sidebar_row.append(Gtk.Label(label="Sidebar position", xalign=0.0))
        self._sidebar_combo = Gtk.ComboBoxText()
        self._sidebar_combo.append_text(i18n.tr("Left"))
        self._sidebar_combo.append_text(i18n.tr("Right"))
        self._sidebar_combo.set_active(1 if self._sidebar_side == "right" else 0)
        self._sidebar_combo.connect("changed", self._on_sidebar_layout_changed)
        sidebar_row.append(self._sidebar_combo)
        sidebar_row.append(Gtk.Label(label="Width", xalign=0.0))
        self._sidebar_width_spin = Gtk.SpinButton.new_with_range(160, 400, 10)
        self._sidebar_width_spin.set_value(self._sidebar_width)
        self._sidebar_width_spin.connect("value-changed", self._on_sidebar_layout_changed)
        sidebar_row.append(self._sidebar_width_spin)
        body.append(sidebar_row)

        image_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        image_row.append(Gtk.Label(label="Background image", xalign=0.0))
        self._bg_image_label = Gtk.Label(label="", xalign=0.0)
        self._bg_image_label.set_ellipsize(3)
        self._bg_image_label.set_hexpand(True)
        self._bg_image_label.get_style_context().add_class("omnify-dim")
        image_row.append(self._bg_image_label)
        choose_image = Gtk.Button(label="Choose…")
        choose_image.connect("clicked", self._on_choose_background)
        image_row.append(choose_image)
        clear_image = Gtk.Button(label="Clear")
        clear_image.connect("clicked", self._on_clear_background)
        image_row.append(clear_image)
        body.append(image_row)
        self._refresh_background_label()

        expander.set_child(body)
        return expander

    def _refresh_background_label(self) -> None:
        if self._bg_image_label is None:
            return
        image = str(self._custom_options.get("background_image", "") or "")
        self._bg_image_label.set_text(Path(image).name if image else i18n.tr("None"))

    def _refresh_color_buttons(self) -> None:
        """Show the current effective palette in the colour buttons."""
        if not self._color_buttons:
            return
        palette = theme_palette(self.theme.mode, self.theme.accent, self._custom_colors)
        self._settings_updating = True
        try:
            for alias, button in self._color_buttons.items():
                var = f"--omnify-{alias}"
                colour = palette.get(var, "#000000")
                rgba = Gdk.RGBA()
                if rgba.parse(colour):
                    button.set_rgba(rgba)
        finally:
            self._settings_updating = False

    def _on_color_button_set(self, button, alias: str) -> None:
        if self._settings_updating:
            return
        rgba = button.get_rgba()
        colour = "#{:02x}{:02x}{:02x}".format(
            round(max(0.0, min(1.0, rgba.red)) * 255),
            round(max(0.0, min(1.0, rgba.green)) * 255),
            round(max(0.0, min(1.0, rgba.blue)) * 255),
        )
        if valid_hex(colour) is None:
            return
        self._custom_colors[f"--omnify-{alias}"] = colour
        self._apply_theme_customization()

    def _on_reset_custom_colours(self, _button) -> None:
        self._custom_colors = {}
        self._apply_theme_customization()
        self._refresh_color_buttons()

    def _on_layout_controls_changed(self, _widget) -> None:
        if self._settings_updating:
            return
        self._apply_theme_customization()

    def _on_sidebar_layout_changed(self, _widget) -> None:
        if self._settings_updating:
            return
        side = "right" if self._sidebar_combo is not None and self._sidebar_combo.get_active() == 1 else "left"
        width = int(self._sidebar_width_spin.get_value()) if self._sidebar_width_spin is not None else 220
        self._sidebar_side = side
        self._sidebar_width = width
        if self.ctx is not None:
            self.ctx.settings.set("sidebar_side", side)
            self.ctx.settings.set("sidebar_width", width)
        self._apply_sidebar_layout()

    def _apply_theme_customization(self) -> None:
        if self.ctx is None or self.theme is None:
            return
        options = {
            "font_family": self._font_entry.get_text().strip() if hasattr(self, "_font_entry") else "",
            "radius": int(self._radius_spin.get_value()) if hasattr(self, "_radius_spin") else 8,
            "background_image": str(self._custom_options.get("background_image", "") or ""),
        }
        self._custom_options = options
        self.ctx.settings.set("theme_custom", self._custom_colors)
        self.ctx.settings.set("theme_font", options["font_family"])
        self.ctx.settings.set("theme_radius", options["radius"])
        self.ctx.settings.set("theme_background", options["background_image"])
        self.theme.set_custom(self._custom_colors, options)
        self._queue_all_redraws()

    def _on_choose_background(self, _button) -> None:
        dialog = Gtk.FileChooserNative(
            title=i18n.tr("Choose a background image"),
            action=Gtk.FileChooserAction.OPEN,
        )
        filter_images = Gtk.FileFilter()
        filter_images.set_name(i18n.tr("Image files"))
        for mime in ("image/png", "image/jpeg", "image/webp", "image/svg+xml"):
            filter_images.add_mime_type(mime)
        dialog.add_filter(filter_images)

        def chosen(_dialog, response, native) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None:
                return
            path = file.get_path()
            if not path:
                return
            self._custom_options["background_image"] = path
            self._refresh_background_label()
            self._apply_theme_customization()

        dialog.connect("response", chosen, dialog)
        dialog.show()

    def _on_clear_background(self, _button) -> None:
        self._custom_options["background_image"] = ""
        self._refresh_background_label()
        self._apply_theme_customization()

    def _theme_export_payload(self) -> Dict[str, Any]:
        return {
            "kind": "omnify-theme",
            "version": 1,
            "mode": self.theme.mode if self.theme else "dark",
            "accent": self.theme.accent if self.theme else "blue",
            "custom": dict(self._custom_colors),
            "options": {
                "font_family": self._custom_options.get("font_family", ""),
                "radius": self._custom_options.get("radius", 8),
                "background_image": self._custom_options.get("background_image", ""),
            },
            "sidebar": {
                "position": self._sidebar_side,
                "width": self._sidebar_width,
            },
        }

    def _on_export_theme(self, _button) -> None:
        dialog = Gtk.FileChooserNative(
            title=i18n.tr("Export theme"),
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.set_current_name("omnify-theme.json")

        def saved(_dialog, response, native) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None:
                return
            path = file.get_path()
            if not path:
                return
            try:
                Path(path).write_text(
                    json.dumps(self._theme_export_payload(), ensure_ascii=False, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                self._notify(i18n.tr("Could not export the theme: {error}", error=exc))
                return
            self._notify(i18n.tr("Theme exported to {path}", path=path))

        dialog.connect("response", saved, dialog)
        dialog.show()

    def _on_import_theme(self, _button) -> None:
        dialog = Gtk.FileChooserNative(
            title=i18n.tr("Import theme"),
            action=Gtk.FileChooserAction.OPEN,
        )

        def opened(_dialog, response, native) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None:
                return
            path = file.get_path()
            if not path:
                return
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._notify(i18n.tr("Could not read the theme: {error}", error=exc))
                return
            if not isinstance(data, dict) or data.get("kind") != "omnify-theme":
                self._notify(i18n.tr("That file is not an Omnify theme"))
                return
            self._apply_imported_theme(data)

        dialog.connect("response", opened, dialog)
        dialog.show()

    def _apply_imported_theme(self, data: Dict[str, Any]) -> None:
        mode = str(data.get("mode", "dark"))
        accent = str(data.get("accent", "blue"))
        self._custom_colors = normalize_custom(data.get("custom", {}))
        options = data.get("options", {}) if isinstance(data.get("options"), dict) else {}
        self._custom_options = {
            "font_family": str(options.get("font_family", "") or ""),
            "radius": int(options.get("radius", 8) or 8),
            "background_image": str(options.get("background_image", "") or ""),
        }
        sidebar = data.get("sidebar", {}) if isinstance(data.get("sidebar"), dict) else {}
        self._sidebar_side = "right" if sidebar.get("position") == "right" else "left"
        try:
            self._sidebar_width = max(160, min(400, int(sidebar.get("width", 220))))
        except (TypeError, ValueError):
            self._sidebar_width = 220
        if self.ctx is not None and self.theme is not None:
            self.ctx.settings.set("theme_mode", mode)
            self.ctx.settings.set("theme_accent", accent)
            self.ctx.settings.set("theme_custom", self._custom_colors)
            self.theme.set(mode, accent)
            self.theme.set_custom(self._custom_colors, self._custom_options)
            self.ctx.settings.set("sidebar_side", self._sidebar_side)
            self.ctx.settings.set("sidebar_width", self._sidebar_width)
        if hasattr(self, "_font_entry"):
            self._font_entry.set_text(str(self._custom_options.get("font_family", "")))
        if hasattr(self, "_radius_spin"):
            self._radius_spin.set_value(int(self._custom_options.get("radius", 8)))
        if self._sidebar_combo is not None:
            self._settings_updating = True
            self._sidebar_combo.set_active(1 if self._sidebar_side == "right" else 0)
            self._settings_updating = False
        if self._sidebar_width_spin is not None:
            self._sidebar_width_spin.set_value(self._sidebar_width)
        self._refresh_background_label()
        self._refresh_color_buttons()
        self._refresh_settings_combos()
        self._apply_sidebar_layout()
        self._queue_all_redraws()
        self._notify(i18n.tr("Theme imported"))

    def _on_open_data_folder(self, _button) -> None:
        if self.ctx is None:
            return
        try:
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(self.ctx.storage.path.as_uri(), None)
        except Exception as exc:
            self._notify(i18n.tr("Could not open the data folder: {error}", error=exc))

    def _on_clear_all_data(self, _button) -> None:
        if self.ctx is None:
            return
        dialog = Gtk.AlertDialog()
        dialog.set_message(i18n.tr("Clear all data?"))
        dialog.set_detail(
            i18n.tr(
                "This deletes calendars, timers, history and settings. It cannot be undone."
            )
        )
        dialog.set_buttons([i18n.tr("Cancel"), i18n.tr("Delete everything")])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.set_modal(True)
        try:
            response = dialog.choose(self._window)
        except Exception:
            return
        if response != 1:
            return
        self.ctx.storage.clear_all()
        self._notify(i18n.tr("All local data deleted"))

    @staticmethod
    def _settings_combo() -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        combo.set_size_request(220, -1)
        return combo

    def _refresh_settings_combos(self) -> None:
        """Repopulate the settings selectors in the active language."""
        if self._language_combo is None or self.ctx is None:
            return
        self._settings_updating = True
        try:
            languages = i18n.available_languages()
            self._language_codes = [code for code, _name in languages]
            self._language_combo.remove_all()
            for _code, name in languages:
                self._language_combo.append_text(name)
            active = i18n.current_language()
            index = self._language_codes.index(active) if active in self._language_codes else 0
            self._language_combo.set_active(index)

            modes = [("dark", i18n.tr("Dark")), ("light", i18n.tr("Light"))]
            self._theme_codes = [code for code, _label in modes]
            self._theme_combo.remove_all()
            for _code, label in modes:
                self._theme_combo.append_text(label)
            mode = self.ctx.settings.get("theme_mode", "dark")
            self._theme_combo.set_active(
                self._theme_codes.index(mode) if mode in self._theme_codes else 0
            )

            accents = [
                ("blue", i18n.tr("Blue")),
                ("purple", i18n.tr("Purple")),
                ("green", i18n.tr("Green")),
                ("orange", i18n.tr("Orange")),
                ("pink", i18n.tr("Pink")),
                ("teal", i18n.tr("Teal")),
            ]
            self._accent_codes = [code for code, _label in accents]
            self._accent_combo.remove_all()
            for _code, label in accents:
                self._accent_combo.append_text(label)
            accent = self.ctx.settings.get("theme_accent", "blue")
            self._accent_combo.set_active(
                self._accent_codes.index(accent) if accent in self._accent_codes else 0
            )
        finally:
            self._settings_updating = False

    def _on_theme_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._settings_updating or self.ctx is None or self.theme is None:
            return
        index = combo.get_active()
        if 0 <= index < len(getattr(self, "_theme_codes", [])):
            mode = self._theme_codes[index]
            self.ctx.settings.set("theme_mode", mode)
            self.theme.set_mode(mode)
            self._refresh_color_buttons()
            self._queue_all_redraws()

    def _on_accent_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._settings_updating or self.ctx is None or self.theme is None:
            return
        index = combo.get_active()
        if 0 <= index < len(getattr(self, "_accent_codes", [])):
            accent = self._accent_codes[index]
            self.ctx.settings.set("theme_accent", accent)
            self.theme.set_accent(accent)
            self._refresh_color_buttons()
            self._queue_all_redraws()

    def _on_language_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._settings_updating or self.ctx is None:
            return
        index = combo.get_active()
        if not (0 <= index < len(getattr(self, "_language_codes", []))):
            return
        code = self._language_codes[index]
        if code == i18n.current_language():
            return
        self.ctx.settings.set("language", code)
        i18n.set_language(code)
        self._apply_language()

    def _apply_language(self) -> None:
        """Retranslate every live view after the language selector changes."""
        if self._window is None:
            return
        self._refresh_settings_combos()
        i18n.translate_widget_tree(self._window)
        for module in self._modules.values():
            module.retranslate()
        if self._mini_window is not None:
            i18n.translate_widget_tree(self._mini_window)
        self._notify("Language changed")

    def _queue_all_redraws(self) -> None:
        for module in self._modules.values():
            view = getattr(module, "_view", None)
            if view is None:
                continue
            for attr in ("_graph_area", "_line_graph", "_chart"):
                widget = getattr(module, attr, None)
                if widget is not None and hasattr(widget, "queue_draw"):
                    widget.queue_draw()
        if self._window is not None:
            self._window.queue_draw()

    # ------------------------------------------------------------------
    # Navigation and callbacks
    # ------------------------------------------------------------------
    def _on_nav_toggled(self, button: Gtk.ToggleButton, module_id: str) -> None:
        # ToggleButton fires "toggled" for both press and release; only act
        # when a button becomes active (radio semantics do the rest).
        if button.get_active():
            self._switch_to(module_id)

    def _on_settings_clicked(self, _button: Gtk.Button) -> None:
        self._switch_to("settings")

    def _clear_nav_selection(self) -> None:
        """Leave Settings without a stale module highlighted in the sidebar."""
        for button in self._nav_buttons.values():
            if button.get_active():
                button.set_active(False)

    def _on_about_clicked(self, _button: Gtk.Button) -> None:
        dialog = Gtk.AboutDialog(transient_for=self._window, modal=True)
        dialog.set_program_name("Omnify")
        dialog.set_version(__version__)
        dialog.set_comments(
            i18n.tr("A modular 'everything in one' desktop toolbox for Linux.")
        )
        dialog.present()

    def _switch_to(self, module_id: str) -> None:
        if module_id == "settings":
            self._clear_nav_selection()
        if self._current_id == module_id:
            return
        if self._current_id is not None:
            previous = self._modules.get(self._current_id)
            if previous is not None:
                previous.on_hide()
        self._current_id = module_id
        if module_id != "settings":
            nav_button = self._nav_buttons.get(module_id)
            if nav_button is not None and not nav_button.get_active():
                nav_button.set_active(True)
        assert self._stack is not None  # set in _build_window
        self._stack.set_visible_child_name(module_id)
        module = self._modules.get(module_id)
        if module is not None:
            module.on_show()
            module.retranslate()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------
    def _notify(self, message: str) -> None:
        """Status-bar sink for ``ModuleContext.notify`` (auto-clears)."""
        if self._window is None or self._status_label is None:
            return
        message = i18n.tr(message)
        if self._status_timeout_id is not None:
            GLib.source_remove(self._status_timeout_id)
            self._status_timeout_id = None
        self._status_label.set_label(message)
        self._status_timeout_id = GLib.timeout_add(_STATUS_CLEAR_MS, self._clear_status)

    def _clear_status(self) -> bool:
        if self._status_label is not None:
            self._status_label.set_label("")
        self._status_timeout_id = None
        return False
