"""Backup settings and backup manager dialogs.

The panel is embedded in the main Settings page. It only exposes policy and
manual operations; automatic cleanup is strictly opt-in, never removes
starred archives and only understands Omnify archives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from omnify.core.backup import BackupConfig, BackupManager
from omnify.core.i18n import tr
from omnify.modules.notes_engine import NoteStore  # noqa: E402


class BackupSettingsPanel(Gtk.Box):
    """Settings card with schedule, destination, limit and retention policy."""

    def __init__(self, ctx, parent_window: Callable[[], Any]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("omnify-card")
        self.ctx = ctx
        self._parent_window = parent_window
        self._store = NoteStore()
        self._config = BackupConfig.load(ctx.storage)
        self._manager_window: Gtk.Window | None = None
        self._chooser: Gtk.FileChooserNative | None = None
        self._updating = False
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        title = Gtk.Label(label=tr("Backups"), xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        self.append(title)

        note = Gtk.Label(
            label=tr(
                "Automatic copies of all notes. Backups live outside the notes "
                "folder; starred archives can only be deleted manually."
            ),
            xalign=0.0,
            wrap=True,
        )
        note.get_style_context().add_class("omnify-dim")
        self.append(note)

        self._auto_switch = Gtk.Switch()
        self._auto_switch.set_halign(Gtk.Align.START)
        self._auto_switch.connect("notify::active", self._on_auto_toggled)
        auto_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto_label = Gtk.Label(label=tr("Create backups automatically"), xalign=0.0)
        auto_label.set_size_request(210, -1)
        auto_row.append(auto_label)
        auto_row.append(self._auto_switch)
        self.append(auto_row)

        interval_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        interval_label = Gtk.Label(label=tr("Every"), xalign=0.0)
        interval_label.set_size_request(210, -1)
        interval_row.append(interval_label)
        self._interval_spin = Gtk.SpinButton.new_with_range(1, 999, 1)
        self._interval_spin.connect("value-changed", self._on_schedule_changed)
        interval_row.append(self._interval_spin)
        self._interval_combo = Gtk.ComboBoxText()
        for unit in ("minutes", "hours", "days", "weeks"):
            self._interval_combo.append(unit, tr(unit))
        self._interval_combo.connect("changed", self._on_schedule_changed)
        interval_row.append(self._interval_combo)
        self.append(interval_row)

        destination_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        destination_label = Gtk.Label(label=tr("Backup folder"), xalign=0.0)
        destination_label.set_size_request(210, -1)
        destination_row.append(destination_label)
        self._destination_entry = Gtk.Entry()
        self._destination_entry.set_hexpand(True)
        self._destination_entry.set_placeholder_text(str(self._default_destination()))
        self._destination_entry.connect("activate", self._on_destination_apply)
        destination_row.append(self._destination_entry)
        choose_button = Gtk.Button(label=tr("Choose…"))
        choose_button.connect("clicked", self._on_choose_destination)
        destination_row.append(choose_button)
        apply_button = Gtk.Button(label=tr("Apply"))
        apply_button.connect("clicked", self._on_destination_apply)
        destination_row.append(apply_button)
        default_button = Gtk.Button(label=tr("Default"))
        default_button.connect("clicked", self._on_destination_default)
        destination_row.append(default_button)
        self.append(destination_row)

        destination_hint = Gtk.Label(
            label=tr(
                "Leave empty to use the default folder. The folder is never "
                "deleted by uninstall."
            ),
            xalign=0.0,
            wrap=True,
        )
        destination_hint.get_style_context().add_class("omnify-dim")
        self.append(destination_hint)

        limit_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        limit_label = Gtk.Label(
            label=tr("Warn when there are more than"), xalign=0.0
        )
        limit_label.set_size_request(210, -1)
        limit_row.append(limit_label)
        self._limit_spin = Gtk.SpinButton.new_with_range(0, 999, 1)
        self._limit_spin.connect("value-changed", self._on_limit_changed)
        limit_row.append(self._limit_spin)
        backup_word = Gtk.Label(label=tr("backups"), xalign=0.0)
        backup_word.get_style_context().add_class("omnify-dim")
        limit_row.append(backup_word)
        self.append(limit_row)

        limit_hint = Gtk.Label(
            label=tr("0 = no limit (default)"),
            xalign=0.0,
            wrap=True,
        )
        limit_hint.get_style_context().add_class("omnify-dim")
        self.append(limit_hint)

        prune_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._prune_check = Gtk.CheckButton(
            label=tr("Automatically delete backups older than")
        )
        self._prune_check.connect("toggled", self._on_prune_toggled)
        self._prune_check.set_hexpand(True)
        prune_row.append(self._prune_check)
        self._prune_spin = Gtk.SpinButton.new_with_range(1, 999, 1)
        self._prune_spin.connect("value-changed", self._on_prune_changed)
        prune_row.append(self._prune_spin)
        self._prune_combo = Gtk.ComboBoxText()
        for unit in ("months", "years"):
            self._prune_combo.append(unit, tr(unit))
        self._prune_combo.connect("changed", self._on_prune_changed)
        prune_row.append(self._prune_combo)
        self.append(prune_row)

        prune_hint = Gtk.Label(
            label=tr(
                "Starred backups and legacy archives are always kept. This "
                "option never deletes files from other programs."
            ),
            xalign=0.0,
            wrap=True,
        )
        prune_hint.get_style_context().add_class("omnify-dim")
        self.append(prune_hint)

        self._stats_label = Gtk.Label(label="", xalign=0.0)
        self._stats_label.get_style_context().add_class("omnify-dim")
        self.append(self._stats_label)
        self._limit_label = Gtk.Label(label="", xalign=0.0, wrap=True)
        self._limit_label.get_style_context().add_class("omnify-danger")
        self.append(self._limit_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        backup_now = Gtk.Button(label=tr("Backup now"))
        backup_now.connect("clicked", self._on_backup_now)
        actions.append(backup_now)
        manage = Gtk.Button(label=tr("Manage backups…"))
        manage.connect("clicked", self._on_manage)
        actions.append(manage)
        open_folder = Gtk.Button(label=tr("Open backup folder"))
        open_folder.connect("clicked", self._on_open_folder)
        actions.append(open_folder)
        self.append(actions)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _default_destination() -> Path:
        config = BackupConfig()
        return config.destination_path()

    def _manager(self) -> BackupManager:
        return BackupManager(self._config)

    def _save(self) -> None:
        self._config.save(self.ctx.storage)

    def _units(self) -> list[str]:
        return ["minutes", "hours", "days", "weeks"]

    def _prune_units(self) -> list[str]:
        return ["months", "years"]

    def refresh(self) -> None:
        """Refresh counts, warning and controls from the current config."""
        self._updating = True
        try:
            self._auto_switch.set_active(self._config.enabled)
            self._interval_spin.set_value(max(1, self._config.interval_value))
            units = self._units()
            self._interval_combo.set_active(
                units.index(self._config.interval_unit)
                if self._config.interval_unit in units
                else units.index("days")
            )
            self._destination_entry.set_text(self._config.destination)
            self._limit_spin.set_value(max(0, self._config.limit_count))
            self._prune_check.set_active(self._config.auto_prune)
            self._prune_spin.set_value(max(1, self._config.prune_value))
            prune_units = self._prune_units()
            self._prune_combo.set_active(
                prune_units.index(self._config.prune_unit)
                if self._config.prune_unit in prune_units
                else prune_units.index("years")
            )
            self._interval_spin.set_sensitive(self._config.enabled)
            self._interval_combo.set_sensitive(self._config.enabled)
            self._prune_spin.set_sensitive(self._config.auto_prune)
            self._prune_combo.set_sensitive(self._config.auto_prune)

            manager = self._manager()
            summary = manager.summary()
            stats = tr(
                "Backups: {count} · {size} · starred: {starred}",
                count=summary["count"],
                size=BackupManager.size_text(summary["bytes"]),
                starred=summary["starred"],
            )
            latest = manager.latest()
            if latest is not None:
                stats = f"{stats} · {tr('latest: {stamp}', stamp=latest.stamp)}"
            self._stats_label.set_text(stats)
            if summary["exceeded"]:
                self._limit_label.set_text(
                    tr(
                        "Backup limit exceeded: {count}/{limit}. Delete old backups manually or enable automatic cleanup below.",
                        count=summary["count"],
                        limit=summary["limit"],
                    )
                )
            else:
                self._limit_label.set_text("")
        finally:
            self._updating = False

    def _run_maintenance(self) -> None:
        manager = self._manager()
        removed = manager.prune_old()
        notice = manager.take_limit_notice()
        self._save()
        if removed:
            self.ctx.notify(
                tr("Removed old backups automatically: {count}", count=removed)
            )
        if notice is not None:
            count, limit = notice
            self.ctx.notify(
                tr(
                    "Backup limit exceeded: {count}/{limit}. Delete old backups manually or enable automatic cleanup.",
                    count=count,
                    limit=limit,
                )
            )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _on_auto_toggled(self, switch, _parameter) -> None:
        if self._updating:
            return
        self._config.enabled = bool(switch.get_active())
        self._save()
        self.refresh()

    def _on_schedule_changed(self, _widget) -> None:
        if self._updating:
            return
        self._config.interval_value = max(1, int(self._interval_spin.get_value()))
        unit = self._interval_combo.get_active_id() or "days"
        self._config.interval_unit = unit
        self._save()

    def _on_limit_changed(self, _widget) -> None:
        if self._updating:
            return
        self._config.limit_count = max(0, int(self._limit_spin.get_value()))
        self._config.last_limit_notice_count = 0
        self._save()
        self.refresh()

    def _on_prune_toggled(self, check, _parameter) -> None:
        if self._updating:
            return
        self._config.auto_prune = bool(check.get_active())
        self._save()
        if self._config.auto_prune:
            self._run_maintenance()
        self.refresh()

    def _on_prune_changed(self, _widget) -> None:
        if self._updating:
            return
        self._config.prune_value = max(1, int(self._prune_spin.get_value()))
        self._config.prune_unit = self._prune_combo.get_active_id() or "years"
        self._save()
        if self._config.auto_prune:
            self._run_maintenance()
        self.refresh()

    def _on_destination_apply(self, _widget) -> None:
        self._config.destination = self._destination_entry.get_text().strip()
        self._save()
        self.refresh()
        self.ctx.notify(tr("Backup folder updated"))

    def _on_destination_default(self, _button) -> None:
        self._config.destination = ""
        self._save()
        self.refresh()
        self.ctx.notify(tr("Backup folder reset to default"))

    def _on_choose_destination(self, _button) -> None:
        dialog = Gtk.FileChooserNative(
            title=tr("Choose backup folder"),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self._parent_window(),
        )

        def on_response(native, response):
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            selected = native.get_file()
            native.destroy()
            if selected is None or not selected.get_path():
                return
            self._config.destination = selected.get_path()
            self._save()
            self.refresh()

        dialog.connect("response", on_response)
        self._chooser = dialog
        dialog.show()

    def _on_backup_now(self, _button) -> None:
        try:
            path = self._store.create_backup(self._config.destination_path())
            self._run_maintenance()
            self.refresh()
        except Exception as exc:
            self.ctx.notify(tr("Could not create backup: {error}", error=exc))
            return
        self.ctx.notify(tr("Backup created: {path}", path=path))

    def _on_open_folder(self, _button) -> None:
        try:
            directory = self._config.destination_path()
            directory.mkdir(parents=True, exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(directory.as_uri(), None)
        except Exception as exc:
            self.ctx.notify(tr("Could not open backup folder: {error}", error=exc))

    def _on_manage(self, _button) -> None:
        if self._manager_window is not None:
            self._manager_window.present()
            return
        self._open_manager_window()

    # ------------------------------------------------------------------
    # Manage backups window
    # ------------------------------------------------------------------
    def _confirm(self, message: str, detail: str, label: str) -> bool:
        dialog = Gtk.AlertDialog()
        dialog.set_message(message)
        dialog.set_detail(detail)
        dialog.set_buttons([tr("Cancel"), label])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.set_modal(True)
        try:
            return dialog.choose(self._manager_window or self._parent_window()) == 1
        except Exception:
            return False

    def _open_manager_window(self) -> None:
        window = Gtk.Window(title=tr("Manage backups"))
        window.set_transient_for(self._parent_window())
        window.set_modal(True)
        window.set_destroy_with_parent(True)
        window.set_default_size(720, 480)
        window.connect(
            "destroy",
            lambda _window: setattr(self, "_manager_window", None),
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        heading = Gtk.Label(label=tr("Manage backups"), xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        outer.append(heading)
        hint = Gtk.Label(
            label=tr(
                "Star protects a backup from automatic cleanup. Deleting is "
                "always a manual action."
            ),
            xalign=0.0,
            wrap=True,
        )
        hint.get_style_context().add_class("omnify-dim")
        outer.append(hint)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.get_style_context().add_class("omnify-list")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(listbox)
        outer.append(scroller)

        empty_label = Gtk.Label(label=tr("No backups yet."), xalign=0.0)
        empty_label.get_style_context().add_class("omnify-dim")
        outer.append(empty_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        create_button = Gtk.Button(label=tr("Backup now"))
        actions.append(create_button)
        open_button = Gtk.Button(label=tr("Open backup folder"))
        open_button.connect("clicked", self._on_open_folder)
        actions.append(open_button)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        actions.append(spacer)
        close_button = Gtk.Button(label=tr("Close"))
        close_button.connect("clicked", lambda _button: window.close())
        actions.append(close_button)
        outer.append(actions)
        window.set_child(outer)

        def refresh_list() -> None:
            while True:
                child = listbox.get_first_child()
                if child is None:
                    break
                listbox.remove(child)
            manager = self._manager()
            records = manager.list_backups()
            empty_label.set_visible(not records)
            for record in records:
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                box.set_margin_top(6)
                box.set_margin_bottom(6)
                box.set_margin_start(6)
                box.set_margin_end(6)

                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                info.set_hexpand(True)
                name = Gtk.Label(label=record.name, xalign=0.0)
                name.set_ellipsize(3)
                info.append(name)
                meta = tr(
                    "{stamp} · {size}{starred}{legacy}",
                    stamp=record.stamp,
                    size=BackupManager.size_text(record.size),
                    starred=tr(" · starred") if record.starred else "",
                    legacy=tr(" · legacy") if record.legacy else "",
                )
                meta_label = Gtk.Label(label=meta, xalign=0.0)
                meta_label.get_style_context().add_class("omnify-dim")
                info.append(meta_label)
                box.append(info)

                star_button = Gtk.Button(
                    label=tr("Unstar") if record.starred else tr("Star")
                )
                star_button.get_style_context().add_class("notes-folder-button")
                star_button.connect(
                    "clicked",
                    lambda _button, item=record: self._toggle_star(item.path),
                )
                box.append(star_button)

                restore_button = Gtk.Button(label=tr("Restore"))
                restore_button.connect(
                    "clicked",
                    lambda _button, item=record: self._restore_backup(item.path),
                )
                box.append(restore_button)

                delete_button = Gtk.Button(label=tr("Delete"))
                delete_button.get_style_context().add_class("omnify-danger")
                delete_button.connect(
                    "clicked",
                    lambda _button, item=record: self._delete_backup(item.path),
                )
                box.append(delete_button)
                row.set_child(box)
                listbox.append(row)

        def on_star_or_delete() -> None:
            self.refresh()
            refresh_list()

        self._manager_refresh = on_star_or_delete

        def on_create(_button) -> None:
            self._on_backup_now(_button)
            on_star_or_delete()

        create_button.connect("clicked", on_create)
        refresh_list()

        self._manager_window = window
        window.present()

    def _toggle_star(self, path: Path) -> None:
        manager = self._manager()
        record = manager.describe(path)
        if record is None:
            return
        try:
            if record.starred:
                manager.unstar(path)
                self.ctx.notify(tr("Backup unstarred"))
            else:
                manager.star(path)
                self.ctx.notify(tr("Backup starred"))
        except Exception as exc:
            self.ctx.notify(tr("Could not update backup: {error}", error=exc))
            return
        self.refresh()
        refresh = getattr(self, "_manager_refresh", None)
        if callable(refresh):
            refresh()

    def _restore_backup(self, path: Path) -> None:
        if not self._confirm(
            tr("Restore backup?"),
            tr(
                "Notes from the backup will be merged with current notes. "
                "A safety copy will be created first."
            ),
            tr("Restore backup"),
        ):
            return
        get_module = getattr(self.ctx, "get_module", None)
        notes = get_module("notes") if callable(get_module) else None
        restore = getattr(notes, "restore_backup_archive", None)
        if not callable(restore):
            self.ctx.notify(tr("Open Notes first to restore a backup."))
            return
        try:
            restored = restore(path)
        except Exception as exc:
            self.ctx.notify(tr("Could not restore backup: {error}", error=exc))
            return
        self.ctx.notify(tr("Restored notes: {count}", count=restored))
        self.refresh()
        refresh = getattr(self, "_manager_refresh", None)
        if callable(refresh):
            refresh()

    def _delete_backup(self, path: Path) -> None:
        if not self._confirm(
            tr("Delete backup?"),
            tr("This permanently deletes the selected backup file."),
            tr("Delete backup"),
        ):
            return
        manager = self._manager()
        try:
            manager.delete(path)
        except Exception as exc:
            self.ctx.notify(tr("Could not delete backup: {error}", error=exc))
            return
        self.ctx.notify(tr("Backup deleted"))
        self.refresh()
        refresh = getattr(self, "_manager_refresh", None)
        if callable(refresh):
            refresh()
