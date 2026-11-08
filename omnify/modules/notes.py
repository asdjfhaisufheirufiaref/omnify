"""Модуль нотаток: Markdown-файли, картинки та простий редактор.

Цей файл — це "візуальна" частина нотаток. Він створює GTK-віджети,
реагує на натискання кнопок і показує картинки всередині тексту.
Робота з файлами винесена в ``notes_engine.py``, щоб логіку було легше
тестувати без графічного інтерфейсу.

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
from pathlib import Path

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .notes_engine import Note, NoteStore

# Регулярний вираз шукає Markdown-посилання на картинку в папці assets.
# Група 1 — підпис (alt), група 2 — шлях після дужки.
# Приклад: ![фото](assets/abc123/photo.png)
_IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\((assets/[^)\n]+)\)")


class NotesModule(BaseModule):
    """Модуль "Нотатки" для Omnify."""

    # Ці атрибути використовує головне вікно для меню та навігації.
    id = "notes"
    name = "Notes"
    icon = "📝"
    description = "Local notes and quick capture."
    group = "Tools"

    # У цьому JSON-файлі зберігається тільки id вибраної нотатки, а не її
    # текст. Сам текст завжди лежить у notes/*.md.
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
        # Словники в Python не можуть мати два однакові ключі, але різні
        # рядки — це різні об'єкти, тому такий варіант працює.
        self._row_ids: dict[Gtk.ListBoxRow, str] = {}

        # Підписи рядків, щоб оновлювати назву без перебудови всього списку.
        self._row_labels: dict[str, Gtk.Label] = {}

        # Нотатка, яка зараз відкрита в редакторі.
        self._current_note: Note | None = None

        # id нотатки, яку треба відкрити після запуску.
        self._selected_id: str | None = None

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
        root.set_position(260)
        root.set_resize_start_child(False)
        root.set_shrink_start_child(False)

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

        # Поле пошуку фільтрує список за назвою та текстом нотатки.
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

        # ------------------------------------------------------------------
        # Права панель: назва та текст нотатки
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

        # Кнопка вибору картинки з файлу. Ctrl+V працює і без неї.
        image_button = Gtk.Button(label="🖼")
        image_button.set_tooltip_text("Insert image (Ctrl+V)")
        image_button.get_style_context().add_class("notes-icon-button")
        image_button.connect("clicked", self._on_insert_image_clicked)
        editor_header.append(image_button)

        # Поки що декоративне меню. Його можна використати для майбутніх
        # функцій: експорту, видалення, тегів тощо.
        menu_button = Gtk.Button(label="⋮")
        menu_button.get_style_context().add_class("notes-icon-button")
        menu_button.set_tooltip_text("More actions")
        editor_header.append(menu_button)
        editor.append(editor_header)

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
        editor.append(body_scroll)
        root.set_end_child(editor)

        # Зберігаємо посилання на віджети, щоб інші методи могли їх знайти.
        self._notes_list = notes_list
        self._title_entry = title_entry
        self._body_view = body

        # Завантажуємо файли ДО підключення сигналів "changed" і
        # "row-selected": інакше саме завантаження виглядало б як
        # редагування користувача.
        self._load_notes()

        notes_list.connect("row-selected", self._on_note_selected)
        title_entry.connect("changed", self._on_title_changed)
        body.get_buffer().connect("changed", self._on_body_changed)

        return root

    # ==================================================================
    # Завантаження, збереження та міграція
    # ==================================================================
    def _load_notes(self) -> None:
        """Завантажити нотатки з Markdown-файлів.

        Якщо папка notes порожня, спочатку намагаємося перенести старий
        ``notes.json``. Якщо й там нічого немає — створюємо вітальну нотатку.
        """
        self._store = NoteStore()
        notes = self._store.load()
        selected_id = self._load_selected_id()
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

        if selected_id is None:
            selected_id = legacy_selected_id

        self._notes = notes
        self._notes_by_id = {note.id: note for note in notes}

        # Створюємо візуальний рядок для кожної нотатки.
        for note in notes:
            row = self._make_note_row(note)
            self._rows_by_id[note.id] = row
            self._notes_list.append(row)

        # Відкриваємо збережену нотатку, а якщо її немає — першу зі списку.
        row = self._rows_by_id.get(selected_id)
        if row is None:
            row = self._rows_by_id[notes[0].id]
        self._notes_list.select_row(row)
        self._show_note(self._notes_by_id[self._row_ids[row]])
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

    def _load_selected_id(self) -> str | None:
        """Прочитати id останньої відкритої нотатки зі стану застосунку."""
        raw = self.ctx.storage.load(self.STATE_KEY, {})
        if isinstance(raw, dict):
            value = raw.get("selected_id")
            return str(value) if value else None
        return None

    def _save_selected_id(self) -> None:
        """Запам'ятати, яку нотатку користувач відкрив останньою."""
        try:
            self.ctx.storage.save(
                self.STATE_KEY,
                {"selected_id": self._selected_id},
            )
        except OSError:
            # Це лише зручність, а не критичні дані.
            pass

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

        # Запам'ятовуємо зв'язок між віджетом і нотаткою.
        self._row_ids[row] = note.id
        self._row_labels[note.id] = title_label
        return row

    def _show_note(self, note: Note) -> None:
        """Показати нотатку в редакторі."""
        self._current_note = note
        self._selected_id = note.id
        self._loading = True
        try:
            self._load_note_into_buffer(note)
        finally:
            # Навіть якщо сталася помилка, не залишаємо прапорець увімкненим.
            self._loading = False

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

        note = Note()
        self._notes.append(note)
        self._notes_by_id[note.id] = note
        self._save_note(note)

        row = self._make_note_row(note)
        self._rows_by_id[note.id] = row
        self._notes_list.prepend(row)
        self._notes_list.select_row(row)

        # Зазвичай select_row сам викликає _on_note_selected. Ця перевірка
        # потрібна лише для випадку, коли сигнал чомусь не спрацював.
        if self._current_note is not note:
            self._show_note(note)

        self._title_entry.grab_focus()
        self._save_selected_id()
        self._schedule_save()

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

        self._schedule_save()

    def _on_body_changed(self, _buffer) -> None:
        """Користувач щось змінив у тексті або вставив картинку."""
        if self._loading or self._current_note is None:
            return
        self._schedule_save()

    def _on_search_changed(self, entry) -> None:
        """Показати лише нотатки, які містять пошуковий запит."""
        query = entry.get_text().strip().casefold()
        for note in self._notes:
            row = self._rows_by_id.get(note.id)
            if row is not None:
                row.set_visible(
                    not query
                    or query in note.label.casefold()
                    or query in note.contents.casefold()
                )

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
        from gi.repository import Gtk

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
