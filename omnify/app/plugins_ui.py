"""Settings card for discovering and enabling drop-in plugins."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..core.i18n import tr


class PluginSettingsPanel:
    """Lists built-in and external plugins with trust/load controls."""

    def __init__(
        self,
        manager,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._manager = manager
        self._on_change = on_change
        self._root: Optional[Any] = None
        self._list: Optional[Any] = None
        self._errors: Optional[Any] = None

    def build(self):
        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("omnify-card")
        self._root = box

        heading = Gtk.Label(label=tr("Plugins"), xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        box.append(heading)
        intro = Gtk.Label(
            label=tr(
                "Drop a plugin folder into the Omnify plugins directory and "
                "press Rescan. External plugins run Python code with your "
                "permissions, so they stay disabled until you trust them."
            ),
            xalign=0.0,
        )
        intro.set_wrap(True)
        intro.get_style_context().add_class("omnify-dim")
        box.append(intro)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_button = Gtk.Button(label=tr("Open plugins folder"))
        open_button.connect("clicked", self._on_open_folder)
        buttons.append(open_button)
        rescan = Gtk.Button(label=tr("Rescan"))
        rescan.connect("clicked", self._on_rescan)
        buttons.append(rescan)
        add_note = Gtk.Label(
            label=tr("Built-in plugins ship with Omnify; external folders are read from the data directory."),
            xalign=0.0,
        )
        add_note.set_wrap(True)
        add_note.get_style_context().add_class("omnify-dim")
        add_note.set_hexpand(True)
        buttons.append(add_note)
        box.append(buttons)

        self._errors = Gtk.Label(label="", xalign=0.0)
        self._errors.set_wrap(True)
        self._errors.get_style_context().add_class("omnify-danger")
        box.append(self._errors)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(180)
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
        for record in self._manager.records():
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            content.set_margin_start(8)
            content.set_margin_end(8)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title = Gtk.Label(
                label=f"{record.manifest.name} · {record.manifest.version}",
                xalign=0.0,
            )
            title.get_style_context().add_class("shortcuts-title")
            title.set_hexpand(True)
            header.append(title)
            source = Gtk.Label(label=tr(record.source_label), xalign=1.0)
            source.get_style_context().add_class("omnify-dim")
            header.append(source)
            content.append(header)

            description = record.manifest.description or tr("No description")
            desc = Gtk.Label(label=description, xalign=0.0)
            desc.set_wrap(True)
            desc.get_style_context().add_class("omnify-dim")
            content.append(desc)

            if record.manifest.permissions:
                perms = Gtk.Label(
                    label=tr("Permissions: {items}", items=", ".join(record.manifest.permissions)),
                    xalign=0.0,
                )
                perms.set_wrap(True)
                perms.get_style_context().add_class("omnify-dim")
                content.append(perms)

            status_text = (
                tr("Loaded")
                if record.loaded
                else tr("Error")
                if record.error
                else tr("Enabled")
                if record.enabled
                else tr("Disabled")
            )
            status = Gtk.Label(
                label=f"{status_text}"
                + (f" · {record.error}" if record.error else ""),
                xalign=0.0,
            )
            status.set_wrap(True)
            status.get_style_context().add_class(
                "browser-status-ok"
                if record.loaded
                else "browser-status-error"
                if record.error
                else "omnify-dim"
            )
            content.append(status)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            if record.loaded:
                reload = Gtk.Button(label=tr("Reload"))
                reload.connect("clicked", self._on_reload, record.id)
                actions.append(reload)
                disable = Gtk.Button(label=tr("Disable"))
                disable.connect("clicked", self._on_disable, record.id)
                actions.append(disable)
            elif record.enabled:
                reload = Gtk.Button(label=tr("Reload"))
                reload.connect("clicked", self._on_reload, record.id)
                actions.append(reload)
                disable = Gtk.Button(label=tr("Disable"))
                disable.connect("clicked", self._on_disable, record.id)
                actions.append(disable)
            else:
                enable = Gtk.Button(label=tr("Enable"))
                enable.get_style_context().add_class("omnify-accent")
                enable.connect("clicked", self._on_enable, record.id)
                actions.append(enable)
            content.append(actions)
            row.set_child(content)
            self._list.append(row)

        errors = self._manager.errors()
        if self._errors is not None:
            if errors:
                text = "\n".join(f"{name}: {message}" for name, message in errors)
                self._errors.set_text(tr("Plugin errors:") + "\n" + text)
            else:
                self._errors.set_text("")

    # -- callbacks -----------------------------------------------------
    def _on_open_folder(self, _button) -> None:
        try:
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(
                self._manager.plugins_dir.resolve().as_uri(), None
            )
        except Exception:
            pass

    def _on_rescan(self, _button=None) -> None:
        self._manager.scan()
        self._manager.load_all()
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    def _on_enable(self, _button, plugin_id: str) -> None:
        record = self._manager.get(plugin_id)
        if record is None:
            return
        trust = record.manifest.builtin
        if not trust:
            trust = self._confirm_trust(record)
            if not trust:
                return
        if self._manager.enable(plugin_id, trust=trust):
            self.refresh()
            if self._on_change is not None:
                self._on_change()

    def _on_disable(self, _button, plugin_id: str) -> None:
        self._manager.disable(plugin_id)
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    def _on_reload(self, _button, plugin_id: str) -> None:
        self._manager.reload(plugin_id)
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    def _confirm_trust(self, record) -> bool:
        from gi.repository import Gtk

        dialog = Gtk.AlertDialog()
        dialog.set_message(tr("Trust {name}?", name=record.manifest.name))
        dialog.set_detail(
            tr(
                "Plugins run Python code with your user permissions and can "
                "read files, use the network and change your data. Only enable "
                "plugins you trust."
            )
        )
        dialog.set_buttons([tr("Cancel"), tr("Trust and enable")])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.set_modal(True)
        try:
            parent = self._root.get_root() if self._root is not None else None
            return dialog.choose(parent) == 1
        except Exception:
            return False
