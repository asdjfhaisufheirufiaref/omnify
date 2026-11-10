"""Перетворення Markdown у HTML, PDF і віджети попереднього перегляду.

Нотатки зберігаються як Markdown. Щоб показати їх "красиво", потрібен
невеликий перетворювач. Ми підтримуємо найважливіше для нотаток:

* заголовки ``#`` ... ``######``;
* абзаци, списки та чеклісти ``- [ ]`` / ``- [x]``;
* таблиці з вертикальними рисками;
* блоки коду в потрійних лапках;
* картинки ``![alt](assets/...)``;
* **жирний**, *курсив*, ``код`` і посилання.

Це не повний парсер Markdown на кшталт CommonMark, але він простий,
зрозумілий і не потребує зовнішніх бібліотек. HTML і PDF експортують
той самий набір можливостей, який показує попередній перегляд.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

# Регулярні вирази для розпізнавання блоків Markdown.
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_CHECKLIST_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\n]+)\)\s*$")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_FENCE_RE = re.compile(r"^\s*```([^\s`]*)\s*$")


@dataclass
class Block:
    """Один логічний блок Markdown.

    ``kind`` визначає тип: heading, paragraph, bullet, checklist, table,
    code, image, quote, rule або list. Решта полів використовуються лише
    тими типами, яким вони потрібні.
    """

    kind: str
    text: str = ""
    level: int = 0
    checked: bool = False
    language: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    alt: str = ""
    target: str = ""


def _normalise_lines(text: str) -> list[str]:
    """Розбити текст на рядки, прибравши Windows-символи ``\r``."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _split_table_row(line: str) -> list[str]:
    """Розібрати один рядок таблиці ``| A | B |`` у список клітинок."""
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    # \| всередині клітинки означає звичайну вертикальну риску.
    cells = re.split(r"(?<!\\)\|", value)
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _looks_like_table(lines: list[str], index: int) -> bool:
    """Перевірити, чи починається на рядку ``index`` Markdown-таблиця."""
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and bool(_TABLE_SEPARATOR_RE.match(lines[index + 1]))


def _starts_block(lines: list[str], index: int) -> bool:
    """Чи може рядок почати новий блок, а не продовжити абзац?"""
    line = lines[index]
    if not line.strip():
        return True
    if _FENCE_RE.match(line) or _HEADING_RE.match(line):
        return True
    if _CHECKLIST_RE.match(line) or _BULLET_RE.match(line) or _ORDERED_RE.match(line):
        return True
    if _IMAGE_RE.match(line) or line.strip() in {"---", "***", "___"}:
        return True
    return _looks_like_table(lines, index)


def parse_markdown(text: str) -> list[Block]:
    """Перетворити Markdown-текст у список блоків.

    Функція не змінює файл і не залежить від GTK. Завдяки цьому її легко
    тестувати та використовувати і для preview, і для експорту.
    """
    lines = _normalise_lines(text)
    blocks: list[Block] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        # Порожні рядки просто розділяють блоки.
        if not stripped:
            index += 1
            continue

        # --- Блок коду в потрійних лапках -------------------------------
        fence = _FENCE_RE.match(line)
        if fence:
            language = fence.group(1) or ""
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1  # пропускаємо закриваючі ```
            blocks.append(
                Block(kind="code", text="\n".join(code_lines), language=language)
            )
            continue

        # --- Заголовок ---------------------------------------------------
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(
                Block(
                    kind="heading",
                    level=len(heading.group(1)),
                    text=heading.group(2).strip(),
                )
            )
            index += 1
            continue

        # --- Горизонтальна лінія ----------------------------------------
        if stripped in {"---", "***", "___"}:
            blocks.append(Block(kind="rule"))
            index += 1
            continue

        # --- Таблиця -----------------------------------------------------
        if _looks_like_table(lines, index):
            headers = _split_table_row(lines[index])
            index += 2  # рядок заголовків + рядок ---
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_table_row(lines[index]))
                index += 1
            blocks.append(Block(kind="table", headers=headers, rows=rows))
            continue

        # --- Чекліст -----------------------------------------------------
        checklist = _CHECKLIST_RE.match(line)
        if checklist:
            blocks.append(
                Block(
                    kind="checklist",
                    checked=checklist.group(1).lower() == "x",
                    text=checklist.group(2).strip(),
                )
            )
            index += 1
            continue

        # --- Маркований список ------------------------------------------
        bullet = _BULLET_RE.match(line)
        if bullet:
            blocks.append(Block(kind="bullet", text=bullet.group(1).strip()))
            index += 1
            continue

        # --- Нумерований список -----------------------------------------
        ordered = _ORDERED_RE.match(line)
        if ordered:
            blocks.append(Block(kind="ordered", text=ordered.group(1).strip()))
            index += 1
            continue

        # --- Картинка окремим рядком ------------------------------------
        image = _IMAGE_RE.match(line)
        if image:
            blocks.append(
                Block(
                    kind="image",
                    alt=image.group(1).strip(),
                    target=image.group(2).strip(),
                )
            )
            index += 1
            continue

        # --- Цитата ------------------------------------------------------
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].lstrip())
                index += 1
            blocks.append(Block(kind="quote", text="\n".join(quote_lines)))
            continue

        # --- Звичайний абзац --------------------------------------------
        paragraph_lines = [line.strip()]
        index += 1
        while index < len(lines):
            if not lines[index].strip() or _starts_block(lines, index):
                break
            paragraph_lines.append(lines[index].strip())
            index += 1
        blocks.append(Block(kind="paragraph", text="\n".join(paragraph_lines)))

    return blocks


