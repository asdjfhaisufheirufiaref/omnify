"""System tray icon via the freedesktop StatusNotifierItem D-Bus spec.

This GTK/GLib build has no ``Gtk.StatusIcon`` and no working way to
*receive* D-Bus method calls through ``gi``, so the tray is implemented
on top of :class:`omnify.core.dbus_min.DbusEndpoint` — a pure-stdlib
session-bus connection that speaks the D-Bus wire protocol directly
(SASL, ``Hello``, name ownership, method calls, replies, signals).
Everything else is stdlib (plus pycairo for the pixel icon), and the
endpoint is injectable, so the core is unit-testable headlessly (see
``tests/test_sni.py``). When no status-notifier watcher is running,
:meth:`StatusNotifierItem.start` returns ``False`` and the caller keeps
the floating window as its only background surface.

The property set follows the KDE-flavoured SNI interface (the same one
Plasma's host consumes, cf.
``/usr/share/dbus-1/interfaces/kf6_org.kde.StatusNotifierItem.xml``):
``IconName`` (a theme icon, preferred by the tray), ``IconPixmap``
(``a(iiay)`` — width/height/ARGB data), the ``ToolTip`` struct
``(sa(iiay)ss)`` and friends. Both the spec's own ``GetProperty`` /
``GetAll`` methods and the standard ``org.freedesktop.DBus.Properties``
interface are exported, so any SNI host can read the item.

The SNI ``Title`` property carries the time text (e.g.
``"⏱ 00:04:59"``) next to the icon — the standard way trays show text,
subject to the user's tray settings; the same text goes into the tooltip
subtitle, so the time is always visible on hover.

Threading: method calls arrive on the endpoint's reader thread. The
``on_activate`` / ``on_menu`` callbacks therefore run off the main
thread and must hop there before touching widgets (the shell does this
with ``GLib.idle_add``).
"""

from __future__ import annotations

import math
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from .dbus_min import DbusEndpoint, DbusMethodError
from .logfile import log

#: The watcher interface every tray (Plasma, ...) exposes on the bus.
WATCHER_BUS_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_OBJECT_PATH = "/StatusNotifierWatcher"
WATCHER_INTERFACE = "org.kde.StatusNotifierWatcher"

#: The object path and interface of our exported item (fixed by the spec).
SNI_OBJECT_PATH = "/StatusNotifierItem"
SNI_INTERFACE = "org.kde.StatusNotifierItem"

#: The standard property-broker interface, exported on the item too.
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

#: D-Bus signatures of every exported property (KDE-flavoured SNI).
_PROPERTY_SIGNATURES: Dict[str, str] = {
    "Id": "s",
    "Category": "s",
    "Status": "s",
    "Title": "s",
    "Description": "s",
    "WindowId": "i",
    "IconName": "s",
    "IconThemePath": "s",
    "IconPixmap": "a(iiay)",
    "ItemIsMenu": "b",
    "Menu": "o",
    "OverlayIconName": "s",
    "OverlayIconPixmap": "a(iiay)",
    "AttentionIconName": "s",
    "AttentionIconPixmap": "a(iiay)",
    "AttentionMovieName": "s",
    "ToolTip": "(sa(iiay)ss)",
}

#: In-argument signatures of the methods we serve (SNI interface).
_SNI_METHOD_SIGS: Dict[str, str] = {
    "Activate": "(ii)",
    "SecondaryActivate": "(ii)",
    "ContextMenu": "(ii)",
    "Scroll": "(is)",
    "GetProperty": "s",
}

#: In-argument signatures of the property-broker methods.
_PROPS_METHOD_SIGS: Dict[str, str] = {
    "Get": "(ss)",
    "GetAll": "s",
    "Set": "(ssv)",
}


