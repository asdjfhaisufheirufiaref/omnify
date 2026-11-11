"""Модуль нотаток: Markdown-файли, картинки, папки, теги та preview.

Цей файл — це "візуальна" частина нотаток. Він створює GTK-віджети,
реагує на натискання кнопок і показує картинки всередині тексту.
Робота з файлами винесена в ``notes_engine.py``, перетворення Markdown —
у ``notes_render.py``, а побудова preview — у ``notes_preview.py``.
Такий поділ дозволяє тестувати логіку без запущеного вікна.

Важлива особливість: у звичайному ``Gtk.TextView`` не можна просто
вставити картинку як символ. GTK використовує для цього спеціальний
об'єкт ``Gtk.TextChildAnchor``. Ми зберігаємо біля кожного такого якоря
Markdown-посилання, а під час збереження знову перетворюємо якір на текст
``![підпис](assets/...)``. Завдяки цьому у файлі залишається звичайний
Markdown, а в редакторі видно саму картинку.
"""

from __future__ import annotations

import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .notes_engine import Note, NoteStore, default_backup_dir
from .notes_preview import build_preview
from .notes_render import export_bundle_html, export_html, export_pdf

# Регулярний вираз шукає Markdown-посилання на картинку в папці assets.
# Група 1 — підпис (alt), група 2 — шлях після дужки.
# Приклад: ![фото](assets/abc123/photo.png)
_IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\((assets/[^)\n]+)\)")

# Спеціальні значення фільтра папок. Звичайний рядок означає реальну папку.
_ALL_NOTES = "__all__"
_UNFILED = ""