# ----------------------------------------------------------------------
# Inline-форматування
# ----------------------------------------------------------------------
def escape_html(text: str) -> str:
    """Безпечно вставити текст у HTML (публічна обгортка)."""
    return html.escape(text, quote=False)


# Стара внутрішня назва залишена для коротших викликів у цьому модулі.
_escape_html = escape_html


def _escape_attr(text: str) -> str:
    """Безпечно вставити значення в HTML-атрибут."""
    return html.escape(text, quote=True)


def _inline_html(text: str) -> str:
    """Перетворити **жирний**, *курсив*, `код` і посилання в HTML.

    Спочатку екрануємо небезпечні символи. Потім тимчасово ховаємо
    ``код``, щоб форматування всередині нього не спрацьовувало.
    """
    escaped = _escape_html(text)
    code_spans: list[str] = []

    def save_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    escaped = re.sub(r"`([^`\n]+)`", save_code, escaped)

    # Картинки та посилання. URL беремо з Markdown і екрануємо для HTML.
    escaped = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: (
            f'<img src="{_escape_attr(m.group(2))}" '
            f'alt="{_escape_attr(m.group(1))}" />'
        ),
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: (
            f'<a href="{_escape_attr(m.group(2))}">'
            f"{m.group(1)}</a>"
        ),
        escaped,
    )

    # **жирний** і *курсив*. Порядок важливий: спочатку подвійні зірочки.
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"~~([^~\n]+)~~", r"<del>\1</del>", escaped)

    # Повертаємо збережений код уже як безпечний HTML.
    for number, code in enumerate(code_spans):
        escaped = escaped.replace(
            f"\x00CODE{number}\x00",
            f"<code>{_escape_html(code)}</code>",
        )
    return escaped


def inline_pango(text: str) -> str:
    """Перетворити inline-форматування в розмітку Pango.

    Ця функція публічна, бо її також використовує ``notes_preview.py``.

    Pango не розуміє HTML, але вміє ``<b>``, ``<i>``, ``<tt>`` та
    ``<span>``. Текст обов'язково екрануємо, інакше символи ``<`` могли б
    зламати розмітку.
    """
    escaped = html.escape(text, quote=False)
    code_spans: list[str] = []

    def save_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    escaped = re.sub(r"`([^`\n]+)`", save_code, escaped)
    escaped = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: f"[{html.escape(m.group(1))}]",
        escaped,
    )
    # Pango не підтримує HTML-тег <a>, тому посилання показуємо як
    # підкреслений текст. Сам URL залишається в Markdown-файлі.
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        r'<span underline="single">\1</span>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", escaped)

    for number, code in enumerate(code_spans):
        escaped = escaped.replace(
            f"\x00CODE{number}\x00",
            f"<tt>{html.escape(code)}</tt>",
        )
    return escaped


