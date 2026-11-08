"""Модель нотаток і збереження їх у Markdown-файли.

Головна ідея цього файлу:
1. ``Note`` — це простий Python-об'єкт (dataclass), який описує одну нотатку.
2. ``NoteStore`` — це "сховище", яке вміє читати й записувати ці об'єкти на диск.

Кожна нотатка лежить в окремому файлі ``notes/<назва>--<id>.md``.
У перших рядках файлу є невеликий службовий блок (front matter) з id,
назвою та датою. Далі йде звичайний текст нотатки в Markdown.

Картинки не "зашиваються" в .md-файл. Вони копіюються в
``notes/assets/<id-нотатки>/``, а в тексті залишається відносне посилання:

    ![підпис](assets/<id-нотатки>/photo.png)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Цей рядок відділяє службовий блок front matter від тексту нотатки.
_FRONTMATTER_END = "\n---\n"


def _now() -> str:
    """Повернути поточний час у текстовому форматі ISO-8601.

    Такий формат зручно сортувати як звичайні рядки, і його легко читати.
    Наприклад: ``2026-11-08T10:05:13.405208+00:00``.
    """
    return datetime.now(timezone.utc).isoformat()


def default_notes_dir() -> Path:
    """Повернути папку, у якій лежатимуть файли нотаток.

    За замовчуванням це папка ``notes`` у корені проєкту. Якщо змінна
    середовища ``OMNIFY_NOTES_DIR`` задана, використовується вона — це
    зручно для тестів і для запуску з іншої копії проєкту.
    """
    env = os.environ.get("OMNIFY_NOTES_DIR")
    if env:
        return Path(env).expanduser()

    # __file__ — це шлях до notes_engine.py:
    # .../omnify/omnify/modules/notes_engine.py
    # parents[0] = modules, parents[1] = omnify, parents[2] = корінь проєкту.
    return Path(__file__).resolve().parents[2] / "notes"


@dataclass
class Note:
    """Одна нотатка.

    ``@dataclass`` автоматично створює ``__init__``, ``__repr__`` та інші
    методи. Нам не потрібно вручну писати конструктор.
    """

    # Унікальний ідентифікатор нотатки. Він не змінюється навіть тоді,
    # коли користувач перейменовує нотатку або її файл.
    id: str = field(default_factory=lambda: uuid4().hex)

    # Назва, яку бачить користувач у списку нотаток.
    label: str = "Untitled"

    # Основний текст нотатки. У ньому можуть бути Markdown-посилання
    # на картинки, наприклад: ![фото](assets/id/photo.png).
    contents: str = ""

    # Коли нотатку востаннє збережено. Використовується для сортування.
    updated_at: str = field(default_factory=_now)

    # Фізичний шлях до .md-файлу на диску. Це поле не записується в JSON,
    # бо шлях залежить від комп'ютера. ``compare=False`` означає, що під час
    # порівняння двох Note це поле не враховується.
    path: Path | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """Перетворити нотатку на словник (для старого JSON-формату)."""
        return {
            "id": self.id,
            "label": self.label,
            "contents": self.contents,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        """Створити нотатку зі словника.

        Це використовується під час одноразової міграції старого
        ``notes.json`` у нові Markdown-файли.
        """
        return cls(
            id=str(data.get("id") or uuid4().hex),
            label=str(data.get("label") or "Untitled"),
            contents=str(data.get("contents") or ""),
            updated_at=str(data.get("updated_at") or _now()),
        )


class NoteStore:
    """Читання та запис нотаток у папці ``notes``.

    Клас навмисно не імпортує GTK. Завдяки цьому його можна тестувати
    без графічного інтерфейсу.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        """Створити сховище.

        ``root`` — необов'язковий шлях до папки нотаток. Якщо його не
        передати, використовується :func:`default_notes_dir`.
        """
        # expanduser() перетворює "~" на домашню папку користувача.
        # resolve() робить шлях абсолютним і прибирає зайві "..".
        self.root = (Path(root).expanduser() if root else default_notes_dir()).resolve()

        # Створюємо папку notes і notes/assets, якщо їх ще немає.
        # parents=True означає "створити всі батьківські папки".
        # exist_ok=True означає "не падати, якщо папка вже існує".
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    @property
    def assets_dir(self) -> Path:
        """Повернути папку, у якій лежать усі картинки нотаток."""
        return self.root / "assets"

    def load(self) -> list[Note]:
        """Прочитати всі ``*.md`` файли з папки нотаток.

        Пошкоджений або чужий файл не повинен ламати весь застосунок,
        тому помилки читання окремого файлу просто пропускаються.
        """
        notes: list[Note] = []

        # sorted() дає стабільний порядок, якщо в нотаток однаковий час.
        for path in sorted(self.root.glob("*.md"), key=lambda item: item.name.casefold()):
            try:
                notes.append(self._read_note(path))
            except (OSError, ValueError):
                continue

        # Новіші нотатки показуємо першими.
        notes.sort(key=lambda note: note.updated_at, reverse=True)
        return notes

    def save(self, note: Note) -> None:
        """Записати нотатку у файл.

        Якщо назва змінилася, файл перейменовується. Спочатку ми записуємо
        новий файл і лише потім видаляємо старий — так навіть при збої
        посередині операції текст не втратиться.
        """
        # Захищаємо id від символів, які можуть бути небезпечними в імені
        # файлу або в шляху до папки з картинками.
        note.id = self._safe_id(note.id) or uuid4().hex
        note.label = note.label.strip() or "Untitled"
        note.updated_at = _now()

        # _note_path будує ім'я виду "Моя-нотатка--abc123.md".
        path = self._note_path(note)
        old_path = Path(note.path) if note.path else None

        # json.dumps(...) бере назву в лапки та екранує спеціальні символи.
        # Це дозволяє безпечно мати двокрапку або лапки в назві нотатки.
        text = (
            "---\n"
            f"id: {note.id}\n"
            f"title: {json.dumps(note.label, ensure_ascii=False)}\n"
            f"updated_at: {note.updated_at}\n"
            "---\n"
            f"{note.contents}"
        )

        self._atomic_write(path, text)

        # Якщо назва змінилася, старий файл більше не потрібен.
        if old_path is not None and old_path != path:
            try:
                old_path.unlink()
            except OSError:
                # Якщо файл уже хтось видалив — це не помилка для нас.
                pass

        note.path = path

    def import_image(self, note: Note, source: Path | str) -> str:
        """Скопіювати картинку в папку assets і повернути шлях для Markdown.

        Наприклад, повертає ``assets/<id>/photo.png``. Саме такий відносний
        шлях записується в текст нотатки, тому папку ``notes`` можна
        переносити цілком, не ламаючи посилання.
        """
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)

        note_id = self._safe_id(note.id) or uuid4().hex
        destination_dir = self.assets_dir / note_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / self._safe_attachment_name(source.name)

        # Якщо користувач вибрав файл, який уже лежить у assets цієї нотатки,
        # не копіюємо його сам у себе.
        if source.resolve() == destination.resolve():
            return destination.relative_to(self.root).as_posix()

        # Якщо файл з такою назвою вже є, додаємо -1, -2, ...
        stem, suffix = destination.stem, destination.suffix
        counter = 1
        while destination.exists():
            destination = destination_dir / f"{stem}-{counter}{suffix}"
            counter += 1

        # copy2 зберігає не лише вміст, а й дату зміни файлу.
        shutil.copy2(source, destination)
        return destination.relative_to(self.root).as_posix()

    def resolve_asset(self, relative_path: str) -> Path | None:
        """Перетворити відносний шлях з Markdown на безпечний шлях на диску.

        Повертає ``None``, якщо шлях намагається вийти за межі папки notes
        (наприклад, через ``../../``). Це проста перевірка безпеки.
        """
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate

    # -- приватні методи читання та запису ----------------------------
    def _read_note(self, path: Path) -> Note:
        """Прочитати один Markdown-файл і перетворити його на ``Note``."""
        text = path.read_text(encoding="utf-8")
        metadata: dict[str, str] = {}
        body = text

        # Front matter має вигляд:
        # ---
        # id: ...
        # title: "..."
        # updated_at: ...
        # ---
        # текст нотатки
        if text.startswith("---\n"):
            end = text.find(_FRONTMATTER_END, 4)
            if end != -1:
                header = text[4:end]
                body = text[end + len(_FRONTMATTER_END):]
                for line in header.splitlines():
                    key, separator, value = line.partition(":")
                    if separator:
                        metadata[key.strip()] = value.strip()

        note_id = self._safe_id(metadata.get("id", ""))
        if not note_id:
            # Якщо id немає, беремо частину імені файлу після "--".
            stem_id = path.stem.rsplit("--", 1)[-1]
            note_id = self._safe_id(stem_id) or uuid4().hex

        title = self._decode_meta(metadata.get("title"))
        if not title:
            title = path.stem.rsplit("--", 1)[0].replace("-", " ").strip()
        if not title:
            title = "Untitled"

        updated_at = metadata.get("updated_at") or ""
        if not updated_at:
            # Якщо дати в файлі немає, використовуємо дату зміни файлу.
            try:
                updated_at = datetime.fromtimestamp(
                    path.stat().st_mtime,
                    timezone.utc,
                ).isoformat()
            except OSError:
                updated_at = _now()

        return Note(
            id=note_id,
            label=title,
            contents=body,
            updated_at=updated_at,
            path=path,
        )

    @staticmethod
    def _decode_meta(value: str | None) -> str:
        """Розкодувати значення з front matter (назва може бути в лапках)."""
        if not value:
            return ""
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                return value[1:-1]
            return str(decoded)
        return value

    @staticmethod
    def _safe_id(value: str | None) -> str:
        """Залишити в id тільки безпечні для шляху символи."""
        value = re.sub(r"[^0-9A-Za-z_-]", "", str(value or ""))
        return value[:128]

    @staticmethod
    def _slugify(label: str) -> str:
        """Перетворити назву нотатки на частину імені файлу.

        Пробіли стають дефісами, а небезпечні символи прибираються.
        Українські літери залишаються, бо ``\\w`` підтримує Unicode.
        """
        text = unicodedata.normalize("NFKC", label).strip()
        text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-._")
        return text[:60].strip("-._") or "note"

    @classmethod
    def _safe_attachment_name(cls, name: str) -> str:
        """Зробити безпечне ім'я для скопійованої картинки."""
        source = Path(name)
        stem = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE)
        stem = re.sub(r"-+", "-", stem).strip("-._") or "image"
        suffix = re.sub(r"[^0-9A-Za-z.]", "", source.suffix)
        return stem[:80] + suffix

    def _note_path(self, note: Note) -> Path:
        """Побудувати повний шлях до .md-файлу нотатки."""
        return self.root / f"{self._slugify(note.label)}--{note.id}.md"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Записати файл атомарно.

        Спочатку пишемо в тимчасовий файл, скидаємо дані на диск через
        fsync, і лише потім замінюємо старий файл через os.replace.
        Так читач ніколи не побачить напівзаписаний текст.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


__all__ = ["Note", "NoteStore", "default_notes_dir"]
