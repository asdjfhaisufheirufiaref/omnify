"""Global input monitoring over raw evdev (stdlib only).

The "activate after N clicks / N key presses" triggers must count input
*anywhere on the desktop* — not just inside the Omnify window. On a
Wayland session there is no compositor API for that, but the kernel
exposes every input device as a plain character file under
``/dev/input``; reading them (``open``/``read`` on a 24-byte
``input_event`` format, no third-party package) works as long as the
user can read those files — membership in the ``input`` group, which is
the default on most desktop distributions.

:class:`InputMonitor` opens every readable ``event*`` device, reads it
in a daemon thread and reports:

* clicks of the left mouse button anywhere
  (:meth:`InputMonitor.subscribe_click`);
* key presses rendered as a combo string like ``"a"`` or
  ``"ctrl+shift+s"`` (:meth:`InputMonitor.subscribe_key_combo` — the
  modifier state is tracked from the same event stream, so subscribers
  receive ready-made combos they can compare with a recorded one).

Callback discipline: subscribers run on the monitor's own thread, never
on the GTK main loop. A subscriber that touches widgets must hop to the
main thread itself (``GLib.idle_add`` is thread-safe for that).

When no device can be opened (missing permissions, no ``/dev/input``)
the monitor degrades gracefully: :meth:`start` returns ``False``,
:attr:`InputMonitor.error` explains why, and subscribers are simply
never called — the time-based ("at") triggers are unaffected.

The keycode→name table renders names exactly as
``Gdk.keyval_name(...)`` does (lower-cased), so a combo recorded inside
the Omnify window matches the same physical key pressed anywhere else.
"""

from __future__ import annotations

import os
import select
import struct
import threading
from typing import Callable, Dict, List, Optional, Set

from .logfile import log

#: One ``input_event``: ``struct timeval`` (two longs), ``u16 type``,
#: ``u16 code``, ``s32 value`` — 24 bytes on 64-bit Linux.
_EVENT_FORMAT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)

#: input_event types we care about.
EV_KEY = 1

#: Keycodes at and above this value are buttons (``BTN_*``); below it
#: they are real keys.
_FIRST_BUTTON = 0x100
BTN_LEFT = 0x110

#: Modifier keycodes (left/right variants).
_MODIFIER_CODES: Dict[int, str] = {
    29: "ctrl",   # KEY_LEFTCTRL
    97: "ctrl",   # KEY_RIGHTCTRL
    42: "shift",  # KEY_LEFTSHIFT
    54: "shift",  # KEY_RIGHTSHIFT
    56: "alt",    # KEY_LEFTALT
    98: "alt",    # KEY_MENU (context-menu key, often the right Alt)
    100: "alt",   # KEY_RIGHTALT
}

#: Fixed names for the non-alphabetic keycodes we map. Values match the
#: lower-cased output of ``Gdk.keyval_name``.
_FIXED_KEY_NAMES: Dict[int, str] = {
    1: "escape",
    12: "minus",
    13: "equal",
    14: "backspace",
    15: "tab",
    26: "bracket_left",
    27: "bracket_right",
    28: "return",
    39: "semicolon",
    40: "apostrophe",
    41: "grave",
    43: "backslash",
    51: "comma",
    52: "period",
    53: "slash",
    55: "asterisk",
    57: "space",
    76: "insert",
    102: "home",
    103: "up",
    104: "down",
    105: "left",
    106: "right",
    107: "end",
    108: "page_up",
    109: "page_down",
    111: "delete",
}


