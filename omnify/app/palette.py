"""VS Code/Obsidian-style command palette and quick switcher.

The palette is deliberately a thin GTK surface: the shell supplies already
resolved :class:`PaletteItem` objects, so commands, notes and future plugin
providers all share the same search and keyboard behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from ..core.fuzzy import fuzzy_score
from ..core.i18n import tr


@dataclass
class PaletteItem:
    """One row in the palette."""

    title: str
    action: Callable[[], Any]
    subtitle: str = ""
    category: str = ""
    keys: str = ""
    search_extra: str = ""
    item_id: str = ""
    kind: str = "command"

    def searchable(self) -> tuple[str, ...]:
        return (
            self.title,
            self.subtitle,
            self.category,
            self.search_extra,
            self.keys,
        )


class CommandPalette:
    """A reusable keyboard-first palette window.

    ``mode`` is ``"commands"`` (all commands) or ``"quick"`` (notes plus
    commands).  The caller decides which items to expose; the palette only
    ranks and renders them.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._window: Optional[Any] = None
        self._entry: Optional[Any] = None
        self._list: Optional[Any] = None
        self._rows: List[PaletteItem] = []
        self._buttons: List[Any] = []
        self.mode = "commands"

    # -- lifecycle -----------------------------------------------------
    def open(self, mode: str = "commands") -> None:
        from gi.repository import Gtk

        self.mode = "quick" if mode == "quick" else "commands"
        if self._window is None:
            self._window = Gtk.Window(title=tr("Command palette"))
            self._window.set_default_size(760, 520)
            self._window.set_modal(False)
            self._window.set_destroy_with_parent(True)
            if getattr(self._app, "_window", None) is not None:
                self._window.set_transient_for(self._app._window)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            outer.set_margin_top(12)
            outer.set_margin_bottom(12)
            outer.set_margin_start(12)
            outer.set_margin_end(12)

            self._heading = Gtk.Label(label=tr("Command palette"), xalign=0.0)
            self._heading.get_style_context().add_class("omnify-card-title")
            outer.append(self._heading)

            self._entry = Gtk.SearchEntry()
            self._entry.set_placeholder_text(
                tr("Type a command, note or module — ↑/↓ to choose, Enter to run")
            )
            self._entry.connect("changed", self._on_search_changed)
            self._entry.connect("activate", self._on_entry_activate)
            outer.append(self._entry)

            self._hint = Gtk.Label(
                label=tr("Esc closes · commands and actions are searchable"),
                xalign=0.0,
            )
            self._hint.get_style_context().add_class("omnify-dim")
            outer.append(self._hint)

            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            self._list = Gtk.ListBox()
            self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self._list.set_activate_on_single_click(True)
            self._list.connect("row-activated", self._on_row_activated)
            scroller.set_child(self._list)
            outer.append(scroller)
            self._window.set_child(outer)

            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self._on_key_pressed)
            self._window.add_controller(key_controller)
            self._window.connect("close-request", self._on_close_request)

        self._window.set_title(
            tr("Quick switcher") if self.mode == "quick" else tr("Command palette")
        )
        self._heading.set_label(
            tr("Quick switcher") if self.mode == "quick" else tr("Command palette")
        )
        self._hint.set_label(
            tr("Notes and commands · Esc closes")
            if self.mode == "quick"
            else tr("Esc closes · every command is searchable")
        )
        if self._entry is not None:
            self._entry.set_text("")
        self._refresh("")
        self._window.present()
        if self._entry is not None:
            self._entry.grab_focus()

    def close(self) -> None:
        if self._window is not None:
            self._window.hide()

    def _on_close_request(self, _window) -> bool:
        return False

    # -- search --------------------------------------------------------
    def _on_search_changed(self, entry) -> None:
        self._refresh(entry.get_text())

    def _refresh(self, query: str) -> None:
        from gi.repository import Gtk

        if self._list is None:
            return
        while True:
            child = self._list.get_first_child()
            if child is None:
                break
            self._list.remove(child)
        self._buttons = []

        items = list(self._app.palette_items(query, self.mode))
        if query.strip():
            ranked = []
            for item in items:
                score = max(
                    (fuzzy_score(query, value) or 0 for value in item.searchable()),
                    default=0,
                )
                if score:
                    ranked.append((score, item))
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            items = [item for _score, item in ranked]
        self._rows = items[:80]

        if not self._rows:
            empty = Gtk.ListBoxRow()
            empty.set_activatable(False)
            label = Gtk.Label(
                label=tr("Nothing found. Try another word or press Esc."),
                xalign=0.0,
            )
            label.get_style_context().add_class("omnify-dim")
            label.set_margin_top(10)
            label.set_margin_bottom(10)
            label.set_margin_start(10)
            empty.set_child(label)
            self._list.append(empty)
            return

        for index, item in enumerate(self._rows):
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            row.palette_item = item
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            content.set_margin_top(7)
            content.set_margin_bottom(7)
            content.set_margin_start(10)
            content.set_margin_end(10)

            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text.set_hexpand(True)
            title = Gtk.Label(label=item.title, xalign=0.0)
            title.get_style_context().add_class("palette-title")
            text.append(title)
            meta_bits = [bit for bit in (item.category, item.subtitle) if bit]
            if meta_bits:
                meta = Gtk.Label(label=" · ".join(meta_bits), xalign=0.0)
                meta.get_style_context().add_class("omnify-dim")
                text.append(meta)
            content.append(text)

            if item.keys:
                keys = Gtk.Label(label=item.keys, xalign=1.0)
                keys.get_style_context().add_class("palette-keys")
                content.append(keys)
            elif item.kind == "note":
                arrow = Gtk.Label(label="›", xalign=1.0)
                arrow.get_style_context().add_class("omnify-dim")
                content.append(arrow)
            row.set_child(content)
            self._list.append(row)
            self._buttons.append(row)
            if index == 0:
                self._list.select_row(row)

    # -- activation ----------------------------------------------------
    def _selected_item(self) -> Optional[PaletteItem]:
        if self._list is None:
            return None
        row = self._list.get_selected_row()
        if row is not None:
            return getattr(row, "palette_item", None)
        return self._rows[0] if self._rows else None

    def _on_entry_activate(self, _entry) -> None:
        item = self._selected_item()
        if item is not None:
            self._activate(item)

    def _on_row_activated(self, _list, row) -> None:
        item = getattr(row, "palette_item", None)
        if item is not None:
            self._activate(item)

    def _activate(self, item: PaletteItem) -> None:
        from gi.repository import GLib

        self.close()
        GLib.idle_add(self._run_item, item)

    def _run_item(self, item: PaletteItem) -> bool:
        try:
            item.action()
        except Exception as exc:
            from ..core.logfile import log

            log(f"palette action failed: {exc!r}")
        return False

    def _on_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        from gi.repository import Gdk

        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._move_selection(-1 if keyval == Gdk.KEY_Up else 1)
            return True
        return False

    def _move_selection(self, direction: int) -> None:
        if not self._buttons or self._list is None:
            return
        current = self._list.get_selected_row()
        try:
            index = self._buttons.index(current)
        except ValueError:
            index = 0
        index = max(0, min(len(self._buttons) - 1, index + direction))
        self._list.select_row(self._buttons[index])

    @property
    def window(self):
        return self._window