# ----------------------------------------------------------------------
# HTML-експорт
# ----------------------------------------------------------------------
def _image_data_uri(target: str, store) -> str | None:
    """Прочитати картинку й повернути ``data:image/...;base64,...``.

    Так HTML-файл залишається автономним: його можна надіслати іншій
    людині, і картинки не загубляться.
    """
    if store is None:
        return None
    absolute = store.resolve_asset(target)
    if absolute is None or not absolute.is_file():
        return None
    mime, _ = mimetypes.guess_type(str(absolute))
    if not mime or not mime.startswith("image/"):
        mime = "application/octet-stream"
    payload = base64.b64encode(absolute.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _html_image(block: Block, store) -> str:
    """Створити HTML для картинки або зрозумілий текст, якщо файлу немає."""
    source = _image_data_uri(block.target, store) or _escape_attr(block.target)
    alt = _escape_attr(block.alt or "image")
    return f'<figure><img src="{source}" alt="{alt}" /></figure>'


def _html_table(block: Block) -> str:
    """Створити HTML-таблицю."""
    columns = max([len(block.headers)] + [len(row) for row in block.rows] or [0])
    if columns <= 0:
        return ""

    def row_html(values: list[str], header: bool = False) -> str:
        tag = "th" if header else "td"
        cells = []
        for number in range(columns):
            value = values[number] if number < len(values) else ""
            cells.append(f"<{tag}>{_inline_html(value)}</{tag}>")
        return "<tr>" + "".join(cells) + "</tr>"

    parts = ["<table><thead>", row_html(block.headers, True), "</thead><tbody>"]
    parts.extend(row_html(row) for row in block.rows)
    parts.append("</tbody></table>")
    return "".join(parts)


def _embed_html_images(body: str, store) -> str:
    """Вбудувати навіть ті картинки, які стоять усередині абзацу.

    Окремі картинки-блоки вже отримують data URI. Але користувач може
    вставити ``![alt](assets/...)`` посеред речення. Для автономного
    HTML-файлу замінюємо такі ``src`` на base64.
    """
    if store is None:
        return body

    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if target.startswith("data:"):
            return match.group(0)
        data = _image_data_uri(target, store)
        if data is None:
            return match.group(0)
        return f'src="{data}"'

    return re.sub(r'src="([^"]+)"', replace, body)


def note_to_html(note, store=None) -> str:
    """Створити повний standalone HTML-документ для нотатки."""
    blocks = parse_markdown(note.contents)
    body: list[str] = []

    if note.label:
        body.append(f"<h1>{_inline_html(note.label)}</h1>")
    if note.tags:
        tags = "".join(
            f'<span class="tag">#{_escape_html(tag)}</span>' for tag in note.tags
        )
        body.append(f'<p class="tags">{tags}</p>')

    for block in blocks:
        if block.kind == "heading":
            level = min(max(block.level, 1), 6)
            body.append(f"<h{level}>{_inline_html(block.text)}</h{level}>")
        elif block.kind == "paragraph":
            body.append(f"<p>{_inline_html(block.text)}</p>")
        elif block.kind == "quote":
            body.append(f"<blockquote>{_inline_html(block.text)}</blockquote>")
        elif block.kind == "bullet":
            body.append(f"<ul><li>{_inline_html(block.text)}</li></ul>")
        elif block.kind == "ordered":
            body.append(f"<ol><li>{_inline_html(block.text)}</li></ol>")
        elif block.kind == "checklist":
            checked = " checked" if block.checked else ""
            body.append(
                '<ul class="checklist"><li>'
                f'<input type="checkbox" disabled{checked}> '
                f"{_inline_html(block.text)}</li></ul>"
            )
        elif block.kind == "table":
            body.append(_html_table(block))
        elif block.kind == "code":
            language = _escape_attr(block.language)
            body.append(
                f'<pre><code class="language-{language}">'
                f"{_escape_html(block.text)}</code></pre>"
            )
        elif block.kind == "image":
            body.append(_html_image(block, store))
        elif block.kind == "rule":
            body.append("<hr>")

    title = _escape_html(note.label or "Note")
    article_html = _embed_html_images("".join(body), store)
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: system-ui, sans-serif; line-height: 1.55;
       max-width: 860px; margin: 40px auto; padding: 0 24px; color: #222; }}
h1, h2, h3, h4, h5, h6 {{ line-height: 1.2; margin: 1.4em 0 .5em; }}
h1 {{ font-size: 2em; }} h2 {{ font-size: 1.6em; }} h3 {{ font-size: 1.3em; }}
p {{ margin: .7em 0; white-space: pre-wrap; }}
ul, ol {{ padding-left: 1.7em; }}
.checklist {{ list-style: none; padding-left: .2em; }}
.tags {{ margin-top: -.4em; }}
.tag {{ display: inline-block; border: 1px solid #bbb; border-radius: 999px;
        padding: 1px 8px; margin: 2px 4px 2px 0; font-size: .85em; color: #555; }}
blockquote {{ margin: 1em 0; padding: .3em 1em; border-left: 4px solid #bbb; color: #555; }}
pre {{ background: #f2f2f2; padding: 12px; overflow: auto; border-radius: 8px; }}
code {{ font-family: ui-monospace, monospace; background: #f2f2f2; padding: 1px 4px; border-radius: 4px; }}
pre code {{ padding: 0; }}
table {{ border-collapse: collapse; margin: 1em 0; width: 100%; }}
th, td {{ border: 1px solid #bbb; padding: 6px 9px; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; }}
img {{ max-width: 100%; height: auto; border-radius: 6px; }}
figure {{ margin: 1em 0; }}
hr {{ border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }}
a {{ color: #2b6cb0; }}
</style>
</head>
<body>
<article>
{article_html}
</article>
</body>
</html>
"""


def export_html(note, store, destination: Path | str) -> Path:
    """Записати нотатку в HTML-файл і повернути шлях."""
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(note_to_html(note, store), encoding="utf-8")
    return destination


def _bundle_file_name(note) -> str:
    """Безпечне унікальне ім'я HTML-файлу для експорту всіх нотаток."""
    label = re.sub(r"[^\w .-]", "", note.label or "", flags=re.UNICODE)
    label = re.sub(r"\s+", "-", label).strip("-._")[:60] or "note"
    note_id = re.sub(r"[^0-9A-Za-z_-]", "", str(note.id))[:12] or "note"
    return f"{label}--{note_id}.html"


def export_bundle_html(notes: Iterable, store, destination: Path | str) -> tuple[Path, int]:
    """Експортувати всі нотатки в теку: окремі HTML-файли та index.html.

    Кожна сторінка автономна, а index.html містить зміст зі згрупованими
    нотатками. Картинки вбудовуються в HTML, тому теку можна надіслати
    або відкрити без папки notes.
    """
    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    notes = list(notes)
    entries: list[str] = []

    for note in notes:
        filename = _bundle_file_name(note)
        export_html(note, store, destination / filename)
        tags = ""
        if note.tags:
            tags = '<span class="entry-tags">' + "".join(
                f"#{_escape_html(tag)} " for tag in note.tags
            ) + "</span>"
        folder = (
            f'<span class="entry-folder">📁 {_escape_html(note.folder)}</span>'
            if note.folder
            else ""
        )
        entries.append(
            '<li class="entry">'
            f'<a href="{quote(filename)}">{_escape_html(note.label or "Untitled")}</a>'
            f"{tags}{folder}</li>"
        )

    if entries:
        listing = "<ul>" + "".join(entries) + "</ul>"
    else:
        listing = "<p>No notes yet.</p>"

    index = f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omnify Notes</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: system-ui, sans-serif; line-height: 1.5;
       max-width: 860px; margin: 40px auto; padding: 0 24px; }}
h1 {{ font-size: 2em; }}
ul {{ padding-left: 1.3em; }}
.entry {{ margin: .55em 0; }}
a {{ color: #2b6cb0; font-weight: 600; }}
.entry-tags {{ margin-left: .5em; color: #777; font-size: .85em; }}
.entry-folder {{ margin-left: .5em; color: #777; font-size: .85em; }}
</style>
</head>
<body>
<h1>Omnify Notes</h1>
<p>{len(notes)} notes</p>
{listing}
</body>
</html>
"""
    index_path = destination / "index.html"
    index_path.write_text(index, encoding="utf-8")
    return index_path, len(notes)


# ----------------------------------------------------------------------
# PDF-експорт
# ----------------------------------------------------------------------
# A4 у типографських пунктах. PDF використовує пункти (1/72 дюйма).
_PAGE_WIDTH = 595.0
_PAGE_HEIGHT = 842.0
_MARGIN = 50.0
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _MARGIN


class _PdfWriter:
    """Маленький "друкар" Markdown-блоків на PDF-сторінки.

    cairo малює лінії та картинки, а Pango — текст. Pango сам робить
    перенесення рядків, тому нам не потрібно вручну рахувати слова.
    """

    def __init__(self, context, store, title: str, tags: Iterable[str]) -> None:
        self.cr = context
        self.store = store
        self.y = _MARGIN
        self._first_page = True
        self._draw_title(title, list(tags))

    def _new_page(self) -> None:
        """Завершити поточну сторінку й почати нову."""
        self.cr.show_page()
        self.cr.set_source_rgb(0, 0, 0)
        self.y = _MARGIN

    def _ensure_space(self, height: float) -> None:
        """Якщо блок не влазить, перенести його на нову сторінку."""
        if self.y + height > _PAGE_HEIGHT - _MARGIN:
            self._new_page()

    def _layout(self, markup: str, size: float, width: float, mono: bool = False):
        """Створити Pango-layout із заданим шрифтом і шириною."""
        from gi.repository import Pango, PangoCairo

        layout = PangoCairo.create_layout(self.cr)
        layout.set_width(max(1, int(width * Pango.SCALE)))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        family = "monospace" if mono else "sans"
        font = Pango.FontDescription(f"{family} {size}")
        layout.set_font_description(font)
        layout.set_markup(markup, -1)
        return layout

    @staticmethod
    def _height(layout) -> float:
        """Висота layout у пунктах."""
        _ink, logical = layout.get_pixel_extents()
        return max(1.0, float(logical.height))

    def _draw_layout(self, layout, x: float, y: float) -> float:
        """Намалювати layout і повернути його висоту."""
        from gi.repository import PangoCairo

        self.cr.move_to(x, y)
        PangoCairo.show_layout(self.cr, layout)
        return self._height(layout)

    def _text(
        self,
        text: str,
        size: float = 11,
        bold: bool = False,
        indent: float = 0,
        spacing_after: float = 6,
        mono: bool = False,
        color: tuple[float, float, float] = (0, 0, 0),
        width: float | None = None,
    ) -> None:
        """Намалювати звичайний текстовий блок з перенесенням рядків."""
        markup = inline_pango(text)
        if bold:
            markup = f"<b>{markup}</b>"
        layout = self._layout(markup, size, width or (_CONTENT_WIDTH - indent), mono)
        height = self._height(layout)
        self._ensure_space(height + spacing_after)
        self.cr.set_source_rgb(*color)
        self._draw_layout(layout, _MARGIN + indent, self.y)
        self.y += height + spacing_after

    def _draw_title(self, title: str, tags: list[str]) -> None:
        """Намалювати назву нотатки та її теги."""
        layout = self._layout(
            f"<b>{inline_pango(title or 'Note')}</b>",
            23,
            _CONTENT_WIDTH,
        )
        height = self._height(layout)
        self._ensure_space(height + 8)
        self.cr.set_source_rgb(0, 0, 0)
        self._draw_layout(layout, _MARGIN, self.y)
        self.y += height + 4
        if tags:
            tag_text = "   ".join(f"#{tag}" for tag in tags)
            self._text(tag_text, size=9, color=(0.38, 0.38, 0.38), spacing_after=12)

    def draw_block(self, block: Block) -> None:
        """Намалювати один розібраний Markdown-блок."""
        if block.kind == "heading":
            sizes = {1: 20.0, 2: 17.0, 3: 14.0, 4: 12.5, 5: 11.5, 6: 11.0}
            size = sizes.get(block.level, 11.0)
            self.y += 7
            self._text(block.text, size=size, bold=True, spacing_after=8)
        elif block.kind == "paragraph":
            self._text(block.text, size=11, spacing_after=7)
        elif block.kind == "quote":
            self._text(block.text, size=10.5, indent=18, color=(0.35, 0.35, 0.35))
        elif block.kind in {"bullet", "ordered"}:
            prefix = "• " if block.kind == "bullet" else "1. "
            self._text(prefix + block.text, size=11, indent=14, spacing_after=3)
        elif block.kind == "checklist":
            prefix = "☑ " if block.checked else "☐ "
            self._text(prefix + block.text, size=11, indent=14, spacing_after=3)
        elif block.kind == "code":
            self._draw_code(block)
        elif block.kind == "table":
            self._draw_table(block)
        elif block.kind == "image":
            self._draw_image(block)
        elif block.kind == "rule":
            self._ensure_space(12)
            self.cr.set_source_rgb(0.78, 0.78, 0.78)
            self.cr.set_line_width(0.8)
            self.cr.move_to(_MARGIN, self.y + 3)
            self.cr.line_to(_PAGE_WIDTH - _MARGIN, self.y + 3)
            self.cr.stroke()
            self.y += 12

    def _draw_code(self, block: Block) -> None:
        """Намалювати блок коду на світлому фоні."""
        layout = self._layout(
            html.escape(block.text, quote=False),
            9,
            _CONTENT_WIDTH - 18,
            mono=True,
        )
        height = self._height(layout) + 14
        self._ensure_space(height + 8)
        self.cr.set_source_rgb(0.94, 0.94, 0.94)
        self.cr.rectangle(_MARGIN, self.y, _CONTENT_WIDTH, height)
        self.cr.fill()
        self.cr.set_source_rgb(0.08, 0.08, 0.08)
        self._draw_layout(layout, _MARGIN + 9, self.y + 7)
        self.y += height + 8

    def _draw_table(self, block: Block) -> None:
        """Намалювати просту таблицю з рамками."""
        from gi.repository import Pango, PangoCairo

        columns = max([len(block.headers)] + [len(row) for row in block.rows] or [0])
        if columns <= 0:
            return

        padding = 5.0
        column_width = _CONTENT_WIDTH / columns
        all_rows = [block.headers] + block.rows
        row_layouts: list[list] = []

        for row_number, row in enumerate(all_rows):
            layouts = []
            for column in range(columns):
                value = row[column] if column < len(row) else ""
                markup = inline_pango(value)
                if row_number == 0:
                    markup = f"<b>{markup}</b>"
                layouts.append(
                    self._layout(
                        markup,
                        9.5,
                        column_width - 2 * padding,
                    )
                )
            row_layouts.append(layouts)

        rows_height = []
        for layouts in row_layouts:
            rows_height.append(max(self._height(layout) for layout in layouts) + 2 * padding)

        # Якщо таблиця не влазить, переносимо її на нову сторінку.
        total = sum(rows_height) + 8
        if self.y + min(total, 120) > _PAGE_HEIGHT - _MARGIN:
            self._new_page()

        self.cr.set_line_width(0.6)
        for row_number, (row, layouts, row_height) in enumerate(
            zip(all_rows, row_layouts, rows_height)
        ):
            if self.y + row_height > _PAGE_HEIGHT - _MARGIN:
                self._new_page()
                self.cr.set_line_width(0.6)

            if row_number == 0:
                self.cr.set_source_rgb(0.93, 0.93, 0.93)
                self.cr.rectangle(_MARGIN, self.y, _CONTENT_WIDTH, row_height)
                self.cr.fill()

            self.cr.set_source_rgb(0, 0, 0)
            for column, layout in enumerate(layouts):
                x = _MARGIN + column * column_width
                self.cr.rectangle(x, self.y, column_width, row_height)
                self.cr.stroke()
                self._draw_layout(layout, x + padding, self.y + padding)

            self.y += row_height

        self.y += 8

    def _draw_image(self, block: Block) -> None:
        """Вставити картинку, якщо вона існує та читається."""
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gdk, GdkPixbuf

        if self.store is None:
            return
        absolute = self.store.resolve_asset(block.target)
        if absolute is None or not absolute.is_file():
            self._text(f"[image: {block.alt or block.target}]", size=10)
            return

        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(absolute))
        except Exception:
            self._text(f"[image: {block.alt or block.target}]", size=10)
            return

        scale = min(
            1.0,
            _CONTENT_WIDTH / max(1, pixbuf.get_width()),
            300.0 / max(1, pixbuf.get_height()),
        )
        width = max(1, int(pixbuf.get_width() * scale))
        height = max(1, int(pixbuf.get_height() * scale))
        scaled = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        self._ensure_space(height + 12)
        Gdk.cairo_set_source_pixbuf(self.cr, scaled, _MARGIN, self.y)
        self.cr.paint()
        self.y += height + 12

    def finish(self) -> None:
        """Завершити PDF-документ."""
        self.cr.show_page()
        self.cr.get_target().finish()


def export_pdf(note, store, destination: Path | str) -> Path:
    """Експортувати нотатку в PDF за допомогою cairo та Pango."""
    import gi

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    import cairo

    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    surface = cairo.PDFSurface(str(destination), _PAGE_WIDTH, _PAGE_HEIGHT)
    context = cairo.Context(surface)
    context.set_source_rgb(0, 0, 0)
    writer = _PdfWriter(context, store, note.label, note.tags)
    for block in parse_markdown(note.contents):
        writer.draw_block(block)
    writer.finish()
    return destination


__all__ = [
    "Block",
    "parse_markdown",
    "inline_pango",
    "escape_html",
    "note_to_html",
    "export_html",
    "export_pdf",
]