def render_clock_icon(
    size: int, rgb: Tuple[float, float, float]
) -> bytes:
    """Render a simple clock face into SNI pixel data.

    Returns the raw 32-bit ARGB bytes (alpha first, network byte order —
    the format the SNI spec requires) for a *size* × *size* image: a
    transparent background with a clock outline and two hands drawn in
    *rgb* (each channel 0..1). Pure function, no GTK; only pycairo.
    """
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    r, g, b = (max(0.0, min(1.0, c)) for c in rgb)
    ctx.set_source_rgb(r, g, b)
    center = size / 2.0
    radius = size * 0.38
    width = max(1.0, size * 0.08)
    ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    # clock face
    ctx.arc(center, center, radius, 0.0, 2.0 * math.pi)
    ctx.stroke()
    # minute hand: straight up; hour hand: towards ~2 o'clock
    for angle, length in ((-math.pi / 2.0, radius * 0.72), (math.pi / 6.0, radius * 0.48)):
        ctx.move_to(center, center)
        ctx.line_to(
            center + length * math.cos(angle),
            center + length * math.sin(angle),
        )
        ctx.stroke()
    # centre dot
    ctx.arc(center, center, width * 0.6, 0.0, 2.0 * math.pi)
    ctx.fill()
    surface.flush()

    # cairo keeps FORMAT_ARGB32 as native-endian BGRA; the SNI spec wants
    # network-order ARGB (alpha first) — swap the channels per pixel.
    data = surface.get_data()
    count = size * size
    out = bytearray(count * 4)
    for i in range(count):
        src = i * 4
        b_, g_, r_, a_ = (
            data[src],
            data[src + 1],
            data[src + 2],
            data[src + 3],
        )
        dst = i * 4
        out[dst] = a_
        out[dst + 1] = r_
        out[dst + 2] = g_
        out[dst + 3] = b_
    return bytes(out)


