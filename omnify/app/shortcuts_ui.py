"""Settings UI for editing every Omnify keyboard shortcut."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..core.commands import (
    ShortcutBinding,
    ShortcutManager,
    format_combo,
    format_keys,
    normalize_combo,
)
from ..core.i18n import tr
from .key_utils import key_event_combo


class ShortcutSettingsPanel:
    """Searchable list of commands with record, global and reset controls."""

    def __init__(
        self,
        context,
        manager: ShortcutManager,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._ctx = context
        self._manager = manager
        self._on_change = on_change
        self._filter = ""
        self._list: Optional[Any] = None
        self._search: Optional[Any] = None
        self._root: Optional[Any] = None

    def build(self):
        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("omnify-card")
        self._root = box

        heading = Gtk.Label(label="Keyboard shortcuts", xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        box.append(heading)
        intro = Gtk.Label(
            label=(
                "Every action is editable. Use the search box, press Record, "
                "then press the new combination. Global shortcuts also work "
                "while Omnify is hidden in the system tray."
            ),
            xalign=0.0,
        )
        intro.set_wrap(True)
        intro.get_style_context().add_class("omnify-dim")
        box.append(intro)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(tr("Search commands…"))
        self._search.set_hexpand(True)
        self._search.connect("changed", self._on_search_changed)
        top.append(self._search)
        reset = Gtk.Button(label=tr("Reset all"))
        reset.set_tooltip_text(tr("Restore every default shortcut"))
        reset.connect("clicked", self._on_reset_all)
        top.append(reset)
        box.append(top)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(260)
        scroller.set_vexpand(True)
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.set_child(self._list)
        box.append(scroller)

        self.refresh()
        return box

    def refresh(self) -> None:
        from gi.repository import Gtk

        if self._list is None:
            return
        while True:
            child = self._list.get_first_child()
            if child is None:
                break
            self._list.remove(child)

        query = (self._search.get_text() if self._search is not None else "").casefold()
        for binding in self._manager.bindings():
            command = binding.command
            searchable = " ".join(
                (
                    command.title,
                    command.id,
                    command.category,
                    command.description,
                )
            ).casefold()
            if query and query not in searchable:
                continue
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            content.set_margin_top(7)
            content.set_margin_bottom(7)
            content.set_margin_start(8)
            content.set_margin_end(8)

            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text.set_hexpand(True)
            title = Gtk.Label(label=tr(command.title), xalign=0.0)
            title.get_style_context().add_class("shortcuts-title")
            text.append(title)
            meta = Gtk.Label(
                label=f"{tr(command.category)} · {command.id}",
                xalign=0.0,
            )
            meta.get_style_context().add_class("omnify-dim")
            text.append(meta)
            content.append(text)

            keys = Gtk.Label(
                label=format_keys(binding.keys) or tr("Not assigned"),
                xalign=1.0,
            )
            keys.get_style_context().add_class("shortcuts-keys")
            keys.set_size_request(180, -1)
            content.append(keys)

            global_check = Gtk.CheckButton(label=tr("Global"))
            global_check.set_active(binding.global_enabled)
            global_check.set_tooltip_text(
                tr("Works while Omnify is hidden in the tray")
            )
            global_check.connect(
                "toggled",
                self._on_global_toggled,
                command.id,
            )
            content.append(global_check)

            record = Gtk.Button(label=tr("Record…"))
            record.connect("clicked", self._on_record, command.id)
            content.append(record)

            reset = Gtk.Button(label="↺")
            reset.set_tooltip_text(tr("Reset this shortcut"))
            reset.connect("clicked", self._on_reset, command.id)
            content.append(reset)

            row.set_child(content)
            self._list.append(row)

    # -- callbacks -----------------------------------------------------
    def _on_search_changed(self, _entry) -> None:
        self.refresh()

    def _on_reset(self, _button, command_id: str) -> None:
        self._manager.reset(command_id)
        self.refresh()
        self._changed()

    def _on_reset_all(self, _button) -> None:
        self._manager.reset_all()
        self.refresh()
        self._changed()

    def _on_global_toggled(self, check, command_id: str) -> None:
        self._manager.set_global(command_id, check.get_active())
        self._changed()

    def _on_record(self, _button, command_id: str) -> None:
        command = self._manager._registry.get(command_id)
        if command is None:
            return
        self._open_recorder(command)

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _open_recorder(self, command) -> None:
        from gi.repository import Gtk

        window = Gtk.Window(title=tr("Record shortcut"))
        window.set_transient_for(self._root.get_root() if self._root else None)
        window.set_modal(True)
        window.set_default_size(480, 250)
        window.set_destroy_with_parent(True)
        self._ctx.suspend_shortcuts = True

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        outer.set_margin_start(14)
        outer.set_margin_end(14)

        title = Gtk.Label(label=command.title, xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        outer.append(title)
        hint = Gtk.Label(
            label=(
                "Press Record and then the key combination, or type it "
                "below (for example alt+f4 or ctrl+shift+p)."
            ),
            xalign=0.0,
        )
        hint.set_wrap(True)
        hint.get_style_context().add_class("omnify-dim")
        outer.append(hint)

        entry = Gtk.Entry()
        entry.set_text(format_combo(self._manager.keys_for(command.id)[0]) if self._manager.keys_for(command.id) else "")
        entry.set_placeholder_text("ctrl+shift+p")
        entry.set_hexpand(True)
        outer.append(entry)

        status = Gtk.Label(label="", xalign=0.0)
        status.set_wrap(True)
        status.get_style_context().add_class("omnify-dim")
        outer.append(status)

        recording = {"value": False}
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        record = Gtk.Button(label=tr("Record"))
        record.set_tooltip_text(tr("Capture the next key combination"))
        buttons.append(record)

        cancel = Gtk.Button(label=tr("Cancel"))
        buttons.append(cancel)
        save = Gtk.Button(label=tr("Save"))
        save.get_style_context().add_class("suggested-action")
        buttons.append(save)
        buttons.set_halign(Gtk.Align.END)
        outer.append(buttons)

        def finish(ok: bool) -> None:
            self._ctx.suspend_shortcuts = False
            if ok:
                raw = entry.get_text().strip()
                combo = normalize_combo(raw)
                if not combo:
                    status.set_text(tr("Enter a key combination"))
                    return
                conflicts = self._manager.conflicts(
                    combo,
                    exclude_command_id=command.id,
                    scope=command.scope,
                    global_enabled=self._manager.is_global(command.id),
                )
                if conflicts:
                    names = ", ".join(
                        tr(binding.command.title) for binding in conflicts[:3]
                    )
                    status.set_text(tr("Already used by: {names}", names=names))
                    return
                self._manager.set_keys(command.id, [combo])
                window.close()
                self.refresh()
                self._changed()
            else:
                window.close()

        def start_recording(_button) -> None:
            recording["value"] = True
            entry.set_editable(False)
            entry.set_text("")
            status.set_text(tr("Press keys now…"))
            record.set_label(tr("Listening…"))
            window.present()

        def on_key(_controller, keyval, _keycode, state) -> bool:
            from gi.repository import Gdk

            if not recording["value"]:
                return False
            if keyval == Gdk.KEY_Escape:
                recording["value"] = False
                entry.set_editable(True)
                record.set_label(tr("Record"))
                status.set_text("")
                return True
            if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete) and not (
                state
                & (
                    Gdk.ModifierType.CONTROL_MASK
                    | Gdk.ModifierType.SHIFT_MASK
                    | Gdk.ModifierType.ALT_MASK
                )
            ):
                recording["value"] = False
                entry.set_editable(True)
                entry.set_text("")
                record.set_label(tr("Record"))
                status.set_text(tr("Shortcut cleared — press Save"))
                return True
            combo = key_event_combo(keyval, state)
            if not combo:
                return True
            entry.set_editable(True)
            entry.set_text(format_combo(combo))
            entry.set_position(-1)
            recording["value"] = False
            record.set_label(tr("Record"))
            status.set_text("")
            return True

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", on_key)
        window.add_controller(controller)
        record.connect("clicked", start_recording)
        cancel.connect("clicked", lambda _b: finish(False))
        save.connect("clicked", lambda _b: finish(True))
        window.connect("destroy", lambda _w: setattr(self._ctx, "suspend_shortcuts", False))
        window.set_child(outer)
        window.present()
        entry.grab_focus()


class ShortcutSettingsCard:
    """Compatibility wrapper for callers that prefer a ``build()`` object."""

    def __init__(self, context, manager: ShortcutManager, on_change=None) -> None:
        self._panel = ShortcutSettingsPanel(context, manager, on_change)

    def build(self):
        return self._panel.build()

    def refresh(self) -> None:
        self._panel.refresh()