#: The letter rows of a QWERTY layout are *not* contiguous in the
#: alphabet (q-w-e..., a-s-d..., z-x-c...), so they get an explicit
#: linux-code → keyval-name table. Values match the lower-cased output
#: of ``Gdk.keyval_name``.
_LETTER_KEY_NAMES: Dict[int, str] = {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5",
    7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    16: "q", 17: "w", 18: "e", 19: "r", 20: "t",
    21: "y", 22: "u", 23: "i", 24: "o", 25: "p",
    30: "a", 31: "s", 32: "d", 33: "f", 34: "g",
    35: "h", 36: "j", 37: "k", 38: "l",
    44: "z", 45: "x", 46: "c", 47: "v", 48: "b",
    49: "n", 50: "m",
    59: "f1", 60: "f2", 61: "f3", 62: "f4", 63: "f5",
    64: "f6", 65: "f7", 66: "f8", 67: "f9", 68: "f10",
    87: "f11", 88: "f12", 89: "f13", 90: "f14", 91: "f15",
    92: "f16", 93: "f17", 94: "f18", 95: "f19", 96: "f20",
}


def key_name(code: int) -> Optional[str]:
    """The lower-cased GTK keyval name for a linux *code*, or ``None``.

    Letters, digits, F1–F20 and the common punctuation/navigation keys
    are mapped (the realistic set of trigger keys); anything else
    returns ``None`` and is simply not reported.
    """
    if code in _LETTER_KEY_NAMES:
        return _LETTER_KEY_NAMES[code]
    return _FIXED_KEY_NAMES.get(code)


def combo_for(key: str, pressed_modifiers: Set[int]) -> str:
    """Render *key* plus held modifiers as ``"ctrl+shift+<key>"``.

    Modifier order (ctrl, shift, alt) matches the in-app key recorder,
    so both sides of a comparison produce the same string for the same
    physical key press.
    """
    parts: List[str] = []
    for mod in ("ctrl", "shift", "alt"):
        if any(code in pressed_modifiers for code, name in _MODIFIER_CODES.items()
               if name == mod):
            parts.append(mod)
    return "+".join(parts + [key])