class StatusNotifierItem:
    """A minimal StatusNotifierItem registered on the session bus.

    Parameters
    ----------
    endpoint:
        A :class:`~omnify.core.dbus_min.DbusEndpoint` (or a stand-in
        with the same methods for tests). ``None`` creates and owns a
        default endpoint connected to the session bus.
    item_id / title:
        The SNI ``Id`` and initial ``Title`` (the tray text).
    icon_name:
        A theme icon name (preferred by the tray over the pixel data).
    on_activate / on_menu:
        ``Callable[[int, int], None]`` for left clicks / right clicks.
        Invoked on the endpoint's reader thread — hop to the main
        thread before touching widgets.
    """

    def __init__(
        self,
        endpoint: Optional[DbusEndpoint] = None,
        *,
        item_id: str = "omnify",
        title: str = "Omnify",
        icon_name: str = "chronometer-symbolic",
        on_activate: Optional[Callable[[int, int], None]] = None,
        on_menu: Optional[Callable[[int, int], None]] = None,
        icon_rgb: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        icon_size: int = 22,
    ) -> None:
        self._endpoint = endpoint
        self._owns_endpoint = endpoint is None
        self._item_id = item_id
        self._title = title
        self._status = "Passive"
        self._icon_pixmap: bytes = b""
        self._icon_name = icon_name
        self._icon_rgb = icon_rgb
        self._icon_size = icon_size
        self._tooltip_subtitle: str = ""
        self.on_activate = on_activate
        self.on_menu = on_menu
        self._service_name = ""
        self._active = False
        self.refresh_icon()

    # -- lifecycle ----------------------------------------------------------
    @property
    def active(self) -> bool:
        """``True`` while the item is registered with a tray."""
        return self._active

    @property
    def service_name(self) -> str:
        """The bus name owned while active (empty otherwise)."""
        return self._service_name

    def start(self) -> bool:
        """Register with the tray; ``True`` on success, never raises.

        Fails (gracefully) when the session bus is unreachable, no
        StatusNotifierWatcher answers, the name cannot be bound, or the
        watcher refuses the item.
        """
        try:
            if self._endpoint is None:
                self._endpoint = DbusEndpoint()
                self._owns_endpoint = True
            if not getattr(self._endpoint, "unique_name", ""):
                self._endpoint.connect(timeout=3.0)
            if not self._watcher_present():
                log("tray: no StatusNotifierWatcher on the bus")
                return False
            # A well-known name element may not start with a digit, so
            # the pid goes behind a dash (the KDE-style convention).
            self._service_name = f"org.omnify.StatusNotifierItem-{os.getpid()}"
            if not self._endpoint.request_name(
                self._service_name, timeout=3.0
            ):
                self._service_name = ""
                log(f"tray: could not bind bus name {self._service_name}")
                return False
            self._install_handlers()
            accepted = self._register_with_watcher()
        except Exception as exc:
            # The app keeps running without a tray, so a failure here is
            # otherwise invisible — record it (this is how a protocol
            # mismatch in the bus handshake would otherwise hide itself).
            log(f"tray: SNI start failed: {exc!r}")
            accepted = False
        if not accepted:
            self._teardown()
            return False
        self._active = True
        self._status = "Active"
        return True

    def _teardown(self) -> None:
        """Release the bound name after a failed :meth:`start`."""
        if self._endpoint is not None and self._service_name:
            try:
                self._endpoint.release_name(self._service_name)
            except Exception:
                pass
        self._service_name = ""
        self._active = False

    def _watcher_present(self) -> bool:
        """``True`` when a StatusNotifierWatcher answers on the bus.

        Prefers the watcher's own ``IsStatusNotifierHostRegistered``
        property; falls back to a plain name-ownership check when the
        property (or the whole call) is missing.
        """
        ep = self._endpoint
        try:
            (value,) = ep.call(
                WATCHER_BUS_NAME,
                WATCHER_OBJECT_PATH,
                PROPERTIES_INTERFACE,
                "Get",
                "(ss)",
                (WATCHER_INTERFACE, "IsStatusNotifierHostRegistered"),
                timeout=2.0,
            )
            return bool(value[1])
        except Exception:
            pass
        try:
            ep.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                "(s)",
                (WATCHER_BUS_NAME,),
                timeout=2.0,
            )
            return True
        except Exception:
            return False

    def _install_handlers(self) -> None:
        self._endpoint.handle(
            SNI_OBJECT_PATH,
            SNI_INTERFACE,
            self._dispatch_sni,
            method_sigs=_SNI_METHOD_SIGS,
        )
        self._endpoint.handle(
            SNI_OBJECT_PATH,
            PROPERTIES_INTERFACE,
            self._dispatch_props,
            method_sigs=_PROPS_METHOD_SIGS,
        )

    def _register_with_watcher(self) -> bool:
        reply = self._endpoint.call(
            WATCHER_BUS_NAME,
            WATCHER_OBJECT_PATH,
            WATCHER_INTERFACE,
            "RegisterStatusNotifierItem",
            "(s)",
            (self._service_name,),
            timeout=5.0,
        )
        if reply and isinstance(reply[0], bool):
            return bool(reply[0])
        return True

    def stop(self) -> None:
        """Unregister from the tray and release the bus name (never raises)."""
        if self._service_name and self._endpoint is not None:
            try:
                self._endpoint.call(
                    WATCHER_BUS_NAME,
                    WATCHER_OBJECT_PATH,
                    WATCHER_INTERFACE,
                    "UnregisterStatusNotifierItem",
                    "(s)",
                    (self._service_name,),
                    timeout=3.0,
                )
            except Exception:
                pass
            try:
                self._endpoint.release_name(self._service_name)
            except Exception:
                pass
        self._service_name = ""
        self._active = False
        self._status = "Passive"

    def shutdown(self) -> None:
        """:meth:`stop`, plus closing the endpoint when this item owns it."""
        self.stop()
        if self._owns_endpoint and self._endpoint is not None:
            try:
                self._endpoint.close()
            except Exception:
                pass
            self._endpoint = None

    # -- property updates -----------------------------------------------------
    def update_title(self, title: str) -> bool:
        """Set the tray text; returns ``True`` when it actually changed.

        The same text becomes the tooltip subtitle, so the running time
        is visible on hover even when the tray shows no text.
        """
        if not self._active or title == self._title:
            return False
        self._title = title
        self._tooltip_subtitle = title
        self._emit("NewTitle")
        self._emit("NewToolTip")
        return True

    def update_tooltip(self, subtitle: str) -> bool:
        """Set the tooltip subtitle line (e.g. the running timers)."""
        if not self._active or subtitle == self._tooltip_subtitle:
            return False
        self._tooltip_subtitle = subtitle
        self._emit("NewToolTip")
        return True

    def update_status(self, status: str) -> bool:
        """Set the SNI status (``Active`` / ``Passive`` / ``NeedsAttention``)."""
        if not self._active or status == self._status:
            return False
        self._status = status
        self._emit("NewStatus", status)
        return True

    def set_icon_rgb(self, rgb: Tuple[float, float, float]) -> bool:
        """Re-render the clock icon in a new colour; returns on change."""
        if rgb == self._icon_rgb:
            return False
        self._icon_rgb = rgb
        self.refresh_icon()
        return True

    def refresh_icon(self) -> None:
        """Rebuild the pixel icon and (if active) emit ``NewIcon``."""
        try:
            self._icon_pixmap = render_clock_icon(
                self._icon_size, self._icon_rgb
            )
        except Exception:
            self._icon_pixmap = b""
        if self._active:
            self._emit("NewIcon")

    def _emit(self, signal: str, param: Optional[str] = None) -> None:
        if self._endpoint is None:
            return
        try:
            if param is None:
                self._endpoint.emit_signal(
                    SNI_OBJECT_PATH, SNI_INTERFACE, signal
                )
            else:
                self._endpoint.emit_signal(
                    SNI_OBJECT_PATH, SNI_INTERFACE, signal, "s", (param,)
                )
        except Exception:
            pass

    # -- D-Bus dispatch -------------------------------------------------------
    def _dispatch_sni(
        self, member: str, args: Tuple[Any, ...]
    ) -> Optional[Tuple[str, Any]]:
        """SNI-interface methods (called on the endpoint's reader thread)."""
        if member in ("Activate", "SecondaryActivate"):
            if self.on_activate is not None:
                self.on_activate(int(args[0]), int(args[1]))
            return None
        if member == "ContextMenu":
            if self.on_menu is not None:
                self.on_menu(int(args[0]), int(args[1]))
            return None
        if member == "GetProperty":
            return "v", self._property_variant(args[0])
        if member == "GetAll":
            return "a{sv}", self._variant_map()
        # Scroll / Show / Hide: accepted, no reply payload.
        return None

    def _dispatch_props(
        self, member: str, args: Tuple[Any, ...]
    ) -> Optional[Tuple[str, Any]]:
        """``org.freedesktop.DBus.Properties`` on the item (reader thread)."""
        if member == "Get":
            _iface, name = args
            return "v", self._property_variant(name)
        if member == "GetAll":
            return "a{sv}", self._variant_map()
        # Set: every property is read-only; accept as a no-op.
        return None

    def _property_variant(self, name: str) -> Tuple[str, Any]:
        sig = _PROPERTY_SIGNATURES.get(name)
        if sig is None:
            raise DbusMethodError(
                "org.freedesktop.DBus.Properties.Error.UnknownProperty",
                name,
            )
        return sig, self._props()[name]

    def _variant_map(self) -> Dict[str, Tuple[str, Any]]:
        props = self._props()
        return {
            name: (sig, props[name])
            for name, sig in _PROPERTY_SIGNATURES.items()
        }

    # -- property values -------------------------------------------------------
    def _pixmap_structs(self) -> List[Tuple[int, int, bytes]]:
        """``a(iiay)`` contents: a list of (width, height, ARGB data)."""
        if not self._icon_pixmap:
            return []
        return [(self._icon_size, self._icon_size, self._icon_pixmap)]

    def _props(self) -> Dict[str, object]:
        return {
            "Id": self._item_id,
            "Category": "Application",
            "Status": self._status,
            "Title": self._title,
            "Description": "Omnify — timers, stopwatch and tools",
            "WindowId": 0,
            "IconName": self._icon_name,
            "IconThemePath": "",
            "IconPixmap": self._pixmap_structs(),
            "ItemIsMenu": False,
            "Menu": "/",
            "OverlayIconName": "",
            "OverlayIconPixmap": [],
            "AttentionIconName": "",
            "AttentionIconPixmap": [],
            "AttentionMovieName": "",
            "ToolTip": (
                self._icon_name,
                [],
                "Omnify",
                self._tooltip_subtitle,
            ),
        }