class NotesModule(BaseModule):
    """Модуль "Нотатки" для Omnify."""

    # Ці атрибути використовує головне вікно для меню та навігації.
    id = "notes"
    name = "Notes"
    icon = "📝"
    description = "Local notes and quick capture."
    group = "Tools"

    # У цьому JSON-файлі зберігається тільки id вибраної нотатки та фільтр
    # папки, а не тексти нотаток. Самі тексти завжди лежать у notes/*.md.
    STATE_KEY = "notes_state"

    # Через скільки мілісекунд після останньої зміни зберігати нотатку.
    # 900 мс — це компроміс: не писати файл на кожну літеру, але й не
    # втратити текст, якщо користувач раптом закриє вікно.
    SAVE_DELAY_MS = 900

    def build_view(self):
        """Створити і повернути GTK-віджет модуля.

        GTK імпортується всередині методу, щоб цей файл можна було
        імпортувати в тестах без запущеного графічного середовища.
        """
        from gi.repository import Gtk

        # ------------------------------------------------------------------
        # Стан модуля (звичайні Python-змінні, не віджети)
        # ------------------------------------------------------------------
        # Список усіх нотаток у тому порядку, у якому вони показані зліва.
        self._notes: list[Note] = []

        # Швидкий доступ до нотатки за її id: {id: Note}.
        self._notes_by_id: dict[str, Note] = {}

        # Віджети рядків списку: {id нотатки: Gtk.ListBoxRow}.
        self._rows_by_id: dict[str, Gtk.ListBoxRow] = {}

        # Зворотний словник: {рядок списку: id нотатки}.
        self._row_ids: dict[Gtk.ListBoxRow, str] = {}

        # Підписи рядків, щоб оновлювати назву без перебудови всього списку.
        self._row_labels: dict[str, Gtk.Label] = {}
        self._row_snippets: dict[str, Gtk.Label] = {}
        self._row_pins: dict[str, Gtk.Label] = {}
        self._row_tags: dict[str, Gtk.Label] = {}
        self._row_folders: dict[str, Gtk.Label] = {}

        # Нотатка, яка зараз відкрита в редакторі.
        self._current_note: Note | None = None

        # id нотатки, яку треба відкрити після запуску.
        self._selected_id: str | None = None

        # Яку папку показувати: "All notes", "Unfiled" або назву папки.
        self._folder_filter: str = _ALL_NOTES
        self._search_query: str = ""

        # Коли програма сама змінює текст (наприклад, завантажує нотатку),
        # сигнал "changed" не повинен вважати це редагуванням користувача.
        self._loading = False

        # Ідентифікатор таймера GLib, який відкладає збереження.
        self._save_timer = None

        # Сховище файлів нотаток. Створюється в _load_notes().
        self._store: NoteStore | None = None

        # Посилання на відкритий діалог вибору картинки. Якщо не зберігати
        # його в атрибуті, Python може зібрати об'єкт збирачем сміття,
        # поки діалог ще показується.
        self._image_dialog = None
        self._import_dialog = None
        self._export_dialog = None
        self._export_all_dialog = None
        self._sync_dialog = None
        self._restore_dialog = None
        self._trash_dialog = None
        self._history_dialog = None

        # Для кожного якоря картинки зберігаємо Markdown-текст, який
        # потрібно записати у файл. Ключ — Gtk.TextChildAnchor.
        self._image_markers: dict[object, str] = {}

        # id нотатки, у яку буде вставлено картинку з буфера обміну.
        # Це захищає від ситуації, коли користувач змінив нотатку, поки
        # програма асинхронно читала буфер.
        self._paste_note_id: str | None = None

        # ------------------------------------------------------------------
        # Ліва панель зі списком нотаток
        # ------------------------------------------------------------------
        root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.set_position(280)
        root.set_resize_start_child(False)
        root.set_shrink_start_child(False)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.set_size_request(230, -1)
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

        # Поле пошуку фільтрує список за назвою, текстом і тегами нотатки.
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search notes or #tag")
        search.set_margin_start(8)
        search.set_margin_end(8)
        search.connect("search-changed", self._on_search_changed)
        sidebar.append(search)
        self._search_entry = search

        # Кнопка-меню показує, яку папку зараз видно, і дозволяє
        # створювати/видаляти папки.
        folder_button = Gtk.MenuButton(label=tr("All notes"))
        folder_button.set_tooltip_text("Filter and manage folders")
        folder_button.get_style_context().add_class("notes-folder-button")
        folder_button.set_margin_start(8)
        folder_button.set_margin_end(8)
        folder_popover = Gtk.Popover()
        folder_popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        folder_popover_box.set_margin_top(8)
        folder_popover_box.set_margin_bottom(8)
        folder_popover_box.set_margin_start(8)
        folder_popover_box.set_margin_end(8)
        folder_popover.set_child(folder_popover_box)
        folder_button.set_popover(folder_popover)
        sidebar.append(folder_button)
        self._folder_button = folder_button
        self._folder_popover = folder_popover
        self._folder_popover_box = folder_popover_box

        # Меню тегів — швидкий спосіб відфільтрувати нотатки без ручного
        # введення #тегу в полі пошуку.
        tag_button = Gtk.MenuButton(label=tr("Tags"))
        tag_button.set_tooltip_text("Filter notes by tag")
        tag_button.get_style_context().add_class("notes-folder-button")
        tag_button.set_margin_start(8)
        tag_button.set_margin_end(8)
        tag_popover = Gtk.Popover()
        tag_popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        tag_popover_box.set_margin_top(8)
        tag_popover_box.set_margin_bottom(8)
        tag_popover_box.set_margin_start(8)
        tag_popover_box.set_margin_end(8)
        tag_popover.set_child(tag_popover_box)
        tag_button.set_popover(tag_popover)
        sidebar.append(tag_button)
        self._tag_button = tag_button
        self._tag_popover = tag_popover
        self._tag_popover_box = tag_popover_box

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)

        notes_list = Gtk.ListBox()
        notes_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        list_scroll.set_child(notes_list)
        sidebar.append(list_scroll)
        root.set_start_child(sidebar)

        # ------------------------------------------------------------------
        # Права панель: назва, теги, інструменти та текст нотатки
        # ------------------------------------------------------------------
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

        # Закріплена нотатка завжди стоїть зверху списку. Це не копія і не
        # окремий файл — у Markdown просто з'являється поле pinned: true.
        pin_button = Gtk.ToggleButton(label="📌")
        pin_button.set_tooltip_text("Pin note")
        pin_button.get_style_context().add_class("notes-icon-button")
        pin_button.connect("toggled", self._on_pin_note_toggled)
        editor_header.append(pin_button)
        self._pin_button = pin_button

        # Кнопка вибору картинки з файлу. Ctrl+V працює і без неї.
        image_button = Gtk.Button(label="🖼")
        image_button.set_tooltip_text("Insert image (Ctrl+V)")
        image_button.get_style_context().add_class("notes-icon-button")
        image_button.connect("clicked", self._on_insert_image_clicked)
        editor_header.append(image_button)

        # Кнопки вставляють Markdown-шаблони в позицію курсора.
        checklist_button = Gtk.Button(label="☑")
        checklist_button.set_tooltip_text("Insert checklist")
        checklist_button.get_style_context().add_class("notes-icon-button")
        checklist_button.connect(
            "clicked",
            lambda _button: self._insert_markdown_snippet("- [ ] "),
        )
        editor_header.append(checklist_button)

        table_button = Gtk.Button(label="▦")
        table_button.set_tooltip_text("Insert table")
        table_button.get_style_context().add_class("notes-icon-button")
        table_button.connect(
            "clicked",
            lambda _button: self._insert_markdown_snippet(
                "| Column 1 | Column 2 |\n| --- | --- |\n| Text | Text |\n"
            ),
        )
        editor_header.append(table_button)

        # Preview — це окремий режим, а не ще одне вікно. Він показує
        # заголовки, таблиці, чеклісти та картинки.
        preview_toggle = Gtk.ToggleButton(label="Preview")
        preview_toggle.set_tooltip_text("Preview Markdown")
        preview_toggle.get_style_context().add_class("notes-folder-button")
        preview_toggle.connect("toggled", self._on_preview_toggled)
        editor_header.append(preview_toggle)

        # Меню дій із нотаткою: експорт, резервна копія, синхронізація,
        # видалення. Поповер створюємо окремим методом, щоб не захаращувати
        # build_view.
        menu_button = self._build_note_menu()
        editor_header.append(menu_button)
        editor.append(editor_header)

        # Рядок тегів і кнопка переміщення нотатки в папку.
        tags_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tags_label = Gtk.Label(label="Tags", xalign=0.0)
        tags_label.get_style_context().add_class("omnify-dim")
        tags_row.append(tags_label)

        tags_entry = Gtk.Entry()
        tags_entry.set_placeholder_text("work, ideas")
        tags_entry.set_hexpand(True)
        tags_entry.connect("changed", self._on_tags_changed)

        # Підказки тегів: користувач бачить уже відомі теги й не робить
        # дублікатів через одрук.
        tag_completion_model = Gtk.ListStore(str)
        tag_completion = Gtk.EntryCompletion()
        tag_completion.set_model(tag_completion_model)
        tag_completion.set_text_column(0)
        tag_completion.set_inline_completion(True)
        tag_completion.set_popup_completion(True)
        tag_completion.set_minimum_key_length(1)
        tags_entry.set_completion(tag_completion)
        self._tag_completion_model = tag_completion_model

        tags_row.append(tags_entry)

        note_folder_button = Gtk.MenuButton(label="📁")
        note_folder_button.set_tooltip_text("Move note to folder")
        note_folder_button.get_style_context().add_class("notes-icon-button")
        note_folder_popover = Gtk.Popover()
        note_folder_popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        note_folder_popover_box.set_margin_top(8)
        note_folder_popover_box.set_margin_bottom(8)
        note_folder_popover_box.set_margin_start(8)
        note_folder_popover_box.set_margin_end(8)
        note_folder_popover.set_child(note_folder_popover_box)
        note_folder_button.set_popover(note_folder_popover)
        tags_row.append(note_folder_button)
        editor.append(tags_row)
        self._tag_entry = tags_entry
        self._note_folder_button = note_folder_button
        self._note_folder_popover = note_folder_popover
        self._note_folder_popover_box = note_folder_popover_box
        self._preview_toggle = preview_toggle

        # Панель форматування: ці кнопки працюють із виділеним текстом
        # або вставляють Markdown-шаблон у позицію курсора.
        format_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        format_bar.get_style_context().add_class("notes-format-bar")

        format_actions = [
            ("B", "Bold (Ctrl+B)", "bold", "notes-format-bold"),
            ("I", "Italic (Ctrl+I)", "italic", "notes-format-italic"),
            ("S", "Strikethrough", "strike", "notes-format-strike"),
            ("H", "Heading", "heading", ""),
            ("•", "Bullet list", "bullet", ""),
            ("1.", "Numbered list", "numbered", ""),
            ("❝", "Quote", "quote", ""),
            ("</>", "Inline code", "code", ""),
            ("🔗", "Link (Ctrl+K)", "link", ""),
        ]
        for label, tooltip, action, style_class in format_actions:
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tooltip)
            button.get_style_context().add_class("notes-format-button")
            if style_class:
                button.get_style_context().add_class(style_class)
            button.connect(
                "clicked",
                lambda _button, action=action: self._format_selection(action),
            )
            format_bar.append(button)
        editor.append(format_bar)
        self._format_bar = format_bar

        # Stack перемикає edit/preview, не втрачаючи текст у буфері.
        editor_stack = Gtk.Stack()
        editor_stack.set_hexpand(True)
        editor_stack.set_vexpand(True)
        editor_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        body_scroll = Gtk.ScrolledWindow()
        body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body_scroll.set_vexpand(True)
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

        # Перехоплюємо Ctrl+V у фазі CAPTURE, тобто до того, як TextView
        # вставить картинку як звичайний текст. Якщо в буфері немає
        # картинки, повертаємо False і GTK робить звичайне вставлення тексту.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_body_key_pressed)
        body.add_controller(keys)

        body_scroll.set_child(body)
        editor_stack.add_named(body_scroll, "edit")

        # Preview-вміст будується з Markdown заново під час перемикання.
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_vexpand(True)
        preview_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_host.set_hexpand(True)
        preview_scroll.set_child(preview_host)
        editor_stack.add_named(preview_scroll, "preview")
        editor.append(editor_stack)

        # Нижній рядок показує приблизну кількість слів і стан збереження.
        # Це допомагає не переживати, чи справді текст уже записано.
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        stats_label = Gtk.Label(label="0 words · 0 characters", xalign=0.0)
        stats_label.set_hexpand(True)
        stats_label.get_style_context().add_class("omnify-dim")
        stats_label.get_style_context().add_class("notes-status")
        status_row.append(stats_label)

        save_status_label = Gtk.Label(label=tr("Saved"), xalign=1.0)
        save_status_label.get_style_context().add_class("omnify-dim")
        save_status_label.get_style_context().add_class("notes-status")
        status_row.append(save_status_label)
        editor.append(status_row)
        root.set_end_child(editor)

        # Зберігаємо посилання на віджети, щоб інші методи могли їх знайти.
        self._notes_list = notes_list
        self._title_entry = title_entry
        self._body_view = body
        self._editor_stack = editor_stack
        self._preview_host = preview_host
        self._stats_label = stats_label
        self._save_status_label = save_status_label

        # Завантажуємо файли ДО підключення сигналів "changed" і
        # "row-selected": інакше саме завантаження виглядало б як
        # редагування користувача.
        self._load_notes()

        notes_list.connect("row-selected", self._on_note_selected)
        title_entry.connect("changed", self._on_title_changed)
        body.get_buffer().connect("changed", self._on_body_changed)

        # Періодично створюємо резервну копію. Перевірка швидка: якщо
        # свіжий архів уже є, нічого не копіюємо.
        self._maybe_auto_backup()

        return root

    # ==================================================================
    # Меню та діалоги
    # ==================================================================
    def _build_note_menu(self):
        """Створити кнопку "⋮" з діями над поточною нотаткою."""
        from gi.repository import Gtk

        menu_button = Gtk.MenuButton(label="⋮")
        menu_button.get_style_context().add_class("notes-icon-button")
        menu_button.set_tooltip_text("More actions")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        actions = [
            (tr("Duplicate note"), self._on_duplicate_note),
            (tr("Save version"), self._on_save_version),
            (tr("Version history…"), self._on_open_history),
            (tr("Import Markdown files…"), self._on_import_markdown),
            (tr("Export as HTML…"), self._on_export_html),
            (tr("Export as PDF…"), self._on_export_pdf),
            (tr("Export all notes as HTML…"), self._on_export_all_html),
            (tr("Backup notes"), self._on_backup_notes),
            (tr("Restore backup…"), self._on_restore_notes),
            (tr("Sync notes to folder…"), self._on_sync_notes),
            (tr("Two-way sync with folder…"), self._on_two_way_sync_notes),
        ]
        for label, callback in actions:
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.FILL)
            button.get_style_context().add_class("notes-menu-item")
            button.connect(
                "clicked",
                lambda _button, cb=callback: (popover.popdown(), cb()),
            )
            box.append(button)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        trash_button = Gtk.Button(label=tr("Trash…"))
        trash_button.set_halign(Gtk.Align.FILL)
        trash_button.get_style_context().add_class("notes-menu-item")
        trash_button.connect(
            "clicked",
            lambda _button: (popover.popdown(), self._on_open_trash()),
        )
        box.append(trash_button)

        delete_button = Gtk.Button(label=tr("Move to Trash"))
        delete_button.set_halign(Gtk.Align.FILL)
        delete_button.get_style_context().add_class("notes-menu-item")
        delete_button.connect(
            "clicked",
            lambda _button: (popover.popdown(), self._on_delete_note_clicked()),
        )
        box.append(delete_button)

        popover.set_child(box)
        menu_button.set_popover(popover)
        self._note_menu_popover = popover
        return menu_button

    def _parent_window(self):
        """Повернути головне вікно для модальних діалогів."""
        try:
            return self._notes_list.get_root()
        except Exception:
            return None

    def _confirm(self, message: str, detail: str, confirm_label: str) -> bool:
        """Показати діалог підтвердження й повернути True для підтвердження."""
        from gi.repository import Gtk

        dialog = Gtk.AlertDialog()
        dialog.set_message(message)
        dialog.set_detail(detail)
        dialog.set_buttons([tr("Cancel"), confirm_label])
        dialog.set_cancel_button(0)
        dialog.set_default_button(1)
        dialog.set_modal(True)
        try:
            response = dialog.choose(self._parent_window())
        except Exception:
            # Якщо в конкретній версії GTK діалог не спрацював, краще
            # не видаляти дані без явного підтвердження.
            return False
        return response == 1

    # ==================================================================
    # Завантаження, збереження та міграція
    # ==================================================================
    def _load_state(self) -> tuple[str | None, str]:
        """Прочитати id останньої нотатки та фільтр папки."""
        raw = self.ctx.storage.load(self.STATE_KEY, {})
        if not isinstance(raw, dict):
            return None, _ALL_NOTES
        selected = raw.get("selected_id")
        folder = raw.get("folder_filter")
        return (
            str(selected) if selected else None,
            str(folder) if folder is not None else _ALL_NOTES,
        )

    def _save_selected_id(self) -> None:
        """Запам'ятати відкриту нотатку та фільтр папки."""
        try:
            self.ctx.storage.save(
                self.STATE_KEY,
                {
                    "selected_id": self._selected_id,
                    "folder_filter": self._folder_filter,
                },
            )
        except OSError:
            # Це лише зручність, а не критичні дані.
            pass

    def _load_notes(self) -> None:
        """Завантажити нотатки з Markdown-файлів.

        Якщо папка notes порожня, спочатку намагаємося перенести старий
        ``notes.json``. Якщо й там нічого немає — створюємо вітальну нотатку.
        """
        self._store = NoteStore()
        notes = self._store.load()
        selected_id, folder_filter = self._load_state()
        self._selected_id = selected_id
        self._folder_filter = folder_filter
        legacy_selected_id = None

        if not notes:
            legacy_notes, legacy_selected_id = self._load_legacy_notes()
            if legacy_notes:
                # Переносимо кожну стару нотатку в окремий .md-файл.
                for note in legacy_notes:
                    try:
                        self._store.save(note)
                    except OSError:
                        # Одна пошкоджена нотатка не повинна заважати
                        # завантажити всі інші.
                        continue
                notes = self._store.load()

            if not notes:
                note = Note(
                    label="Welcome",
                    contents="Start writing here.\n\nUse + to add another note.",
                )
                self._store.save(note)
                notes = [note]

        if self._selected_id is None:
            self._selected_id = legacy_selected_id

        self._notes = notes
        self._notes_by_id = {note.id: note for note in notes}
        self._rebuild_notes_list()

        # Якщо збережений фільтр більше не існує, показуємо всі нотатки.
        folder_names = set(self._store.folders())
        if self._folder_filter not in {_ALL_NOTES, _UNFILED} and self._folder_filter not in folder_names:
            self._folder_filter = _ALL_NOTES

        self._refresh_folder_popover()
        self._refresh_tag_popover()
        self._refresh_visibility()

        # Якщо вибрана папка порожня, показуємо всі нотатки: інакше
        # редактор відкрив би прихований запис.
        if self._first_visible_row() is None:
            self._folder_filter = _ALL_NOTES
            self._refresh_folder_button()
            self._refresh_visibility()

        # Відкриваємо збережену нотатку, а якщо вона прихована фільтром —
        # першу видиму.
        row = self._visible_row_for_id(self._selected_id)
        if row is None:
            row = self._first_visible_row()
        if row is None and self._notes:
            row = self._rows_by_id.get(self._notes[0].id)
        if row is not None:
            self._notes_list.select_row(row)
            note = self._notes_by_id.get(self._row_ids.get(row, ""))
            if note is not None:
                self._show_note(note)
        self._save_selected_id()

    def _load_legacy_notes(self) -> tuple[list[Note], str | None]:
        """Прочитати старий формат ``notes.json`` (один JSON на всі нотатки)."""
        raw = self.ctx.storage.load("notes", {})

        if isinstance(raw, dict):
            items = raw.get("notes", [])
            selected_id = raw.get("selected_id")
        elif isinstance(raw, list):
            # Дуже стара версія могла зберігати просто список.
            items = raw
            selected_id = None
        else:
            return [], None

        if not isinstance(items, list):
            return [], selected_id

        notes: list[Note] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                notes.append(Note.from_dict(item))
            except Exception:
                continue
        return notes, selected_id

    def _rebuild_notes_list(self) -> None:
        """Повністю перебудувати рядки лівого списку."""
        # Прибираємо старі рядки. ListBox.remove прибирає один віджет.
        while True:
            child = self._notes_list.get_first_child()
            if child is None:
                break
            self._notes_list.remove(child)

        self._rows_by_id.clear()
        self._row_ids.clear()
        self._row_labels.clear()
        self._row_snippets.clear()
        self._row_pins.clear()
        self._row_tags.clear()
        self._row_folders.clear()

        for note in self._notes:
            row = self._make_note_row(note)
            self._rows_by_id[note.id] = row
            self._notes_list.append(row)

    def _save_note(self, note: Note | None) -> None:
        """Зберегти одну нотатку, показавши повідомлення про помилку."""
        if self._store is None or note is None:
            return
        try:
            self._store.save(note)
        except Exception as exc:
            self.ctx.notify(tr("Could not save note: {error}", error=exc))

    def _save_current_note(self) -> None:
        """Зберегти відкриту нотатку та запам'ятати вибір у списку."""
        if self._current_note is None:
            return
        self._save_note(self._current_note)
        self._save_selected_id()

    def _sync_current_note(self) -> None:
        """Скопіювати те, що зараз у редакторі, у Python-об'єкт Note.

        Зверни увагу: ми не використовуємо ``get_text()``, бо він не
        повертає картинки-якорі. Замість цього працює спеціальна
        ``_buffer_to_markdown()``.
        """
        if self._current_note is None:
            return

        self._current_note.contents = self._buffer_to_markdown()

        # Назва також є частиною нотатки.
        label = self._title_entry.get_text().strip() or "Untitled"
        if self._current_note.label != label:
            self._current_note.label = label
            title_label = self._row_labels.get(self._current_note.id)
            if title_label is not None:
                title_label.set_text(label)

        # Теги вводяться одним рядком через кому.
        self._current_note.tags = self._parse_tags(self._tag_entry.get_text())
        self._update_row_snippet(self._current_note)
        self._update_row_tags(self._current_note)

    def _schedule_save(self) -> None:
        """Запланувати збереження через SAVE_DELAY_MS мілісекунд."""
        from gi.repository import GLib

        # Якщо попередній таймер ще не спрацював, скасовуємо його.
        # Так швидке друкування не створює багато записів на диск.
        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(self.SAVE_DELAY_MS, self._flush_save)

    def _flush_save(self) -> bool:
        """Виконати відкладене збереження.

        Повертаємо False, щоб GLib не запускав цей таймер знову.
        """
        self._save_timer = None
        self._sync_current_note()
        self._save_current_note()
        self._update_note_stats()
        self._set_save_status(tr("Saved"))
        return False

    def save_state(self) -> None:
        """Примусово зберегти все перед закриттям або зміною модуля."""
        if getattr(self, "_body_view", None) is None:
            return

        from gi.repository import GLib

        if self._save_timer is not None:
            GLib.source_remove(self._save_timer)
            self._save_timer = None

        self._sync_current_note()
        self._save_current_note()

    def on_hide(self) -> None:
        """Викликається, коли користувач переходить в інший модуль."""
        self.save_state()

    def on_window_closed(self) -> None:
        """Викликається, коли вікно Omnify закривається в трей."""
        self.save_state()

    # ==================================================================
    # Список нотаток і текстові сигнали
    # ==================================================================
    def _make_note_row(self, note: Note):
        """Створити один рядок у лівому списку нотаток."""
        from gi.repository import Gtk, Pango

        row = Gtk.ListBoxRow()
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row_box.set_margin_top(8)
        row_box.set_margin_bottom(8)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pin_label = Gtk.Label(label="📌")
        pin_label.get_style_context().add_class("notes-row-pin")
        pin_label.set_visible(bool(note.pinned))
        title_row.append(pin_label)

        title_label = Gtk.Label(label=note.label or "Untitled", xalign=0.0)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_hexpand(True)
        title_row.append(title_label)
        row_box.append(title_row)

        snippet_label = Gtk.Label(xalign=0.0)
        snippet_label.get_style_context().add_class("notes-row-snippet")
        snippet_label.set_ellipsize(Pango.EllipsizeMode.END)
        row_box.append(snippet_label)

        tags_label = Gtk.Label(xalign=0.0)
        tags_label.get_style_context().add_class("notes-row-tags")
        tags_label.set_ellipsize(Pango.EllipsizeMode.END)
        row_box.append(tags_label)

        folder_label = Gtk.Label(xalign=0.0)
        folder_label.get_style_context().add_class("notes-row-folder")
        folder_label.set_ellipsize(Pango.EllipsizeMode.END)
        row_box.append(folder_label)

        row.set_child(row_box)

        # Запам'ятовуємо зв'язок між віджетом і нотаткою.
        self._row_ids[row] = note.id
        self._row_labels[note.id] = title_label
        self._row_snippets[note.id] = snippet_label
        self._row_pins[note.id] = pin_label
        self._row_tags[note.id] = tags_label
        self._row_folders[note.id] = folder_label
        self._update_row_snippet(note)
        self._update_row_tags(note)
        self._update_row_folder(note)
        return row

    def _parse_tags(self, text: str) -> list[str]:
        """Розібрати рядок ``робота, ідеї`` у список без повторів."""
        result: list[str] = []
        seen: set[str] = set()
        for part in text.split(","):
            tag = part.strip().lstrip("#").strip()
            key = tag.casefold()
            if tag and key not in seen:
                result.append(tag)
                seen.add(key)
        return result

    @staticmethod
    def _note_snippet(contents: str) -> str:
        """Взяти перший змістовний рядок нотатки для списку."""
        for line in contents.splitlines():
            line = line.strip()
            if not line or line.startswith("```") or line.startswith("!["):
                continue
            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^[-*+]\s+\[[ xX]\]\s*", "☐ ", line)
            line = re.sub(r"^[-*+]\s+", "", line)
            line = re.sub(r"^\d+[.)]\s+", "", line)
            line = re.sub(r"^>\s*", "", line)
            line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
            line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
            line = re.sub(r"[*_`~]", "", line).strip()
            if line:
                return line[:100]
        return ""

    def _update_row_snippet(self, note: Note) -> None:
        """Оновити короткий уривок тексту під назвою нотатки."""
        label = self._row_snippets.get(note.id)
        if label is None:
            return
        snippet = self._note_snippet(note.contents)
        label.set_text(snippet)
        label.set_visible(bool(snippet))

    def _update_row_tags(self, note: Note) -> None:
        """Оновити маленький рядок тегів у списку нотаток."""
        label = self._row_tags.get(note.id)
        if label is None:
            return
        label.set_text("   ".join(f"#{tag}" for tag in note.tags))
        label.set_visible(bool(note.tags))

    def _update_row_folder(self, note: Note) -> None:
        """Оновити підпис папки в рядку списку."""
        label = self._row_folders.get(note.id)
        if label is None:
            return
        label.set_text(f"📁 {note.folder}" if note.folder else "")
        label.set_visible(bool(note.folder))

    def _show_note(self, note: Note) -> None:
        """Показати нотатку в редакторі."""
        self._current_note = note
        self._selected_id = note.id
        self._loading = True
        try:
            self._load_note_into_buffer(note)
            self._title_entry.set_text(note.label or "Untitled")
            self._tag_entry.set_text(", ".join(note.tags))
            self._pin_button.set_active(bool(note.pinned))
            self._pin_button.set_tooltip_text(
                tr("Unpin note") if note.pinned else tr("Pin note")
            )
            self._note_folder_button.set_tooltip_text(
                tr("Move note to folder: {folder}", folder=note.folder or tr("Unfiled"))
            )
            self._refresh_note_folder_popover()
            if self._preview_toggle.get_active():
                self._refresh_preview()
        finally:
            # Навіть якщо сталася помилка, не залишаємо прапорець увімкненим.
            self._loading = False
        self._update_note_stats()
        self._set_save_status(tr("Saved"))
        self._notes_list.select_row(self._rows_by_id.get(note.id))

    def _load_note_into_buffer(self, note: Note) -> None:
        """Завантажити текст і картинки нотатки у Gtk.TextBuffer."""
        buffer = self._body_view.get_buffer()
        buffer.set_text("")
        self._image_markers.clear()

        # Розбиваємо Markdown на звичайний текст і посилання на картинки.
        cursor = 0
        for match in _IMAGE_LINK_RE.finditer(note.contents):
            # Усе, що стоїть перед картинкою, вставляємо звичайним текстом.
            before = note.contents[cursor:match.start()]
            if before:
                buffer.insert(buffer.get_end_iter(), before)

            relative_path = match.group(2)
            absolute_path = (
                self._store.resolve_asset(relative_path)
                if self._store is not None
                else None
            )

            if absolute_path is None or not absolute_path.is_file():
                # Файл картинки загубився — залишаємо Markdown-текст, щоб
                # користувач бачив, що саме було в нотатці.
                buffer.insert(buffer.get_end_iter(), match.group(0))
            else:
                # Картинка існує — показуємо її як справжній віджет.
                self._insert_image_anchor(
                    relative_path=relative_path,
                    absolute_path=absolute_path,
                    marker=match.group(0),
                )

            cursor = match.end()

        # Останній шматок тексту після останньої картинки.
        tail = note.contents[cursor:]
        if tail:
            buffer.insert(buffer.get_end_iter(), tail)

    def _refresh_visibility(self) -> None:
        """Показати лише нотатки, які відповідають папці та пошуку."""
        for note in self._notes:
            row = self._rows_by_id.get(note.id)
            if row is None:
                continue
            folder_ok = (
                self._folder_filter == _ALL_NOTES
                or note.folder == self._folder_filter
            )
            row.set_visible(folder_ok and self._matches_search(note))

    def _matches_search(self, note: Note) -> bool:
        """Перевірити одну нотатку на відповідність пошуковому запиту."""
        query = self._search_query
        if not query:
            return True
        if query.startswith("#"):
            tag_query = query[1:]
            return any(tag_query in tag.casefold() for tag in note.tags)
        return (
            query in note.label.casefold()
            or query in note.contents.casefold()
            or any(query in tag.casefold() for tag in note.tags)
        )

    def _visible_row_for_id(self, note_id: str | None):
        """Знайти рядок нотатки, якщо він проходить поточний фільтр."""
        if not note_id:
            return None
        row = self._rows_by_id.get(note_id)
        if row is not None and row.get_visible():
            return row
        return None

    def _first_visible_row(self):
        """Повернути перший видимий рядок списку."""
        for note in self._notes:
            row = self._rows_by_id.get(note.id)
            if row is not None and row.get_visible():
                return row
        return None

    def _on_note_selected(self, _listbox, row) -> None:
        """Користувач вибрав іншу нотатку в списку."""
        if row is None or self._loading:
            return

        note_id = self._row_ids.get(row)
        note = self._notes_by_id.get(note_id)
        if note is None or note is self._current_note:
            return

        # Перед перемиканням зберігаємо те, що вже встигли набрати в
        # попередній нотатці. Інакше останні 900 мс могли б загубитися.
        previous = self._current_note
        self._sync_current_note()
        self._save_note(previous)

        self._show_note(note)
        self._save_selected_id()
        self._schedule_save()

    def _on_new_note_clicked(self, _button) -> None:
        """Створити нову порожню нотатку."""
        previous = self._current_note
        self._sync_current_note()
        self._save_note(previous)

        # Якщо користувач дивиться конкретну папку — нова нотатка
        # створюється саме там. "Усі нотатки" означає корінь notes/.
        folder = self._folder_filter
        if folder in {_ALL_NOTES, _UNFILED}:
            folder = _UNFILED

        note = Note(label="Untitled", folder=folder)
        self._notes.insert(0, note)
        self._notes_by_id[note.id] = note
        self._save_note(note)

        row = self._make_note_row(note)
        self._rows_by_id[note.id] = row
        self._notes_list.prepend(row)

        # Якщо був активний пошук, нова нотатка могла б бути невидимою.
        if self._search_query:
            self._search_entry.set_text("")

        self._refresh_visibility()
        self._notes_list.select_row(row)

        # Зазвичай select_row сам викликає _on_note_selected. Ця перевірка
        # потрібна лише для випадку, коли сигнал чомусь не спрацював.
        if self._current_note is not note:
            self._show_note(note)

        self._title_entry.grab_focus()
        self._save_selected_id()
        self._schedule_save()

    def _on_duplicate_note(self) -> None:
        """Створити незалежну копію відкритої нотатки."""
        if self._current_note is None or self._store is None:
            return

        self._sync_current_note()
        self._save_current_note()
        try:
            clone = self._store.duplicate_note(self._current_note)
        except Exception as exc:
            self.ctx.notify(tr("Could not duplicate note: {error}", error=exc))
            return

        # Якщо був активний пошук, копія може бути прихована. Показуємо
        # весь список, щоб користувач одразу побачив новий рядок.
        if self._search_query:
            self._search_entry.set_text("")
        self._reload_notes_from_store(clone.id)
        self.ctx.notify(tr("Note duplicated"))

    @staticmethod
    def _format_version_time(path: Path | None) -> str:
        """Показати час створення знімка у зрозумілому форматі."""
        if path is None:
            return ""
        stem = path.stem.split("-", 1)[0]
        try:
            stamp = datetime.strptime(stem, "%Y%m%dT%H%M%S%fZ")
        except ValueError:
            try:
                stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError:
                return path.stem
        return (
            stamp.replace(tzinfo=timezone.utc)
            .astimezone()
            .strftime("%d.%m.%Y %H:%M:%S")
        )

    def _on_save_version(self) -> None:
        """Створити ручний знімок поточної нотатки."""
        if self._current_note is None or self._store is None:
            return
        self._sync_current_note()
        self._save_current_note()
        try:
            path = self._store.create_version(self._current_note, force=True)
        except Exception as exc:
            self.ctx.notify(tr("Could not save version: {error}", error=exc))
            return
        if path is None:
            self.ctx.notify(tr("Could not save version"))
            return
        self.ctx.notify(tr("Version saved"))

    def _on_open_history(self) -> None:
        """Показати історію версій поточної нотатки з preview та відкатом."""
        from gi.repository import Gtk, Pango

        if self._store is None or self._current_note is None:
            return
        existing = getattr(self, "_history_dialog", None)
        if existing is not None:
            existing.present()
            return

        self._sync_current_note()
        self._save_current_note()
        note = self._current_note
        if note is None:
            return

        window = Gtk.Window(title=tr("Version history"))
        window.set_transient_for(self._parent_window())
        window.set_modal(True)
        window.set_destroy_with_parent(True)
        window.set_default_size(820, 560)
        window.connect(
            "destroy",
            lambda _window: setattr(self, "_history_dialog", None),
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=tr("Version history"), xalign=0.0)
        title.set_hexpand(True)
        title.get_style_context().add_class("omnify-card-title")
        header.append(title)
        header.append(
            Gtk.Label(
                label=note.label,
                xalign=1.0,
                ellipsize=Pango.EllipsizeMode.END,
            )
        )
        outer.append(header)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(300)
        paned.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left.set_size_request(260, -1)
        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_vexpand(True)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        left_scroll.set_child(listbox)
        left.append(left_scroll)
        paned.set_start_child(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_margin_start(12)
        info = Gtk.Label(label=tr("No saved versions yet."), xalign=0.0)
        info.set_ellipsize(Pango.EllipsizeMode.END)
        info.get_style_context().add_class("omnify-dim")
        right.append(info)

        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_vexpand(True)
        preview = Gtk.TextView()
        preview.set_editable(False)
        preview.set_cursor_visible(False)
        preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        preview.set_left_margin(10)
        preview.set_right_margin(10)
        preview.set_top_margin(8)
        preview.set_bottom_margin(8)
        preview.get_style_context().add_class("omnify-mono")
        preview_scroll.set_child(preview)
        right.append(preview_scroll)
        paned.set_end_child(right)
        outer.append(paned)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_button = Gtk.Button(label=tr("Save version"))
        clear_button = Gtk.Button(label=tr("Clear history"))
        clear_button.set_sensitive(False)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        restore_button = Gtk.Button(label=tr("Restore version"))
        restore_button.set_sensitive(False)
        delete_button = Gtk.Button(label=tr("Delete version"))
        delete_button.set_sensitive(False)
        close_button = Gtk.Button(label=tr("Close"))
        for button in (
            save_button,
            clear_button,
            restore_button,
            delete_button,
            close_button,
        ):
            button.get_style_context().add_class("notes-folder-button")
        footer.append(save_button)
        footer.append(clear_button)
        footer.append(spacer)
        footer.append(restore_button)
        footer.append(delete_button)
        footer.append(close_button)
        outer.append(footer)
        window.set_child(outer)

        preview_buffer = preview.get_buffer()
        selected_version: list[Note | None] = [None]
        row_versions: dict[Any, Note] = {}

        def show_version(version: Note) -> None:
            selected_version[0] = version
            restore_button.set_sensitive(True)
            delete_button.set_sensitive(True)
            timestamp = self._format_version_time(version.path)
            info.set_text(f"{timestamp} · {version.label}")
            preview_buffer.set_text(version.contents)

        def refresh(select_path: Path | None = None) -> None:
            while True:
                child = listbox.get_first_child()
                if child is None:
                    break
                listbox.remove(child)
            row_versions.clear()
            selected_version[0] = None
            restore_button.set_sensitive(False)
            delete_button.set_sensitive(False)
            versions = self._store.versions(note)
            clear_button.set_sensitive(bool(versions))
            if not versions:
                empty_row = Gtk.ListBoxRow()
                empty_row.set_selectable(False)
                empty = Gtk.Label(
                    label=tr("No saved versions yet."),
                    xalign=0.0,
                )
                empty.set_margin_top(12)
                empty.set_margin_bottom(12)
                empty.set_margin_start(8)
                empty.get_style_context().add_class("omnify-dim")
                empty_row.set_child(empty)
                listbox.append(empty_row)
                info.set_text(tr("No saved versions yet."))
                preview_buffer.set_text("")
                return

            target_row = None
            for version in versions:
                row = Gtk.ListBoxRow()
                row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                row_box.set_margin_top(8)
                row_box.set_margin_bottom(8)
                row_box.set_margin_start(8)
                row_box.set_margin_end(8)
                time_label = Gtk.Label(
                    label=self._format_version_time(version.path),
                    xalign=0.0,
                )
                time_label.get_style_context().add_class("omnify-dim")
                row_box.append(time_label)
                snippet = Gtk.Label(
                    label=self._note_snippet(version.contents) or tr("Empty note"),
                    xalign=0.0,
                )
                snippet.set_ellipsize(Pango.EllipsizeMode.END)
                row_box.append(snippet)
                row.set_child(row_box)
                row_versions[row] = version
                listbox.append(row)
                if select_path is not None and version.path == select_path:
                    target_row = row

            if target_row is None:
                target_row = listbox.get_first_child()
            if target_row is not None:
                listbox.select_row(target_row)

        def on_selected(_listbox, row) -> None:
            version = row_versions.get(row)
            if version is not None:
                show_version(version)

        def save_version(_button) -> None:
            try:
                path = self._store.create_version(note, force=True)
            except Exception as exc:
                self.ctx.notify(tr("Could not save version: {error}", error=exc))
                return
            if path is None:
                self.ctx.notify(tr("Could not save version"))
                return
            refresh(path)
            self.ctx.notify(tr("Version saved"))

        def restore_version(_button) -> None:
            version = selected_version[0]
            if version is None:
                return
            if not self._confirm(
                tr("Restore version?"),
                tr(
                    "The current note will be saved to history first, then this "
                    "version will be restored."
                ),
                tr("Restore version"),
            ):
                return
            try:
                self._store.restore_version(note, version)
            except Exception as exc:
                self.ctx.notify(tr("Could not restore version: {error}", error=exc))
                return
            self._reload_notes_from_store(note.id)
            self.ctx.notify(tr("Version restored"))
            window.close()

        def delete_version(_button) -> None:
            version = selected_version[0]
            if version is None:
                return
            if not self._confirm(
                tr("Delete version?"),
                tr("This saved version will be deleted permanently."),
                tr("Delete version"),
            ):
                return
            try:
                self._store.delete_version(version)
            except Exception as exc:
                self.ctx.notify(tr("Could not delete version: {error}", error=exc))
                return
            refresh()
            self.ctx.notify(tr("Version deleted"))

        def clear_history(_button) -> None:
            if not self._confirm(
                tr("Clear history?"),
                tr(
                    "All saved versions of this note will be deleted. "
                    "This cannot be undone."
                ),
                tr("Clear history"),
            ):
                return
            try:
                count = self._store.clear_history(note)
            except Exception as exc:
                self.ctx.notify(tr("Could not clear history: {error}", error=exc))
                return
            refresh()
            self.ctx.notify(tr("History cleared: {count}", count=count))

        listbox.connect("row-selected", on_selected)
        save_button.connect("clicked", save_version)
        restore_button.connect("clicked", restore_version)
        delete_button.connect("clicked", delete_version)
        clear_button.connect("clicked", clear_history)
        close_button.connect("clicked", lambda _button: window.close())

        refresh()
        self._history_dialog = window
        window.present()

    def _on_title_changed(self, entry) -> None:
        """Користувач змінив назву нотатки."""
        if self._loading or self._current_note is None:
            return

        label = entry.get_text().strip() or "Untitled"
        self._current_note.label = label

        # Оновлюємо підпис у списку без перебудови всього списку.
        title_label = self._row_labels.get(self._current_note.id)
        if title_label is not None:
            title_label.set_text(label)

        if self._preview_toggle.get_active():
            self._refresh_preview()
        self._set_save_status(tr("Saving…"))
        self._schedule_save()

    def _on_pin_note_toggled(self, button) -> None:
        """Закріпити або відкріпити поточну нотатку."""
        if self._loading or self._current_note is None:
            return

        note = self._current_note
        self._sync_current_note()
        note.pinned = bool(button.get_active())
        self._save_current_note()
        self.ctx.notify(tr("Note pinned") if note.pinned else tr("Note unpinned"))

        # Після зміни прапорця список треба пересортувати: закріплені
        # нотатки завжди мають бути зверху.
        self._reload_notes_from_store(note.id)

    def _on_tags_changed(self, entry) -> None:
        """Користувач змінив теги через кому."""
        if self._loading or self._current_note is None:
            return
        self._current_note.tags = self._parse_tags(entry.get_text())
        self._update_row_tags(self._current_note)
        self._refresh_visibility()
        self._refresh_tag_popover()
        if self._preview_toggle.get_active():
            self._refresh_preview()
        self._set_save_status(tr("Saving…"))
        self._schedule_save()

    def _on_body_changed(self, _buffer) -> None:
        """Користувач щось змінив у тексті або вставив картинку."""
        if self._loading or self._current_note is None:
            return
        self._set_save_status(tr("Saving…"))
        self._update_note_stats()
        self._schedule_save()

    @staticmethod
    def _count_words(text: str) -> int:
        """Порахувати слова приблизно, ігноруючи Markdown-розмітку.

        Це саме допоміжний показник, а не точний лінгвістичний підрахунок:
        посилання, картинки та символи форматування не повинні роздувати
        цифру.
        """
        plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)
        plain = re.sub(r"https?://\S+", " ", plain)
        plain = re.sub(r"[#>*_`~\-]+", " ", plain)
        return len(re.findall(r"\w+", plain, flags=re.UNICODE))

    def _update_note_stats(self) -> None:
        """Оновити підпис із кількістю слів і символів."""
        label = getattr(self, "_stats_label", None)
        if label is None:
            return
        if self._current_note is None or getattr(self, "_body_view", None) is None:
            label.set_text(tr("0 words · 0 characters"))
            return
        buffer = self._body_view.get_buffer()
        text = buffer.get_text(
            buffer.get_start_iter(),
            buffer.get_end_iter(),
            True,
        )
        label.set_text(
            tr(
                "{words} words · {chars} characters",
                words=self._count_words(text),
                chars=len(text),
            )
        )

    def _set_save_status(self, status: str) -> None:
        """Показати, чи нотатка вже записана на диск."""
        label = getattr(self, "_save_status_label", None)
        if label is not None:
            label.set_text(status)

    def _on_search_changed(self, entry) -> None:
        """Показати лише нотатки, які містять пошуковий запит."""
        self._search_query = entry.get_text().strip().casefold()
        self._refresh_visibility()
        self._refresh_tag_popover()

    def _insert_markdown_snippet(self, snippet: str) -> None:
        """Вставити Markdown-шаблон у позицію курсора."""
        if self._current_note is None:
            return
        from gi.repository import Gtk

        if self._preview_toggle.get_active():
            self._preview_toggle.set_active(False)

        buffer = self._body_view.get_buffer()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            buffer.delete(start, end)

        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        prefix = "" if cursor.starts_line() else "\n"
        buffer.insert(cursor, prefix + snippet)
        end_iter = buffer.get_iter_at_mark(buffer.get_insert())
        buffer.place_cursor(end_iter)
        self._body_view.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)
        self._body_view.grab_focus()
        self._schedule_save()

    def _format_selection(self, action: str) -> None:
        """Застосувати просте Markdown-форматування до виділення.

        Для жирного/курсиву/закреслення/коду текст обгортається з двох
        боків. Для заголовка, списку та цитати префікс додається на
        початок кожного вибраного рядка. Це маленький редактор, а не
        повноцінний WYSIWYG: у файлі завжди залишається звичайний Markdown.
        """
        if self._current_note is None:
            return
        if self._preview_toggle.get_active():
            self._preview_toggle.set_active(False)

        buffer = self._body_view.get_buffer()
        has_selection = buffer.get_has_selection()
        start = end = None
        if has_selection:
            start, end = buffer.get_selection_bounds()

        wrappers = {
            "bold": ("**", "**", "text"),
            "italic": ("*", "*", "text"),
            "strike": ("~~", "~~", "text"),
            "code": ("`", "`", "code"),
            "link": ("[", "](https://)", "text"),
        }
        line_prefixes = {
            "heading": "# ",
            "bullet": "- ",
            "numbered": "1. ",
            "quote": "> ",
        }

        buffer.begin_user_action()
        try:
            if action in wrappers:
                left, right, placeholder = wrappers[action]
                if has_selection and start is not None and end is not None:
                    selected = buffer.get_text(start, end, True)
                    buffer.delete(start, end)
                    buffer.insert_at_cursor(left + selected + right)
                    text_end = buffer.get_iter_at_mark(buffer.get_insert())
                    text_end.backward_chars(len(right))
                    text_start = text_end.copy()
                    text_start.backward_chars(len(selected))
                    if action == "link":
                        # Після посилання зручно одразу замінити URL.
                        url_end = buffer.get_iter_at_mark(buffer.get_insert())
                        url_end.backward_chars(1)  # перед символом ")"
                        url_start = url_end.copy()
                        url_start.backward_chars(len("https://"))
                        buffer.select_range(url_start, url_end)
                    else:
                        buffer.select_range(text_start, text_end)
                else:
                    buffer.insert_at_cursor(left + placeholder + right)
                    text_end = buffer.get_iter_at_mark(buffer.get_insert())
                    text_end.backward_chars(len(right))
                    text_start = text_end.copy()
                    text_start.backward_chars(len(placeholder))
                    buffer.select_range(text_start, text_end)
            elif action in line_prefixes:
                prefix = line_prefixes[action]
                if has_selection and start is not None and end is not None:
                    selected = buffer.get_text(start, end, True)
                    formatted = "\n".join(
                        prefix + line if line.strip() else line
                        for line in selected.split("\n")
                    )
                    buffer.delete(start, end)
                    buffer.insert_at_cursor(formatted)
                else:
                    cursor = buffer.get_iter_at_mark(buffer.get_insert())
                    line_start = buffer.get_iter_at_line(cursor.get_line())
                    line_end = line_start.copy()
                    line_end.forward_to_line_end()
                    line_text = buffer.get_text(line_start, line_end, True)
                    if line_text.startswith(prefix):
                        # Повторне натискання прибирає форматування.
                        remove_end = line_start.copy()
                        remove_end.forward_chars(len(prefix))
                        buffer.delete(line_start, remove_end)
                    else:
                        buffer.insert(line_start, prefix)
            else:
                return
        finally:
            buffer.end_user_action()

        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        self._body_view.scroll_to_iter(cursor, 0.0, False, 0.0, 0.0)
        self._body_view.grab_focus()
        self._schedule_save()

    # ==================================================================
    # Папки
    # ==================================================================
    def _folder_label(self, folder: str) -> str:
        """Людський підпис для значення фільтра папки."""
        if folder == _ALL_NOTES:
            return tr("All notes")
        if folder == _UNFILED:
            return tr("Unfiled")
        return f"📁 {folder}"

    def _refresh_folder_button(self) -> None:
        """Оновити підпис кнопки-фільтра папок."""
        self._folder_button.set_label(self._folder_label(self._folder_filter))

    def _refresh_folder_popover(self) -> None:
        """Перебудувати список папок у лівій панелі."""
        from gi.repository import Gtk

        box = self._folder_popover_box
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)

        folders = self._store.folders() if self._store is not None else []
        entries = [(_ALL_NOTES, tr("All notes")), (_UNFILED, tr("Unfiled"))]
        entries.extend((folder, f"📁 {folder}") for folder in folders)

        for value, label in entries:
            marker = "✓ " if value == self._folder_filter else ""
            button = Gtk.Button(label=marker + label)
            button.set_halign(Gtk.Align.FILL)
            button.get_style_context().add_class("notes-menu-item")
            button.connect(
                "clicked",
                lambda _button, folder=value: self._on_folder_filter_selected(folder),
            )
            box.append(button)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        new_label = Gtk.Label(label=tr("New folder"), xalign=0.0)
        new_label.get_style_context().add_class("omnify-dim")
        box.append(new_label)

        folder_entry = Gtk.Entry()
        folder_entry.set_placeholder_text(tr("Folder name"))
        box.append(folder_entry)

        create_button = Gtk.Button(label=tr("Create folder"))
        create_button.connect(
            "clicked",
            lambda _button: self._create_folder_from_entry(folder_entry),
        )
        box.append(create_button)

        delete_button = Gtk.Button(label=tr("Delete current folder"))
        delete_button.set_sensitive(
            self._folder_filter not in {_ALL_NOTES, _UNFILED}
        )
        delete_button.connect("clicked", self._on_delete_folder_clicked)
        box.append(delete_button)

        # Перейменування доступне лише для справжньої папки, а не для
        # віртуальних фільтрів "Усі нотатки"/"Без папки".
        if self._folder_filter not in {_ALL_NOTES, _UNFILED}:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            rename_label = Gtk.Label(label=tr("Rename folder"), xalign=0.0)
            rename_label.get_style_context().add_class("omnify-dim")
            box.append(rename_label)

            rename_entry = Gtk.Entry()
            rename_entry.set_text(self._folder_filter)
            rename_entry.set_placeholder_text(tr("New folder name"))
            box.append(rename_entry)

            rename_button = Gtk.Button(label=tr("Rename"))
            rename_button.connect(
                "clicked",
                lambda _button, entry=rename_entry: self._rename_folder_from_entry(entry),
            )
            box.append(rename_button)

        self._refresh_folder_button()

    def _tag_counts(self) -> list[tuple[str, int]]:
        """Порахувати нотатки для кожного тегу (без урахування регістру)."""
        counts: dict[str, list] = {}
        for note in self._notes:
            seen: set[str] = set()
            for tag in note.tags:
                key = tag.casefold()
                if key in seen:
                    continue
                seen.add(key)
                if key not in counts:
                    counts[key] = [tag, 0]
                counts[key][1] += 1
        return [
            (str(display), int(count))
            for _key, (display, count) in sorted(
                counts.items(),
                key=lambda item: item[0].casefold(),
            )
        ]

    def _active_tag(self) -> str:
        """Повернути тег із пошуку, якщо він починається з #."""
        query = self._search_query.strip()
        return query[1:].strip() if query.startswith("#") else ""

    def _refresh_tag_button(self) -> None:
        """Показати активний тег на кнопці або загальний підпис."""
        button = getattr(self, "_tag_button", None)
        if button is None:
            return
        active = self._active_tag()
        button.set_label(f"#{active}" if active else tr("Tags"))

    def _refresh_tag_popover(self) -> None:
        """Перебудувати меню тегів з актуальними назвами й кількістю."""
        from gi.repository import Gtk

        box = getattr(self, "_tag_popover_box", None)
        if box is None:
            return
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)

        active = self._active_tag().casefold()
        all_button_label = tr("Clear tag filter") if active else tr("All tags")
        all_button = Gtk.Button(label=all_button_label)
        all_button.set_halign(Gtk.Align.FILL)
        all_button.get_style_context().add_class("notes-menu-item")
        all_button.connect("clicked", lambda _button: self._clear_tag_filter())
        box.append(all_button)

        counts = self._tag_counts()
        self._refresh_tag_completion(counts)
        if not counts:
            empty = Gtk.Label(label=tr("No tags yet"), xalign=0.0)
            empty.get_style_context().add_class("omnify-dim")
            box.append(empty)
        else:
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            for tag, count in counts:
                marker = "✓ " if tag.casefold() == active else ""
                button = Gtk.Button(label=f"{marker}#{tag}  ({count})")
                button.set_halign(Gtk.Align.FILL)
                button.get_style_context().add_class("notes-menu-item")
                button.connect(
                    "clicked",
                    lambda _button, value=tag: self._filter_by_tag(value),
                )
                box.append(button)

        self._refresh_tag_button()

    def _refresh_tag_completion(self, counts: list[tuple[str, int]] | None = None) -> None:
        """Оновити підказки для поля тегів."""
        model = getattr(self, "_tag_completion_model", None)
        if model is None:
            return
        values = counts if counts is not None else self._tag_counts()
        model.clear()
        for tag, _count in values:
            model.append([tag])

    def _filter_by_tag(self, tag: str) -> None:
        """Підставити #тег у поле пошуку й закрити меню."""
        query = f"#{tag}"
        # Оновлюємо стан одразу, не чекаючи відкладеного search-changed
        # від Gtk.SearchEntry.
        self._search_query = query.casefold()
        self._search_entry.set_text(query)
        self._refresh_visibility()
        self._refresh_tag_popover()
        popover = getattr(self, "_tag_popover", None)
        if popover is not None:
            popover.popdown()

    def _clear_tag_filter(self) -> None:
        """Прибрати теговий фільтр, не змінюючи фільтр папок."""
        self._search_query = ""
        self._search_entry.set_text("")
        self._refresh_visibility()
        self._refresh_tag_popover()
        popover = getattr(self, "_tag_popover", None)
        if popover is not None:
            popover.popdown()

    def _on_folder_filter_selected(self, folder: str) -> None:
        """Перемкнути видиму папку."""
        self._folder_filter = folder
        self._refresh_folder_button()
        self._refresh_visibility()
        self._refresh_folder_popover()
        self._folder_popover.popdown()
        # Якщо поточна нотатка не належить цій папці, відкриваємо першу видиму.
        current_row = self._rows_by_id.get(self._current_note.id) if self._current_note else None
        if current_row is not None and not current_row.get_visible():
            row = self._first_visible_row()
            if row is not None:
                self._notes_list.select_row(row)
        self._save_selected_id()

    def _create_folder_from_entry(self, entry) -> None:
        """Створити папку з тексту, введеного в popover."""
        if self._store is None:
            return
        name = entry.get_text().strip()
        if not name:
            return
        try:
            folder = self._store.create_folder(name)
        except Exception as exc:
            self.ctx.notify(tr("Could not create folder: {error}", error=exc))
            return
        entry.set_text("")
        self._folder_filter = folder
        self._refresh_folder_popover()
        self._refresh_visibility()
        self._folder_popover.popdown()
        self.ctx.notify(tr("Folder created: {name}", name=folder))
        self._save_selected_id()

    def _rename_folder_from_entry(self, entry) -> None:
        """Перейменувати вибрану папку разом з її нотатками."""
        if self._store is None or self._folder_filter in {_ALL_NOTES, _UNFILED}:
            return
        old_folder = self._folder_filter
        new_name = entry.get_text().strip()
        if not new_name or new_name == old_folder:
            return

        self._sync_current_note()
        self._save_current_note()
        try:
            moved = self._store.rename_folder(old_folder, new_name)
            new_folder = self._store._safe_folder(new_name)
        except Exception as exc:
            self.ctx.notify(tr("Could not rename folder: {error}", error=exc))
            return

        self._folder_filter = new_folder
        self._reload_notes_from_store(self._selected_id)
        self.ctx.notify(tr("Folder renamed. Moved notes: {count}", count=moved))

    def _on_delete_folder_clicked(self, _button) -> None:
        """Видалити поточну папку, перенісши нотатки в Unfiled."""
        if self._store is None or self._folder_filter in {_ALL_NOTES, _UNFILED}:
            return
        folder = self._folder_filter
        if not self._confirm(
            tr("Delete folder?"),
            tr("Notes in this folder will be moved to Unfiled. The folder itself will be removed."),
            tr("Delete folder"),
        ):
            return

        self._sync_current_note()
        self._save_current_note()
        try:
            moved = self._store.delete_folder(folder)
        except Exception as exc:
            self.ctx.notify(tr("Could not delete folder: {error}", error=exc))
            return

        self._folder_filter = _UNFILED
        self._reload_notes_from_store(self._selected_id)
        self.ctx.notify(
            tr("Folder deleted. Moved notes: {count}", count=moved)
        )

    def _move_current_note_to(self, folder: str) -> None:
        """Перемістити відкриту нотатку в іншу папку."""
        if self._current_note is None or self._store is None:
            return
        self._sync_current_note()
        try:
            moved_to = self._store.move_note(self._current_note, folder)
        except Exception as exc:
            self.ctx.notify(tr("Could not move note: {error}", error=exc))
            return

        self._note_folder_button.set_tooltip_text(
            tr("Move note to folder: {folder}", folder=moved_to or tr("Unfiled"))
        )
        self._update_row_folder(self._current_note)
        self._refresh_note_folder_popover()
        self._refresh_visibility()
        self._note_folder_popover.popdown()
        self.ctx.notify(
            tr("Note moved to: {folder}", folder=moved_to or tr("Unfiled"))
        )
        # Якщо поточна папка-фільтр більше не містить цю нотатку,
        # відкриваємо першу видиму, щоб редактор не показував "схований" запис.
        current_row = self._rows_by_id.get(self._current_note.id)
        if current_row is not None and not current_row.get_visible():
            first = self._first_visible_row()
            if first is not None:
                self._notes_list.select_row(first)
        if self._preview_toggle.get_active():
            self._refresh_preview()
        self._save_selected_id()

    def _refresh_note_folder_popover(self) -> None:
        """Перебудувати меню переміщення поточної нотатки."""
        from gi.repository import Gtk

        box = self._note_folder_popover_box
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)

        if self._current_note is None:
            return

        folders = self._store.folders() if self._store is not None else []
        current = self._current_note.folder
        entries = [(_UNFILED, tr("Unfiled"))]
        entries.extend((folder, f"📁 {folder}") for folder in folders)
        for value, label in entries:
            marker = "✓ " if value == current else ""
            button = Gtk.Button(label=marker + label)
            button.set_halign(Gtk.Align.FILL)
            button.get_style_context().add_class("notes-menu-item")
            button.connect(
                "clicked",
                lambda _button, folder=value: self._move_current_note_to(folder),
            )
            box.append(button)

    def _reload_notes_from_store(self, preserve_id: str | None = None) -> None:
        """Перечитати файли після масових змін (наприклад, папки).

        Важливо: перед викликом цього методу поточну нотатку вже треба
        зберегти. Після масового переміщення файлів старий Python-об'єкт
        ``Note`` може вказувати на неіснуючий шлях. Тому ми "забуваємо"
        його, щоб випадково не відтворити старий файл під час вибору рядка.
        """
        if self._store is None:
            return
        self._current_note = None
        notes = self._store.load()
        folder_names = set(self._store.folders())
        if (
            self._folder_filter not in {_ALL_NOTES, _UNFILED}
            and self._folder_filter not in folder_names
        ):
            self._folder_filter = _ALL_NOTES
        self._notes = notes
        self._notes_by_id = {note.id: note for note in notes}
        self._rebuild_notes_list()
        self._refresh_folder_popover()
        self._refresh_tag_popover()
        self._refresh_visibility()
        row = self._visible_row_for_id(preserve_id)
        if row is None:
            row = self._first_visible_row()
        if row is not None:
            self._notes_list.select_row(row)
            note = self._notes_by_id.get(self._row_ids.get(row, ""))
            if note is not None:
                self._show_note(note)
        self._save_selected_id()

    # ==================================================================
    # Preview та Markdown-інструменти
    # ==================================================================
    def _refresh_preview(self) -> None:
        """Перебудувати віджети попереднього перегляду."""
        if self._current_note is None or self._store is None:
            return
        host = self._preview_host
        while True:
            child = host.get_first_child()
            if child is None:
                break
            host.remove(child)
        host.append(
            build_preview(
                self._current_note,
                self._store,
                on_checklist_toggle=self._on_preview_checklist_toggled,
            )
        )

    def _on_preview_checklist_toggled(self, index: int, checked: bool) -> None:
        """Змінити ``- [ ]`` на ``- [x]`` (і навпаки) прямо з preview."""
        if self._current_note is None:
            return
        lines = self._current_note.contents.splitlines(keepends=True)
        counter = 0
        changed = False
        for line_number, line in enumerate(lines):
            # Відокремлюємо \n, щоб не загубити перенесення рядка під час
            # заміни позначки в чеклісті.
            stripped = line.rstrip("\r\n")
            ending = line[len(stripped):]
            match = re.match(r"^(\s*[-*+]\s+\[)([ xX])(\].*)$", stripped)
            if match is None:
                continue
            if counter == index:
                mark = "x" if checked else " "
                lines[line_number] = (
                    match.group(1) + mark + match.group(3) + ending
                )
                changed = True
                break
            counter += 1

        if not changed:
            return

        self._current_note.contents = "".join(lines)
        # Оновлюємо і текстовий буфер, щоб після вимкнення preview
        # користувач побачив ту саму позначку.
        self._loading = True
        try:
            self._load_note_into_buffer(self._current_note)
        finally:
            self._loading = False
        self._schedule_save()

    def _on_preview_toggled(self, button) -> None:
        """Перемкнути режим редагування та preview."""
        if self._current_note is None:
            button.set_active(False)
            return
        if button.get_active():
            self._sync_current_note()
            self._refresh_preview()
            self._editor_stack.set_visible_child_name("preview")
        else:
            self._editor_stack.set_visible_child_name("edit")
            self._body_view.grab_focus()

    # ==================================================================
    # Картинки: показ, вибір файлу та вставлення через Ctrl+V
    # ==================================================================
    def _make_picture(self, absolute_path: Path):
        """Створити віджет картинки для показу всередині тексту."""
        from gi.repository import Gtk

        picture = Gtk.Picture.new_for_filename(str(absolute_path))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_size_request(320, 200)
        picture.set_visible(True)
        picture.set_halign(Gtk.Align.START)
        picture.set_margin_top(6)
        picture.set_margin_bottom(6)
        picture.get_style_context().add_class("notes-inline-image")
        return picture

    def _insert_image_anchor(
        self,
        relative_path: str,
        absolute_path: Path,
        marker: str,
        at_iter=None,
    ):
        """Вставити картинку в буфер і запам'ятати її Markdown-текст.

        ``marker`` — це рядок, який піде у файл, наприклад
        ``![photo](assets/id/photo.png)``.
        """
        buffer = self._body_view.get_buffer()
        if at_iter is None:
            at_iter = buffer.get_end_iter()

        # create_child_anchor створює в тексті спеціальний "якір".
        anchor = buffer.create_child_anchor(at_iter)
        self._image_markers[anchor] = marker

        picture = self._make_picture(absolute_path)
        self._body_view.add_child_at_anchor(picture, anchor)
        return anchor

    def _buffer_to_markdown(self) -> str:
        """Перетворити вміст редактора назад у Markdown.

        GTK не повертає картинки через ``get_text()``. Тому ми йдемо по
        буферу символ за символом. Коли зустрічаємо якір, додаємо
        збережений Markdown-рядок. Усі звичайні символи додаємо як є.
        """
        buffer = self._body_view.get_buffer()
        iterator = buffer.get_start_iter()
        chunks: list[str] = []

        while not iterator.is_end():
            anchor = iterator.get_child_anchor()
            if anchor is not None:
                marker = self._image_markers.get(anchor)
                if marker is not None:
                    chunks.append(marker)
                iterator.forward_char()
                continue

            chunks.append(iterator.get_char())
            iterator.forward_char()

        return "".join(chunks)

    def _on_insert_image_clicked(self, _button) -> None:
        """Відкрити системний діалог вибору картинки."""
        if self._current_note is None:
            return
        if self._preview_toggle.get_active():
            self._preview_toggle.set_active(False)

        from gi.repository import Gtk

        dialog = Gtk.FileChooserNative(
            title=tr("Insert image"),
            action=Gtk.FileChooserAction.OPEN,
        )

        image_filter = Gtk.FileFilter()
        image_filter.set_name(tr("Image files"))
        for mime_type in (
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/gif",
            "image/svg+xml",
        ):
            image_filter.add_mime_type(mime_type)
        dialog.add_filter(image_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name(tr("All files"))
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        dialog.connect("response", self._on_image_chosen, dialog)
        self._image_dialog = dialog
        dialog.show()

    def _on_image_chosen(self, _dialog, response, native) -> None:
        """Обробити результат діалогу вибору файлу."""
        from gi.repository import Gtk

        self._image_dialog = None
        if response != Gtk.ResponseType.ACCEPT:
            native.destroy()
            return

        file = native.get_file()
        native.destroy()
        if file is None:
            return

        path = file.get_path()
        if not path:
            self.ctx.notify(tr("Choose an image from a local folder"))
            return
        self._insert_image(Path(path))

    def _on_body_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        """Перехопити Ctrl+V, якщо в буфері обміну лежить картинка.

        Повертаємо True, якщо подію оброблено. Тоді GTK не буде вставляти
        картинку як звичайний текст. Якщо картинки немає — повертаємо False,
        і працює стандартне вставлення тексту.
        """
        from gi.repository import Gdk

        is_v = keyval in (Gdk.KEY_v, Gdk.KEY_V)
        has_control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        has_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Швидкі клавіші форматування. Працюють лише без Shift, щоб не
        # перехоплювати системні комбінації на кшталт Ctrl+Shift+V.
        if has_control and not has_shift:
            lowered = Gdk.keyval_to_lower(keyval)
            if lowered == Gdk.KEY_s and self._current_note is not None:
                # Ctrl+S зберігає негайно, не чекаючи 900 мс.
                self.save_state()
                self._set_save_status(tr("Saved"))
                return True
            if lowered == Gdk.KEY_f:
                self._search_entry.grab_focus()
                self._search_entry.select_region(0, -1)
                return True

        if has_control and not has_shift and self._current_note is not None:
            shortcuts = {
                Gdk.KEY_b: "bold",
                Gdk.KEY_i: "italic",
                Gdk.KEY_k: "link",
            }
            action = shortcuts.get(Gdk.keyval_to_lower(keyval))
            if action is not None:
                self._format_selection(action)
                return True

        # Ctrl+Shift+V зазвичай означає "вставити як звичайний текст",
        # тому не забираємо цю комбінацію в себе.
        if not (is_v and has_control) or has_shift:
            return False
        if self._current_note is None:
            return False

        clipboard = self._body_view.get_clipboard()
        formats = clipboard.get_formats()

        # Gdk.Texture — це картинка, яку GTK уміє читати з буфера.
        # На практиці clipboard може містити не сам Gdk.Texture, а його
        # підтип (наприклад, GdkMemoryTexture). Тому перевіряємо всі типи
        # через is_a(), а не тільки точну рівність типів.
        has_image = (
            formats.contain_mime_type("image/png")
            or formats.contain_mime_type("image/jpeg")
            or formats.contain_mime_type("image/webp")
        )
        try:
            texture_type = Gdk.Texture.__gtype__
            for gtype in formats.get_gtypes() or []:
                if gtype.is_a(texture_type):
                    has_image = True
                    break
        except Exception:
            pass
        if not has_image:
            return False

        # Запам'ятовуємо нотатку: поки читаємо буфер, користувач може
        # встигнути перемкнутися на іншу.
        self._paste_note_id = self._current_note.id
        clipboard.read_texture_async(None, self._on_clipboard_texture, None)

        # True говорить GTK: "я вже обробив Ctrl+V, не вставляй текст".
        return True

    def _on_clipboard_texture(self, clipboard, result, _user_data) -> None:
        """Отримати картинку з буфера після асинхронного читання."""
        if self._current_note is None:
            return
        if self._current_note.id != self._paste_note_id:
            # Користувач уже перейшов в іншу нотатку — не вставляємо.
            return

        try:
            texture = clipboard.read_texture_finish(result)
        except Exception as exc:
            self.ctx.notify(tr("Could not insert image: {error}", error=exc))
            return
        if texture is None:
            return

        # Спочатку зберігаємо картинку в тимчасовий PNG, бо наш NoteStore
        # працює з файлами. Потім _insert_image скопіює її в assets.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pasted-image.png"
            if not texture.save_to_png(str(path)):
                self.ctx.notify(tr("Could not insert image: {error}", error="PNG"))
                return
            self._insert_image(path, alt_text="pasted image")

    def _insert_image(self, source: Path, alt_text: str | None = None) -> None:
        """Скопіювати картинку в assets і вставити її у відкриту нотатку."""
        if self._current_note is None or self._store is None:
            return
        if self._preview_toggle.get_active():
            self._preview_toggle.set_active(False)

        try:
            relative_path = self._store.import_image(self._current_note, source)
        except Exception as exc:
            self.ctx.notify(tr("Could not insert image: {error}", error=exc))
            return

        absolute_path = self._store.resolve_asset(relative_path)
        if absolute_path is None:
            self.ctx.notify(tr("Could not insert image: {error}", error=relative_path))
            return

        alt = (alt_text or source.stem).replace("[", "").replace("]", "").strip()
        if not alt:
            alt = "image"
        marker = f"![{alt}]({relative_path})"

        buffer = self._body_view.get_buffer()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            buffer.delete(start, end)

        # Вставляємо картинку саме туди, де зараз курсор.
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        anchor = self._insert_image_anchor(
            relative_path=relative_path,
            absolute_path=absolute_path,
            marker=marker,
            at_iter=cursor,
        )

        # Після картинки ставимо курсор, щоб можна було одразу писати далі.
        after = buffer.get_iter_at_child_anchor(anchor)
        after.forward_char()
        buffer.place_cursor(after)

        self._schedule_save()
        self.ctx.notify(
            tr("Image inserted: {name}", name=Path(relative_path).name)
        )

    # ==================================================================
    # Експорт, резервні копії та синхронізація
    # ==================================================================
    def _default_export_name(self, suffix: str) -> str:
        """Створити безпечну назву файлу для експорту."""
        label = self._current_note.label if self._current_note else "note"
        safe = re.sub(r"[^\w .-]+", "-", label, flags=re.UNICODE).strip(" .-")
        return f"{safe or 'note'}.{suffix}"

    def _choose_export_path(self, suffix: str, callback) -> None:
        """Показати діалог збереження й викликати callback(шлях)."""
        from gi.repository import Gtk

        if self._current_note is None:
            return

        dialog = Gtk.FileChooserNative(
            title=tr("Export note"),
            action=Gtk.FileChooserAction.SAVE,
            transient_for=self._parent_window(),
        )
        dialog.set_current_name(self._default_export_name(suffix))

        file_filter = Gtk.FileFilter()
        if suffix == "pdf":
            file_filter.set_name(tr("PDF files"))
            file_filter.add_mime_type("application/pdf")
            file_filter.add_pattern("*.pdf")
        else:
            file_filter.set_name(tr("HTML files"))
            file_filter.add_mime_type("text/html")
            file_filter.add_pattern("*.html")
        dialog.add_filter(file_filter)

        def on_response(native, response):
            self._export_dialog = None
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None:
                return
            path = Path(file.get_path())
            if path.suffix.lower() != f".{suffix}":
                path = path.with_suffix(f".{suffix}")
            callback(path)

        dialog.connect("response", on_response)
        self._export_dialog = dialog
        dialog.show()

    def _on_import_markdown(self) -> None:
        """Імпортувати один або кілька зовнішніх Markdown-файлів."""
        from gi.repository import Gtk

        if self._store is None:
            return
        self._sync_current_note()
        self._save_current_note()

        dialog = Gtk.FileChooserNative(
            title=tr("Import Markdown files"),
            action=Gtk.FileChooserAction.OPEN,
            transient_for=self._parent_window(),
        )
        dialog.set_select_multiple(True)
        markdown_filter = Gtk.FileFilter()
        markdown_filter.set_name(tr("Markdown files"))
        markdown_filter.add_pattern("*.md")
        markdown_filter.add_pattern("*.markdown")
        markdown_filter.add_mime_type("text/markdown")
        dialog.add_filter(markdown_filter)

        folder = (
            self._folder_filter
            if self._folder_filter not in {_ALL_NOTES, _UNFILED}
            else ""
        )

        def on_response(native, response):
            self._import_dialog = None
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            files = native.get_files() or []
            native.destroy()
            imported = 0
            last_error = None
            for file in files:
                path = file.get_path() if file is not None else None
                if not path:
                    continue
                try:
                    self._store.import_markdown(path, folder=folder)
                    imported += 1
                except Exception as exc:
                    last_error = exc
            if imported:
                self._reload_notes_from_store(self._selected_id)
                self.ctx.notify(
                    tr("Imported Markdown files: {count}", count=imported)
                )
            elif last_error is not None:
                self.ctx.notify(
                    tr("Could not import Markdown: {error}", error=last_error)
                )

        dialog.connect("response", on_response)
        self._import_dialog = dialog
        dialog.show()

    def _on_export_html(self) -> None:
        """Експортувати відкриту нотатку в HTML."""
        if self._current_note is None or self._store is None:
            return
        self._sync_current_note()

        def run(path: Path) -> None:
            try:
                export_html(self._current_note, self._store, path)
            except Exception as exc:
                self.ctx.notify(tr("Could not export note: {error}", error=exc))
                return
            self.ctx.notify(tr("Exported: {path}", path=path))

        self._choose_export_path("html", run)

    def _on_export_pdf(self) -> None:
        """Експортувати відкриту нотатку в PDF."""
        if self._current_note is None or self._store is None:
            return
        self._sync_current_note()

        def run(path: Path) -> None:
            try:
                export_pdf(self._current_note, self._store, path)
            except Exception as exc:
                self.ctx.notify(tr("Could not export note: {error}", error=exc))
                return
            self.ctx.notify(tr("Exported: {path}", path=path))

        self._choose_export_path("pdf", run)

    def _on_export_all_html(self) -> None:
        """Експортувати всі нотатки в теку з index.html."""
        from gi.repository import Gtk

        if self._store is None:
            return
        self._sync_current_note()
        self._save_current_note()
        notes = self._store.load()

        dialog = Gtk.FileChooserNative(
            title=tr("Choose export folder"),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self._parent_window(),
        )

        def on_response(native, response):
            self._export_all_dialog = None
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None or not file.get_path():
                return
            target = Path(file.get_path()).expanduser()
            try:
                if target.resolve() == self._store.root or target.resolve().is_relative_to(
                    self._store.root
                ):
                    raise ValueError("Export folder cannot be inside notes")
                _index_path, count = export_bundle_html(notes, self._store, target)
            except Exception as exc:
                self.ctx.notify(tr("Could not export notes: {error}", error=exc))
                return
            self.ctx.notify(
                tr("Exported notes: {count} to {path}", count=count, path=target)
            )

        dialog.connect("response", on_response)
        self._export_all_dialog = dialog
        dialog.show()

    def _on_backup_notes(self) -> None:
        """Створити ZIP-архів усіх нотаток."""
        if self._store is None:
            return
        self._sync_current_note()
        try:
            path = self._store.create_backup()
        except Exception as exc:
            self.ctx.notify(tr("Could not create backup: {error}", error=exc))
            return
        self.ctx.notify(tr("Backup created: {path}", path=path))

    def _on_restore_notes(self) -> None:
        """Відновити нотатки з ZIP-архіву, створивши страхувальну копію."""
        from gi.repository import Gtk

        if self._store is None:
            return
        self._sync_current_note()
        self._save_current_note()

        dialog = Gtk.FileChooserNative(
            title=tr("Restore backup"),
            action=Gtk.FileChooserAction.OPEN,
            transient_for=self._parent_window(),
        )
        archive_filter = Gtk.FileFilter()
        archive_filter.set_name(tr("ZIP archives"))
        archive_filter.add_mime_type("application/zip")
        archive_filter.add_pattern("*.zip")
        dialog.add_filter(archive_filter)

        def on_response(native, response):
            self._restore_dialog = None
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None or not file.get_path():
                return
            archive_path = Path(file.get_path())
            if not self._confirm(
                tr("Restore backup?"),
                tr("Notes from the backup will be merged with current notes. A safety copy will be created first."),
                tr("Restore backup"),
            ):
                return

            try:
                self._store.create_backup()
                restored = self._store.restore_backup(archive_path)
            except Exception as exc:
                self.ctx.notify(tr("Could not restore backup: {error}", error=exc))
                return

            self._reload_notes_from_store(self._selected_id)
            self.ctx.notify(
                tr("Restored notes: {count}", count=restored)
            )

        dialog.connect("response", on_response)
        self._restore_dialog = dialog
        dialog.show()

    def _on_sync_notes(self) -> None:
        """Скопіювати теку notes в обрану папку."""
        from gi.repository import Gtk

        if self._store is None:
            return
        self._sync_current_note()

        dialog = Gtk.FileChooserNative(
            title=tr("Choose sync folder"),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self._parent_window(),
        )

        def on_response(native, response):
            self._sync_dialog = None
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None:
                return
            target = file.get_path()
            if not target:
                return
            try:
                self._store.sync_to(target)
            except Exception as exc:
                self.ctx.notify(tr("Could not sync notes: {error}", error=exc))
                return
            self.ctx.notify(tr("Notes synced to: {path}", path=target))

        dialog.connect("response", on_response)
        self._sync_dialog = dialog
        dialog.show()

    def _on_two_way_sync_notes(self) -> None:
        """Об'єднати нотатки з іншою текою в обидва боки."""
        from gi.repository import Gtk

        if self._store is None:
            return
        self._sync_current_note()

        dialog = Gtk.FileChooserNative(
            title=tr("Choose two-way sync folder"),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self._parent_window(),
        )

        def on_response(native, response):
            self._sync_dialog = None
            if response != Gtk.ResponseType.ACCEPT:
                native.destroy()
                return
            file = native.get_file()
            native.destroy()
            if file is None:
                return
            target = file.get_path()
            if not target:
                return
            self._sync_current_note()
            self._save_current_note()
            try:
                # Перед об'єднанням створюємо страхувальну копію. Якщо її
                # не вдалося зробити, краще не ризикувати даними.
                self._store.create_backup(default_backup_dir())
                imported, updated, exported = self._store.sync_two_way(target)
            except Exception as exc:
                self.ctx.notify(tr("Could not sync notes: {error}", error=exc))
                return
            self._reload_notes_from_store(self._selected_id)
            self.ctx.notify(
                tr(
                    "Notes merged. Imported: {imported}, updated: {updated}, exported: {exported}",
                    imported=imported,
                    updated=updated,
                    exported=exported,
                )
            )

        dialog.connect("response", on_response)
        self._sync_dialog = dialog
        dialog.show()

    def _maybe_auto_backup(self) -> None:
        """Створити резервну копію, якщо свіжої ще немає.

        Це захищає від випадкового видалення файлів. Автоматичні копії
        лежать поза текою notes, тому не заважають списку нотаток.
        """
        if self._store is None:
            return
        try:
            backup_dir = default_backup_dir()
            backups = sorted(backup_dir.glob("notes-*.zip"))
            if backups:
                newest = max(path.stat().st_mtime for path in backups)
                if time.time() - newest < 24 * 60 * 60:
                    return
            self._store.create_backup(backup_dir)
        except Exception:
            # Автоматична копія не повинна заважати запуску застосунку.
            pass

    def _on_delete_note_clicked(self) -> None:
        """Перемістити відкриту нотатку в Trash після підтвердження."""
        if self._current_note is None or self._store is None:
            return
        note = self._current_note
        self._sync_current_note()
        self._save_current_note()
        if not self._confirm(
            tr("Move note to Trash?"),
            tr("The note and its images will be moved to Trash. You can restore it later."),
            tr("Move to Trash"),
        ):
            return

        try:
            self._store.trash_note(note)
        except Exception as exc:
            self.ctx.notify(tr("Could not move note to Trash: {error}", error=exc))
            return

        # Прибираємо нотатку з усіх словників і з лівого списку.
        self._notes = [item for item in self._notes if item.id != note.id]
        self._notes_by_id.pop(note.id, None)
        row = self._rows_by_id.pop(note.id, None)
        self._row_labels.pop(note.id, None)
        self._row_snippets.pop(note.id, None)
        self._row_pins.pop(note.id, None)
        self._row_tags.pop(note.id, None)
        self._row_folders.pop(note.id, None)
        if row is not None:
            self._row_ids.pop(row, None)
            self._notes_list.remove(row)

        self._current_note = None
        self._refresh_note_folder_popover()
        self._refresh_visibility()
        self._refresh_folders_after_delete()

        row = self._first_visible_row()
        if row is not None:
            self._notes_list.select_row(row)
            selected = self._notes_by_id.get(self._row_ids.get(row, ""))
            if selected is not None:
                self._show_note(selected)
        else:
            self._create_welcome_note()

        self._save_selected_id()
        self.ctx.notify(tr("Note moved to Trash"))

    def _on_open_trash(self) -> None:
        """Показати вікно Trash з відновленням і остаточним видаленням."""
        from gi.repository import Gtk, Pango

        if self._store is None:
            return
        existing = getattr(self, "_trash_dialog", None)
        if existing is not None:
            existing.present()
            return

        self._sync_current_note()
        self._save_current_note()

        window = Gtk.Window(title=tr("Trash"))
        window.set_transient_for(self._parent_window())
        window.set_modal(True)
        window.set_destroy_with_parent(True)
        window.set_default_size(560, 420)
        window.connect(
            "destroy",
            lambda _window: setattr(self, "_trash_dialog", None),
        )

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=tr("Trash"), xalign=0.0)
        title.set_hexpand(True)
        title.get_style_context().add_class("omnify-card-title")
        header.append(title)

        empty_button = Gtk.Button(label=tr("Empty Trash"))
        empty_button.get_style_context().add_class("notes-folder-button")
        header.append(empty_button)
        outer.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(listbox)
        outer.append(scroll)
        window.set_child(outer)

        def clear_list() -> None:
            while True:
                child = listbox.get_first_child()
                if child is None:
                    break
                listbox.remove(child)

        def refresh() -> None:
            clear_list()
            notes = self._store.trashed_notes()
            empty_button.set_sensitive(bool(notes))
            if not notes:
                row = Gtk.ListBoxRow()
                label = Gtk.Label(label=tr("Trash is empty."), xalign=0.0)
                label.get_style_context().add_class("omnify-dim")
                label.set_margin_top(12)
                label.set_margin_bottom(12)
                label.set_margin_start(8)
                row.set_child(label)
                listbox.append(row)
                return

            for note in notes:
                row = Gtk.ListBoxRow()
                row.set_selectable(False)
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row_box.set_margin_top(8)
                row_box.set_margin_bottom(8)
                row_box.set_margin_start(8)
                row_box.set_margin_end(8)

                text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                text_box.set_hexpand(True)
                title_label = Gtk.Label(label=note.label or "Untitled", xalign=0.0)
                title_label.set_ellipsize(Pango.EllipsizeMode.END)
                text_box.append(title_label)
                folder = note.folder or tr("Unfiled")
                details = Gtk.Label(
                    label=f"{folder} · {note.updated_at[:10]}",
                    xalign=0.0,
                )
                details.get_style_context().add_class("omnify-dim")
                text_box.append(details)
                row_box.append(text_box)

                restore_button = Gtk.Button(label=tr("Restore"))
                restore_button.get_style_context().add_class("notes-icon-button")

                def restore(_button, item=note) -> None:
                    try:
                        restored = self._store.restore_trashed(item)
                    except Exception as exc:
                        self.ctx.notify(
                            tr("Could not restore note: {error}", error=exc)
                        )
                        return
                    refresh()
                    self._reload_notes_from_store(restored.id)
                    self.ctx.notify(tr("Note restored"))
                    window.close()

                restore_button.connect("clicked", restore)
                row_box.append(restore_button)

                delete_button = Gtk.Button(label=tr("Delete permanently"))
                delete_button.get_style_context().add_class("notes-icon-button")

                def purge(_button, item=note) -> None:
                    if not self._confirm(
                        tr("Delete permanently?"),
                        tr("The note and its images will be deleted permanently. This cannot be undone."),
                        tr("Delete permanently"),
                    ):
                        return
                    try:
                        self._store.purge_trashed(item)
                    except Exception as exc:
                        self.ctx.notify(
                            tr("Could not delete note permanently: {error}", error=exc)
                        )
                        return
                    refresh()
                    self.ctx.notify(tr("Note permanently deleted"))

                delete_button.connect("clicked", purge)
                row_box.append(delete_button)
                row.set_child(row_box)
                listbox.append(row)

        def empty_trash(_button) -> None:
            if not self._confirm(
                tr("Empty Trash?"),
                tr("All notes in Trash will be deleted permanently. This cannot be undone."),
                tr("Empty Trash"),
            ):
                return
            try:
                count = self._store.empty_trash()
            except Exception as exc:
                self.ctx.notify(tr("Could not empty Trash: {error}", error=exc))
                return
            refresh()
            self.ctx.notify(tr("Trash emptied: {count}", count=count))

        empty_button.connect("clicked", empty_trash)
        refresh()
        self._trash_dialog = window
        window.present()

    def _refresh_folders_after_delete(self) -> None:
        """Оновити меню папок після видалення нотатки/папки."""
        if self._store is None:
            return
        folder_names = set(self._store.folders())
        if (
            self._folder_filter not in {_ALL_NOTES, _UNFILED}
            and self._folder_filter not in folder_names
        ):
            self._folder_filter = _ALL_NOTES
        self._refresh_folder_popover()
        self._refresh_visibility()

    def _create_welcome_note(self) -> None:
        """Створити нотатку-заглушку, якщо список нотаток спорожнів."""
        if self._store is None:
            return
        note = Note(
            label="Welcome",
            contents="Start writing here.\n\nUse + to add another note.",
        )
        self._store.save(note)
        self._notes = [note]
        self._notes_by_id = {note.id: note}
        # Якщо користувач був у конкретній папці, вітальна нотатка в
        # корені була б прихована фільтром. Показуємо "Без папки".
        if self._folder_filter not in {_ALL_NOTES, _UNFILED}:
            self._folder_filter = _UNFILED
        row = self._make_note_row(note)
        self._rows_by_id[note.id] = row
        self._notes_list.append(row)
        self._refresh_folder_popover()
        self._refresh_visibility()
        self._notes_list.select_row(row)
        self._show_note(note)
        self._save_selected_id()
