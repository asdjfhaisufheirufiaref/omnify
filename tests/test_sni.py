"""Headless tests for the StatusNotifierItem core (no live bus needed).

Run with:  python3 tests/test_sni.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.core import sni  # noqa: E402
from omnify.core.dbus_min import DbusError  # noqa: E402


class FakeEndpoint:
    """Stand-in for :class:`DbusEndpoint` with the methods SNI uses."""

    def __init__(
        self,
        has_watcher: bool = True,
        accept: bool = True,
        bind: bool = True,
    ) -> None:
        self.has_watcher = has_watcher
        self.accept = accept
        self.bind = bind
        self.unique_name = ":1.42"
        self.calls = []
        self.emitted = []
        self.handlers = {}
        self.names_bound = []
        self.names_released = []
        self.closed = False

    def connect(self, timeout: float = 3.0) -> None:
        pass

    def call(self, dest, path, interface, member, sig="", args=(), timeout=5.0):
        self.calls.append((dest, path, interface, member, sig, args))
        if member == "Get" and interface == sni.PROPERTIES_INTERFACE:
            if not self.has_watcher:
                raise DbusError(
                    "org.freedesktop.DBus.Error.ServiceUnknown", dest
                )
            return (("b", True),)
        if member == "GetNameOwner":
            if not self.has_watcher:
                raise DbusError(
                    "org.freedesktop.DBus.Error.NameHasNoOwner", dest
                )
            return (":1.5",)
        if member == "RegisterStatusNotifierItem":
            return (self.accept,)
        if member == "UnregisterStatusNotifierItem":
            return ()
        return ()

    def request_name(self, name: str, timeout: float = 3.0) -> bool:
        self.names_bound.append(name)
        return self.bind

    def release_name(self, name: str, timeout: float = 3.0) -> None:
        self.names_released.append(name)

    def handle(self, path, interface, dispatch, method_sigs=None):
        self.handlers[(path, interface)] = (dispatch, method_sigs or {})

    def emit_signal(self, path, interface, member, sig="", args=()):
        self.emitted.append((path, interface, member))

    def close(self) -> None:
        self.closed = True


class TestStatusNotifierItem(unittest.TestCase):
    def make_item(self, endpoint=None, **kwargs):
        endpoint = endpoint or FakeEndpoint()
        item = sni.StatusNotifierItem(endpoint, **kwargs)
        return item, endpoint

    # -- lifecycle ------------------------------------------------------
    def test_start_registers_with_watcher(self):
        item, endpoint = self.make_item()
        self.assertTrue(item.start())
        self.assertTrue(item.active)
        registers = [c for c in endpoint.calls
                     if c[3] == "RegisterStatusNotifierItem"]
        self.assertEqual(len(registers), 1)
        dest, path, iface, method, sig, args = registers[0]
        self.assertEqual(
            (dest, path, iface),
            (sni.WATCHER_BUS_NAME, sni.WATCHER_OBJECT_PATH,
             sni.WATCHER_INTERFACE),
        )
        self.assertEqual(args, (item.service_name,))
        self.assertIn(str(os.getpid()), item.service_name)
        self.assertIn(
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE), endpoint.handlers
        )
        self.assertIn(
            (sni.SNI_OBJECT_PATH, sni.PROPERTIES_INTERFACE),
            endpoint.handlers,
        )
        self.assertEqual(endpoint.names_bound, [item.service_name])
        self.assertEqual(endpoint.names_released, [])

    def test_start_fails_without_watcher(self):
        item, endpoint = self.make_item(endpoint=FakeEndpoint(has_watcher=False))
        self.assertFalse(item.start())
        self.assertFalse(item.active)
        self.assertEqual(
            [c for c in endpoint.calls if c[3] == "RegisterStatusNotifierItem"],
            [],
        )
        self.assertEqual(endpoint.handlers, {})

    def test_start_fails_when_watcher_refuses(self):
        item, endpoint = self.make_item(endpoint=FakeEndpoint(accept=False))
        self.assertFalse(item.start())
        self.assertFalse(item.active)
        self.assertTrue(
            endpoint.names_released
            and endpoint.names_released[0].startswith(
                "org.omnify.StatusNotifierItem-"
            )
        )
        self.assertEqual(item.service_name, "")

    def test_start_fails_when_name_binding_fails(self):
        item, endpoint = self.make_item(endpoint=FakeEndpoint(bind=False))
        self.assertFalse(item.start())
        # the name was never bound, so there is nothing to release
        self.assertEqual(endpoint.names_released, [])
        self.assertEqual(
            [c for c in endpoint.calls if c[3] == "RegisterStatusNotifierItem"],
            [],
        )

    def test_stop_unregisters_and_releases_name(self):
        item, endpoint = self.make_item()
        item.start()
        service = item.service_name
        item.stop()
        self.assertFalse(item.active)
        self.assertEqual(item.service_name, "")
        methods = [call[3] for call in endpoint.calls]
        self.assertIn("UnregisterStatusNotifierItem", methods)
        self.assertEqual(endpoint.names_released, [service])
        # stopping twice is a no-op
        item.stop()
        self.assertEqual(endpoint.names_released, [service])

    # -- property updates --------------------------------------------------
    def test_update_title_emits_signals_once(self):
        item, endpoint = self.make_item()
        item.start()
        self.assertTrue(item.update_title("⏱ 00:05:00"))
        self.assertIn(
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE, "NewTitle"),
            endpoint.emitted,
        )
        self.assertIn(
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE, "NewToolTip"),
            endpoint.emitted,
        )
        # an unchanged title must not re-emit
        before = len(endpoint.emitted)
        self.assertFalse(item.update_title("⏱ 00:05:00"))
        self.assertEqual(len(endpoint.emitted), before)

    def test_update_title_ignored_while_inactive(self):
        item, endpoint = self.make_item()
        self.assertFalse(item.update_title("x"))
        self.assertEqual(endpoint.emitted, [])

    def test_status_and_tooltip_updates(self):
        item, endpoint = self.make_item()
        item.start()
        self.assertTrue(item.update_status("NeedsAttention"))
        self.assertIn(
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE, "NewStatus"),
            endpoint.emitted,
        )
        self.assertTrue(item.update_tooltip("one two"))
        self.assertEqual(item._tooltip_subtitle, "one two")

    # -- D-Bus dispatch --------------------------------------------------------
    def test_activate_and_menu_callbacks(self):
        activated = []
        menued = []
        item, endpoint = self.make_item(
            on_activate=lambda x, y: activated.append((x, y)),
            on_menu=lambda x, y: menued.append((x, y)),
        )
        item.start()
        dispatch, _sigs = endpoint.handlers[
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE)
        ]
        dispatch("Activate", (3, 4))
        dispatch("ContextMenu", (5, 6))
        self.assertEqual(activated, [(3, 4)])
        self.assertEqual(menued, [(5, 6)])

    def test_get_property_method(self):
        item, endpoint = self.make_item()
        item.start()
        dispatch, _sigs = endpoint.handlers[
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE)
        ]
        sig, value = dispatch("GetProperty", ("Status",))
        # the method returns one variant wrapping the property value;
        # start() switches the status from Passive to Active
        self.assertEqual((sig, value), ("v", ("s", "Active")))
        with self.assertRaises(DbusError):
            dispatch("GetProperty", ("Nope",))

    def test_get_all_method(self):
        item, endpoint = self.make_item(title="T")
        item.start()
        dispatch, _sigs = endpoint.handlers[
            (sni.SNI_OBJECT_PATH, sni.PROPERTIES_INTERFACE)
        ]
        sig, props = dispatch("GetAll", (sni.SNI_INTERFACE,))
        self.assertEqual(sig, "a{sv}")
        self.assertEqual(props["Title"], ("s", "T"))
        self.assertEqual(props["Id"], ("s", "omnify"))
        self.assertEqual(props["Category"], ("s", "Application"))
        self.assertEqual(props["IconName"], ("s", "chronometer-symbolic"))
        # KDE-style pixmap list: (width, height, ARGB bytes)
        psig, pixmaps = props["IconPixmap"]
        self.assertEqual(psig, "a(iiay)")
        self.assertEqual(len(pixmaps), 1)
        width, height, raw = pixmaps[0]
        self.assertEqual(width, 22)
        self.assertEqual(height, 22)
        self.assertEqual(len(raw), 22 * 22 * 4)
        # KDE-style tooltip struct: (icon, icon_pixmap, title, subtitle)
        tsig, tooltip = props["ToolTip"]
        self.assertEqual(tsig, "(sa(iiay)ss)")
        self.assertEqual(len(tooltip), 4)
        self.assertEqual(tooltip[2], "Omnify")
        self.assertEqual(tooltip[3], "")
        # the title update must also refresh the tooltip subtitle
        self.assertTrue(item.update_title("T2"))
        _sig, props2 = dispatch("GetAll", (sni.SNI_INTERFACE,))
        self.assertEqual(props2["ToolTip"][1][3], "T2")
        self.assertEqual(props2["Title"], ("s", "T2"))

    def test_set_icon_rgb_renders_and_emits(self):
        item, endpoint = self.make_item()
        item.start()
        self.assertTrue(item.set_icon_rgb((0.1, 0.2, 0.3)))
        self.assertIn(
            (sni.SNI_OBJECT_PATH, sni.SNI_INTERFACE, "NewIcon"),
            endpoint.emitted,
        )
        self.assertFalse(item.set_icon_rgb((0.1, 0.2, 0.3)))


class TestRenderClockIcon(unittest.TestCase):
    def test_shape_and_byte_order(self):
        data = sni.render_clock_icon(22, (1.0, 0.5, 0.0))
        self.assertEqual(len(data), 22 * 22 * 4)
        # corners must be transparent (alpha is the first byte)
        self.assertEqual(data[0], 0)
        self.assertEqual(data[-4], 0)
        # the most opaque pixel must be the requested colour; edge pixels
        # may be semi-transparent because of anti-aliasing
        best = max(range(0, len(data), 4), key=lambda i: data[i])
        alpha, red, green, blue = data[best : best + 4]
        self.assertGreaterEqual(alpha, 128)
        self.assertGreater(red, 200)
        self.assertGreater(green, 100)
        self.assertLess(green, 160)
        self.assertLess(blue, 60)

    def test_other_sizes(self):
        for size in (16, 24, 48):
            data = sni.render_clock_icon(size, (0.0, 1.0, 0.0))
            self.assertEqual(len(data), size * size * 4)
            self.assertTrue(any(data[i] > 0 for i in range(0, len(data), 4)))


if __name__ == "__main__":
    unittest.main()
