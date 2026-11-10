"""Побудова попереднього перегляду нотатки з GTK-віджетів.

Ми не використовуємо WebKit або інший браузерний рушій: у багатьох
дистрибутивах його немає. Замість цього Markdown розбирається на блоки,
а кожен блок стає звичайним GTK-віджетом:

* заголовок — ``Gtk.Label``;
* таблиця — ``Gtk.Grid``;
* картинка — ``Gtk.Picture``;
* чекліст — ``Gtk.CheckButton`` (у preview він лише показує стан).

Такий підхід легкий, не потребує інтернету й добре виглядає в темній
темі Omnify.
"""

from __future__ import annotations

import re

from . import notes_render


def _label(markup: str, css_class: str = "notes-preview-text", wrap: bool = True):
    """Створити підпис із Pango-розміткою та охайним відступом."""
    from gi.repository import Gtk, Pango

    label = Gtk.Label()
    label.set_markup(markup)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    if wrap:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.get_style_context().add_class(css_class)
    return label


def _image_by_target(alt: str, target: str, store):
    """Створити віджет картинки за шляхом із Markdown."""
    from gi.repository import Gtk

    absolute = store.resolve_asset(target) if store is not None else None
    if absolute is None or not absolute.is_file():
        return _label(
            f"[image: {alt or target}]",
            css_class="notes-preview-missing",
        )

    picture = Gtk.Picture.new_for_filename(str(absolute))
    picture.set_can_shrink(True)
    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
    picture.set_size_request(420, 240)
    picture.set_halign(Gtk.Align.START)
    picture.get_style_context().add_class("notes-inline-image")
    return picture


def _image_widget(block: notes_render.Block, store):
    """Створити віджет для окремого image-блоку."""
    return _image_by_target(block.alt, block.target, store)


def _paragraph_widget(text: str, store):
    """Показати абзац, розділяючи його на текст і inline-картинки."""
    from gi.repository import Gtk

    pattern = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
    matches = list(pattern.finditer(text))
    if not matches:
        return _label(notes_render.inline_pango(text))

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    cursor = 0
    for match in matches:
        before = text[cursor:match.start()].strip()
        if before:
            box.append(_label(notes_render.inline_pango(before)))
        box.append(_image_by_target(match.group(1), match.group(2), store))
        cursor = match.end()
    after = text[cursor:].strip()
    if after:
        box.append(_label(notes_render.inline_pango(after)))
    return box


def _table_widget(block: notes_render.Block):
    """Створити таблицю з рамками та перенесенням тексту в клітинках."""
    from gi.repository import Gtk, Pango

    grid = Gtk.Grid()
    grid.set_column_spacing(1)
    grid.set_row_spacing(1)
    grid.set_halign(Gtk.Align.START)
    grid.get_style_context().add_class("notes-preview-table")

    columns = max([len(block.headers)] + [len(row) for row in block.rows] or [0])
    if columns <= 0:
        return grid

    for row_number, values in enumerate([block.headers] + block.rows):
        for column in range(columns):
            value = values[column] if column < len(values) else ""
            label = Gtk.Label()
            label.set_markup(notes_render.inline_pango(value))
            label.set_xalign(0.0)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(28)
            label.set_size_request(120, -1)
            label.set_margin_top(5)
            label.set_margin_bottom(5)
            label.set_margin_start(7)
            label.set_margin_end(7)
            label.get_style_context().add_class(
                "notes-preview-cell-head" if row_number == 0 else "notes-preview-cell"
            )
            grid.attach(label, column, row_number, 1, 1)
    return grid


def build_preview(note, store, on_checklist_toggle=None):
    """Побудувати один великий вертикальний блок preview для нотатки.

    ``on_checklist_toggle`` — необов'язковий callback(index, checked).
    Якщо він переданий, чекбокси в preview можна натискати, і вони
    одразу змінюють Markdown-текст нотатки.
    """
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_margin_top(8)
    box.set_margin_bottom(16)
    box.set_margin_start(4)
    box.set_margin_end(16)
    box.set_valign(Gtk.Align.START)

    title_markup = notes_render.inline_pango(note.label or "Untitled")
    box.append(_label(f"<b>{title_markup}</b>", css_class="notes-preview-title"))

    if note.tags:
        tags = "   ".join(
            f"<span foreground='#7a7a7a'>#{notes_render.escape_html(tag)}</span>"
            for tag in note.tags
        )
        box.append(_label(tags, css_class="notes-preview-tags"))

    blocks = notes_render.parse_markdown(note.contents)
    if not blocks:
        box.append(
            _label(
                "This note is empty. Start writing in the editor.",
                css_class="notes-preview-muted",
            )
        )

    checklist_index = 0
    for block in blocks:
        if block.kind == "heading":
            # CSS-клас вибираємо за рівнем заголовка.
            css_class = f"notes-preview-h{min(max(block.level, 1), 6)}"
            box.append(
                _label(notes_render.inline_pango(block.text), css_class=css_class)
            )
        elif block.kind == "paragraph":
            box.append(_paragraph_widget(block.text, store))
        elif block.kind == "quote":
            quote = _label(
                notes_render.inline_pango(block.text),
                css_class="notes-preview-quote",
            )
            box.append(quote)
        elif block.kind in {"bullet", "ordered"}:
            prefix = "•" if block.kind == "bullet" else "1."
            box.append(
                _label(
                    f"{prefix}  {notes_render.inline_pango(block.text)}",
                    css_class="notes-preview-list",
                )
            )
        elif block.kind == "checklist":
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_top(2)
            row.set_margin_bottom(2)
            check = Gtk.CheckButton()
            check.set_active(block.checked)
            if on_checklist_toggle is None:
                check.set_sensitive(False)
            else:
                # set_active вище не викликає callback, бо ми підключаємо
                # сигнал після встановлення початкового стану.
                index = checklist_index
                check.connect(
                    "toggled",
                    lambda button, number=index: on_checklist_toggle(
                        number, button.get_active()
                    ),
                )
            row.append(check)
            row.append(_label(notes_render.inline_pango(block.text)))
            box.append(row)
            checklist_index += 1
        elif block.kind == "table":
            box.append(_table_widget(block))
        elif block.kind == "code":
            from gi.repository import Pango

            code = Gtk.Label()
            code.set_text(block.text)
            code.set_xalign(0.0)
            code.set_selectable(True)
            code.set_wrap(True)
            code.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            code.set_margin_top(8)
            code.set_margin_bottom(8)
            code.set_margin_start(10)
            code.set_margin_end(10)
            code.get_style_context().add_class("notes-preview-code")
            box.append(code)
        elif block.kind == "image":
            box.append(_image_widget(block, store))
        elif block.kind == "rule":
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            separator.set_margin_top(10)
            separator.set_margin_bottom(10)
            box.append(separator)

    return box


__all__ = ["build_preview"]