class InputMonitor:
    """Reads evdev devices and reports global clicks and key combos.

    ``device_dir`` is the directory containing the ``event*`` devices
    (default ``/dev/input``); ``opener`` is an injectable
    ``path -> fd`` factory (tests pass a fake so no real devices are
    touched). Both are optional; with the defaults the monitor opens
    every readable device in ``/dev/input``.
    """

    def __init__(
        self,
        device_dir: str = "/dev/input",
        opener: Optional[Callable[[str], int]] = None,
    ) -> None:
        self._device_dir = device_dir
        self._opener = opener
        self._click_subscribers: Set[Callable[[], None]] = set()
        self._key_subscribers: Set[Callable[[str], None]] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._fds: List[int] = []
        self._mods: Set[int] = set()
        self.available = False
        self.error = ""

    # -- subscription -----------------------------------------------------
    def subscribe_click(self, callback: Callable[[], None]) -> None:
        """Report every left-button press anywhere (no duplicates)."""
        self._click_subscribers.add(callback)

    def unsubscribe_click(self, callback: Callable[[], None]) -> None:
        self._click_subscribers.discard(callback)

    def subscribe_key_combo(self, callback: Callable[[str], None]) -> None:
        """Report every key press as a combo string (no duplicates)."""
        self._key_subscribers.add(callback)

    def unsubscribe_key_combo(self, callback: Callable[[str], None]) -> None:
        self._key_subscribers.discard(callback)

    # -- lifecycle -----------------------------------------------------------
    @property
    def running(self) -> bool:
        """``True`` while the reader thread is active and devices are open."""
        return self._thread is not None and not self._stop.is_set()

    def start(self) -> bool:
        """Open every readable input device and start the reader thread.

        Idempotent — starting twice is a no-op. Returns ``True`` when at
        least one device was opened; on failure :attr:`error` says why.
        """
        if self._thread is not None:
            return self.available
        self._open_devices()
        if not self._fds:
            self.available = False
            return False
        self.available = True
        self.error = ""
        self._mods = set()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="omnify-input-monitor", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the reader thread and close every device (idempotent)."""
        if self._thread is None:
            return
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        for fd in self._fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = []
        self.available = False

    # -- device discovery ------------------------------------------------------
    def _open_devices(self) -> None:
        try:
            entries = sorted(os.listdir(self._device_dir))
        except OSError as exc:
            self.error = f"cannot read {self._device_dir}: {exc}"
            return
        opened = 0
        for name in entries:
            if not name.startswith("event"):
                continue
            path = os.path.join(self._device_dir, name)
            try:
                if self._opener is not None:
                    fd = self._opener(path)
                else:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self._fds.append(fd)
            opened += 1
        if opened == 0:
            if entries:
                self.error = (
                    "no readable /dev/input devices — add the user to the "
                    "'input' group (sudo usermod -aG input $USER) and log in "
                    "again; time-based triggers keep working"
                )
            else:
                self.error = f"no input devices found in {self._device_dir}"

    # -- reader loop -------------------------------------------------------------
    def _loop(self) -> None:
        """The daemon thread body.

        Nothing here may raise: one bad event or one broken subscriber
        must never kill the reader (a dead monitor is the silent kind of
        failure that takes days to diagnose).
        """
        pending: Dict[int, bytes] = {}
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select(self._fds, [], [], 0.25)
            except (OSError, ValueError):
                break
            for fd in ready:
                try:
                    events = self._read_events(fd, pending)
                except Exception:
                    log("input monitor: read failed on an open device")
                    continue
                for etype, code, value in events:
                    try:
                        self._handle(etype, code, value)
                    except Exception as exc:
                        log(
                            f"input monitor: dispatch failed for "
                            f"({etype}, {code}, {value}): {exc!r}"
                        )

    def _read_events(self, fd: int, pending: Dict[int, bytes]) -> List[tuple]:
        """Read complete input events from *fd* (handles partial reads)."""
        try:
            data = os.read(fd, 4096)
        except (BlockingIOError, InterruptedError):
            return []
        except OSError:
            # The device went away (unplug, permissions) — drop the fd.
            try:
                os.close(fd)
                self._fds.remove(fd)
            except (OSError, ValueError):
                pass
            return []
        if not data:
            return []
        buffer = pending.get(fd, b"") + data
        count = len(buffer) // _EVENT_SIZE
        events: List[tuple] = []
        for i in range(count):
            _sec, _usec, etype, code, value = struct.unpack_from(
                _EVENT_FORMAT, buffer, i * _EVENT_SIZE
            )
            events.append((etype, code, value))
        rest = buffer[count * _EVENT_SIZE:]
        if rest:
            pending[fd] = rest
        else:
            pending.pop(fd, None)
        return events

    # -- event dispatch ----------------------------------------------------------
    def _handle(self, etype: int, code: int, value: int) -> None:
        if etype != EV_KEY:
            return
        if code >= _FIRST_BUTTON:
            # A button; only left-button presses count as "clicks".
            if value == 1 and code == BTN_LEFT and self._click_subscribers:
                self._notify(self._click_subscribers, ())
            return
        if code in _MODIFIER_CODES:
            if value == 1:
                self._mods.add(code)
            elif value == 0:
                self._mods.discard(code)
            return
        if value != 1:
            return  # releases and auto-repeats do not count
        name = key_name(code)
        if name is None or not self._key_subscribers:
            return
        combo = combo_for(name, self._mods)
        self._notify(self._key_subscribers, (combo,))

    @staticmethod
    def _notify(subscribers: Set[Callable], args: tuple) -> None:
        """Call every subscriber; one raising must not block the others
        (and must never reach the reader loop)."""
        for callback in list(subscribers):
            try:
                callback(*args)
            except Exception as exc:
                log(f"input monitor: subscriber failed: {exc!r}")


_default_monitor: Optional[InputMonitor] = None


def get_default_monitor() -> InputMonitor:
    """The process-wide monitor shared by every module (idempotent)."""
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = InputMonitor()
    return _default_monitor
