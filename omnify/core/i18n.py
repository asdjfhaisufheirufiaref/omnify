"""Small dependency-free translation layer for Omnify.

The English source strings are the keys.  A language is one module in
:mod:`omnify.locales` exposing ``NAME`` and ``TRANSLATIONS``; dropping a new
file there is enough to make it available in Settings.  Nothing here imports
GTK at module level, so the catalogue and the lookup helpers stay testable
without a display.

UI code can call :func:`tr` directly for dynamic strings.  Static widgets are
translated in place by :func:`translate_widget_tree`, which remembers the
English source text on each widget so switching languages back and forth is
idempotent (and dynamic labels can be re-translated on the next pass).
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The language used when no preference has been saved.
DEFAULT_LANGUAGE = "en"

_current_language = DEFAULT_LANGUAGE
_catalogues: Dict[str, Dict[str, str]] = {}
_language_names: Dict[str, str] = {}


def _load_locales() -> None:
    """Discover ``omnify.locales.<code>`` modules and read their catalogues."""
    global _language_names
    try:
        package = importlib.import_module("omnify.locales")
    except Exception:
        return
    names: Dict[str, str] = {}
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"omnify.locales.{info.name}")
        except Exception:
            continue
        translations = getattr(module, "TRANSLATIONS", {})
        if isinstance(translations, dict):
            _catalogues[info.name] = {
                str(key): str(value) for key, value in translations.items()
            }
        names[info.name] = str(getattr(module, "NAME", info.name))
    _language_names = names


_load_locales()


def available_languages() -> List[Tuple[str, str]]:
    """Return ``(code, native_name)`` pairs, English first, then alphabetical."""
    order = sorted(_language_names.items(), key=lambda item: item[0])
    order.sort(key=lambda item: (item[0] != DEFAULT_LANGUAGE, item[1].lower()))
    return order


def language_name(code: str) -> str:
    """Return the native display name for *code* (falls back to the code)."""
    return _language_names.get(str(code), str(code))


def current_language() -> str:
    """Return the active language code."""
    return _current_language


def set_language(code: str) -> str:
    """Activate *code* (falling back to English) and return the active code."""
    global _current_language
    candidate = str(code or DEFAULT_LANGUAGE)
    if candidate not in _language_names:
        candidate = DEFAULT_LANGUAGE
    _current_language = candidate
    return _current_language


def source_for(text: str) -> str:
    """Reverse-map a translation in the active catalogue back to English.

    This lets a widget created while Ukrainian is active, or a label changed
    dynamically to an already-translated value, keep a stable English source.
    """
    if not text:
        return text
    catalogue = _catalogues.get(_current_language, {})
    for source, translated in catalogue.items():
        if translated == text:
            return source
    return text


def tr(text: str, **values: Any) -> str:
    """Translate *text* into the active language.

    Unknown strings are returned unchanged, which keeps the app usable while a
    catalogue is still being filled in.  ``str.format`` placeholders are
    formatted when keyword values are supplied; malformed placeholders never
    crash the UI.
    """
    if not isinstance(text, str) or not text:
        return text
    catalogue = _catalogues.get(_current_language, {})
    translated = catalogue.get(text, text)
    if values:
        try:
            translated = translated.format(**values)
        except (KeyError, IndexError, ValueError):
            pass
    return translated


def translate_widget_tree(root: Any) -> None:
    """Translate labels, buttons, placeholders and tooltips below *root*.

    This is the pragmatic layer for existing module UIs: the English source is
    preserved in a private Python attribute, and a widget whose text was
    changed by application code since the previous pass is treated as a new
    source.  GTK is imported lazily so importing this module stays headless.
    """
    try:
        from gi.repository import Gtk
    except Exception:
        return

    if root is None:
        return

    def update(widget: Any, key: str, getter, setter) -> None:
        try:
            current = getter()
        except Exception:
            return
        if not isinstance(current, str) or not current:
            return
        source_attr = f"_i18n_source_{key}"
        last_attr = f"_i18n_last_{key}"
        stored = getattr(widget, source_attr, None)
        last = getattr(widget, last_attr, None)
        if stored is None or (last is not None and current != last):
            stored = source_for(current)
            setattr(widget, source_attr, stored)
        translated = tr(stored)
        try:
            setter(translated)
        except Exception:
            return
        setattr(widget, last_attr, translated)

    def walk(widget: Any) -> None:
        if widget is None:
            return
        try:
            if isinstance(widget, Gtk.Label):
                if not widget.get_use_markup():
                    update(widget, "label", widget.get_text, widget.set_text)
            elif isinstance(widget, Gtk.Entry):
                update(
                    widget,
                    "placeholder",
                    widget.get_placeholder_text,
                    widget.set_placeholder_text,
                )
            elif isinstance(widget, Gtk.Expander):
                update(widget, "expander", widget.get_label, widget.set_label)
            elif isinstance(widget, Gtk.Button):
                update(widget, "label", widget.get_label, widget.set_label)
            elif isinstance(widget, Gtk.LinkButton):
                update(widget, "label", widget.get_label, widget.set_label)
            if isinstance(widget, Gtk.Widget):
                update(
                    widget,
                    "tooltip",
                    widget.get_tooltip_text,
                    widget.set_tooltip_text,
                )
            if isinstance(widget, Gtk.Stack):
                pages = getattr(widget, "get_pages", None)
                if callable(pages):
                    try:
                        page_list = pages()
                    except Exception:
                        page_list = []
                    for page in page_list or []:
                        update(
                            page,
                            "stack_title",
                            page.get_title,
                            page.set_title,
                        )
            if isinstance(widget, Gtk.Window):
                update(
                    widget,
                    "window_title",
                    widget.get_title,
                    widget.set_title,
                )
        except Exception:
            pass
        try:
            child = widget.get_first_child()
        except Exception:
            child = None
        while child is not None:
            walk(child)
            try:
                child = child.get_next_sibling()
            except Exception:
                child = None
        # Gtk.Expander keeps its content in a separate ``child`` outside the
        # title-area first-child chain; walk it explicitly so Settings →
        # “Custom colours…” is translated too.
        if isinstance(widget, Gtk.Expander):
            try:
                content = widget.get_child()
            except Exception:
                content = None
            if content is not None:
                walk(content)

    walk(root)


def is_rtl(code: Optional[str] = None) -> bool:
    """Placeholder for future right-to-left catalogues."""
    return (code or _current_language) in {"ar", "fa", "he", "ur"}
