"""Translate GTK key events into Omnify shortcut strings."""

from __future__ import annotations

from ..core.commands import normalize_combo


def key_event_combo(keyval: int, state: int) -> str:
    """Return a canonical combo such as ``ctrl+shift+p`` or ``""``.

    Modifier-only presses are ignored so a shortcut recorder waits for the
    actual key.  GTK's numeric state mask is translated without importing
    Gdk at module level (the app shell already owns the GTK dependency).
    """
    try:
        from gi.repository import Gdk

        name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
    except Exception:
        return ""
    if not name:
        return ""
    lowered = name.lower()
    if lowered in {
        "control_l",
        "control_r",
        "shift_l",
        "shift_r",
        "alt_l",
        "alt_r",
        "super_l",
        "super_r",
        "meta_l",
        "meta_r",
        "caps_lock",
        "num_lock",
    }:
        return ""
    modifiers = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        modifiers.append("ctrl")
    if state & Gdk.ModifierType.SHIFT_MASK:
        modifiers.append("shift")
    if state & Gdk.ModifierType.ALT_MASK:
        modifiers.append("alt")
    if state & Gdk.ModifierType.SUPER_MASK:
        modifiers.append("super")
    return normalize_combo("+".join(modifiers + [lowered]))
