"""Notes module: local notes and quick capture.

The module keeps the data model in :mod:`notes_engine` and only builds the
GTK widgets here.  Notes are stored as one atomic JSON document through the
shared :class:`~omnify.core.storage.Storage` object.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.module_context import BaseModule
from .notes_engine import Note


class NotesModule(BaseModule):
    id = "notes"
    name = "Notes"
    icon = "📝"
    description = "Local notes and quick capture."
    group = "Tools"

    #: Storage key and debounce delay for the editor's autosave.
    KEY = "notes"
    SAVE_DELAY_MS = 900

    def build_view(self):
        from gi.repository import Gtk

        self._notes: list[Note] = []
        self._notes_by_id: dict[str, Note] = {}
        self._rows_by_id: dict[str, Gtk.ListBoxRow] = {}
        self._row_ids: dict[Gtk.ListBoxRow, str] = {}
        self._row_labels: dict[str, Gtk.Label] = {}
        self._current_note: Note | None = None
        self._selected_id: str | None = None
        self._loading = False
        self._save_timer = None

        root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.set_position(260)
        root.set_resize_start_child(False)
        root.set_shrink_start_child(False)

        # Left panel: note list
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_size_request(220, -1)
        sidebar.set_vexpand(True)
        sidebar.get_style_context().add_class("omnify-sidebar")

        sidebar_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        sidebar_header.set_margin_top(12)
        sidebar_header.set_margin_start(8)
        sidebar_header.set_margin_end(8)

        notes_label = Gtk.Label(label="Notes", xalign=0.0)
        notes_label.set_hexpand(True)
        notes_label.get_style_context().add_class("omnify-card-title")
        sidebar_header.append(notes_label)

        new_button = Gtk.Button(label="+")
        new_button.set_tooltip_text("New note")
        new_button.get_style_context().add_class("notes-icon-button")
        new_button.connect("clicked", self._on_new_note_clicked)
        sidebar_header.append(new_button)
        sidebar.append(sidebar_header)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search notes")
        search.set_margin_start(8)
        search.set_margin_end(8)
        search.connect("search-changed", self._on_search_changed)
        sidebar.append(search)

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)

        notes_list = Gtk.ListBox()
        notes_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        list_scroll.set_child(notes_list)
        sidebar.append(list_scroll)
        root.set_start_child(sidebar)

        # Right panel: title and body editor
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        editor.set_hexpand(True)
        editor.set_vexpand(True)
        editor.set_margin_top(16)
        editor.set_margin_bottom(16)
        editor.set_margin_start(24)
        editor.set_margin_end(24)

        editor_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )

        title_entry = Gtk.Entry()
        title_entry.set_placeholder_text("Untitled")
        title_entry.set_hexpand(True)
        editor_header.append(title_entry)

        menu_button = Gtk.Button(label="⋮")
        menu_button.get_style_context().add_class("notes-icon-button")
        menu_button.set_tooltip_text("More actions")
        editor_header.append(menu_button)
        editor.append(editor_header)

        body_scroll = Gtk.ScrolledWindow()
        body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body_scroll.set_vexpand(True)
        # Reuse the calculator's focused input styling so the editor follows
        # the active theme instead of the toolkit's default text view colour.
        body_scroll.get_style_context().add_class("calc-input")

        body = Gtk.TextView()
        body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        body.set_accepts_tab(False)
        body.set_top_margin(8)
        body.set_bottom_margin(8)
        body.set_left_margin(8)
        body.set_right_margin(8)
        body.set_hexpand(True)
        body.set_vexpand(True)
        body.get_buffer().set_enable_undo(True)

        body_scroll.set_child(body)
        editor.append(body_scroll)
        root.set_end_child(editor)

        self._notes_list = notes_list
        self._title_entry = title_entry
        self._body_view = body

        self._load_notes()

        # Connect after the initial load so programmatic text changes do not
        # create a spurious save or overwrite the loaded note.
        notes_list.connect("row-selected", self._on_note_selected)
        title_entry.connect("changed", self._on_title_changed)
        body.get_buffer().connect("changed", self._on_body_changed)

        return root

    # -- loading and persistence ---------------------------------------
    def _load_notes(self) -> None:
        from gi.repository import Gtk

        raw = self.ctx.storage.load(self.KEY, {})
        notes_data = raw
        selected_id = None
        if isinstance(raw, dict):
            notes_data = raw.get("notes", [])
            selected_id = raw.get("selected_id")
        if not isinstance(notes_data, list):
            notes_data = []

        notes: list[Note] = []
        for item in notes_data:
            if not isinstance(item, dict):
                continue
            try:
                notes.append(Note.from_dict(item))
            except Exception:
                continue

        created_default = False
        if not notes:
            notes = [
                Note(
                    label="Welcome",
                    contents="Start writing here.\n\nUse + to add another note.",
                )
            ]
            created_default = True

        self._notes = notes
        self._notes_by_id = {note.id: note for note in notes}
        for note in notes:
            row = self._make_note_row(note)
            self._rows_by_id[note.id] = row
            self._notes_list.append(row)

        row = self._rows_by_id.get(selected_id)
        if row is None:
            row = self._rows_by_id[notes[0].id]
        self._notes_list.select_row(row)
        self._show_note(self._notes_by_id[self._row_ids[row]])

        if created_default:
            self._save_document()

    def _save_document(self) -> None:
        document = {
            "version": 1,
            "selected_id": self._selected_id,
            "notes": [note.to_dict() for note in self._notes],
        }
        try:
            self.ctx.storage.save(self.KEY, document)
        except Exception as exc:
            self.ctx.notify(f"Could not save notes: {exc}")

    def _sync_current_note(self) -> None:
        if self._current_note is None:
            return
        buffer = self._body_view.get_buffer()
        self._current_note.contents = buffer.get_text(
            buffer.get_start_iter(),
            buffer.get_end_iter(),
            True,
        )
        self._current_note.updated_at = datetime.now(timezone.utc).isoformat()

    def _schedule_save(self) -> None:
        from gi.repository import GLib

        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(self.SAVE_DELAY_MS, self._flush_save)

    def _flush_save(self) -> bool:
        self._save_timer = None
        self._sync_current_note()
        self._save_document()
        return False

    def save_state(self) -> None:
        """Flush the current note when Omnify quits or hides."""
        if getattr(self, "_body_view", None) is None:
            return
        from gi.repository import GLib

        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
            self._save_timer = None
        self._sync_current_note()
        self._save_document()

    def on_hide(self) -> None:
        self.save_state()

    def on_window_closed(self) -> None:
        self.save_state()

    # -- widgets and handlers ------------------------------------------
    def _make_note_row(self, note: Note):
        from gi.repository import Gtk

        row = Gtk.ListBoxRow()
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row_box.set_margin_top(8)
        row_box.set_margin_bottom(8)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)

        title_label = Gtk.Label(label=note.label or "Untitled", xalign=0.0)
        row_box.append(title_label)
        row.set_child(row_box)

        self._row_ids[row] = note.id
        self._row_labels[note.id] = title_label
        return row

    def _show_note(self, note: Note) -> None:
        self._current_note = note
        self._selected_id = note.id
        self._loading = True
        try:
            self._title_entry.set_text(note.label)
            self._body_view.get_buffer().set_text(note.contents)
        finally:
            self._loading = False

    def _on_note_selected(self, _listbox, row) -> None:
        if row is None or self._loading:
            return
        note_id = self._row_ids.get(row)
        note = self._notes_by_id.get(note_id)
        if note is None or note is self._current_note:
            return
        # Capture the previous editor contents before replacing the buffer.
        self._sync_current_note()
        self._show_note(note)
        self._schedule_save()

    def _on_new_note_clicked(self, _button) -> None:
        self._sync_current_note()
        note = Note()
        self._notes.append(note)
        self._notes_by_id[note.id] = note
        row = self._make_note_row(note)
        self._rows_by_id[note.id] = row
        self._notes_list.prepend(row)
        self._notes_list.select_row(row)
        if self._current_note is not note:
            self._show_note(note)
        self._title_entry.grab_focus()
        self._schedule_save()

    def _on_title_changed(self, entry) -> None:
        if self._loading or self._current_note is None:
            return
        label = entry.get_text().strip() or "Untitled"
        self._current_note.label = label
        title_label = self._row_labels.get(self._current_note.id)
        if title_label is not None:
            title_label.set_text(label)
        self._schedule_save()

    def _on_body_changed(self, _buffer) -> None:
        if self._loading or self._current_note is None:
            return
        self._schedule_save()

    def _on_search_changed(self, entry) -> None:
        query = entry.get_text().strip().casefold()
        for note in self._notes:
            row = self._rows_by_id.get(note.id)
            if row is not None:
                row.set_visible(not query or query in note.label.casefold())
