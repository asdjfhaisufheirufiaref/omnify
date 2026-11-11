"""Модель нотаток і збереження їх у Markdown-файли.

Цей файл — "мозок" модуля Notes. Він не знає нічого про GTK і вікна.
Тут є дві головні сутності:

``Note``
    Одна нотатка: id, назва, текст, теги, папка та дата.

``NoteStore``
    Сховище на диску. Воно читає й записує Markdown-файли, копіює
    картинки, створює папки, робить резервні копії та синхронізує теку.

Структура папки notes::

    notes/
      Welcome--abc123.md
      Робота/
        План--def456.md
      assets/
        abc123/
          photo.png
      .trash/
        abc123.md
      .history/
        abc123/
          20261111T120000123456Z.md

Кожна нотатка — окремий .md-файл. Це важливо: файли можна читати
навіть без Omnify, копіювати на флешку або синхронізувати через
Dropbox/Syncthing. Приховані ``.trash`` і ``.history`` — службові:
вони не показуються як звичайні нотатки й не синхронізуються.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Цей рядок відділяє службовий блок front matter від тексту нотатки.
_FRONTMATTER_END = "\n---\n"

# Автоматичні знімки історії не створюються частіше, ніж раз на 5 хвилин.
# Ручний знімок через меню створюється завжди. На одну нотатку зберігаємо
# останні 20 версій — цього достатньо для відкату й це не роздуває теку.
_HISTORY_INTERVAL_SECONDS = 5 * 60
_HISTORY_LIMIT = 20


def _now() -> str:
    """Повернути поточний час у форматі ISO-8601.

    Приклад: ``2026-11-08T10:05:13.405208+00:00``.
    Такий рядок можна сортувати як звичайний текст.
    """
    return datetime.now(timezone.utc).isoformat()


def default_notes_dir() -> Path:
    """Повернути папку, де лежать Markdown-файли нотаток.

    За замовчуванням це ``notes`` у корені проєкту. Для тестів або
    альтернативного запуску можна задати ``OMNIFY_NOTES_DIR``.
    """
    env = os.environ.get("OMNIFY_NOTES_DIR")
    if env:
        return Path(env).expanduser()

    # notes_engine.py лежить у .../omnify/omnify/modules/notes_engine.py,
    # тому parents[2] — це корінь усього проєкту.
    return Path(__file__).resolve().parents[2] / "notes"


def default_backup_dir() -> Path:
    """Повернути папку для автоматичних ZIP-резервних копій."""
    env = os.environ.get("OMNIFY_BACKUP_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "omnify" / "backups"


def _normalise_tags(values) -> list[str]:
    """Перетворити будь-яке значення в охайний список тегів.

    Теги вводяться як ``робота, ідеї`` або зберігаються як JSON-список.
    Ця функція прибирає пробіли, порожні значення та повторення.
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value).strip().lstrip("#")
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


@dataclass
class Note:
    """Одна нотатка.

    ``@dataclass`` автоматично створює конструктор, ``repr`` та
    порівняння. Поля з ``default``/``default_factory`` можна не передавати.
    """

    # Унікальний id. Він не змінюється, навіть якщо змінити назву нотатки.
    id: str = field(default_factory=lambda: uuid4().hex)

    # Назва, яку бачить користувач.
    label: str = "Untitled"

    # Markdown-текст нотатки. Картинки записані як звичайні посилання.
    contents: str = ""

    # Час останнього збереження у форматі ISO-8601.
    updated_at: str = field(default_factory=_now)

    # Теги без символу "#": ["робота", "ідеї"].
    tags: list[str] = field(default_factory=list)

    # Папка нотатки відносно notes/. Порожній рядок = корінь notes/.
    folder: str = ""

    # Закріплена нотатка показується на початку списку.
    pinned: bool = False

    # Фізичний шлях до .md-файлу. У JSON він не зберігається, бо залежить
    # від комп'ютера. ``compare=False`` не включає шлях у порівняння.
    path: Path | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """Перетворити нотатку на словник для старого JSON-формату."""
        return {
            "id": self.id,
            "label": self.label,
            "contents": self.contents,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
            "folder": self.folder,
            "pinned": bool(self.pinned),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        """Створити нотатку зі словника (міграція старого notes.json)."""
        raw_pinned = data.get("pinned", False)
        if isinstance(raw_pinned, str):
            pinned = raw_pinned.strip().casefold() in {"1", "true", "yes", "on"}
        else:
            pinned = bool(raw_pinned)
        return cls(
            id=str(data.get("id") or uuid4().hex),
            label=str(data.get("label") or "Untitled"),
            contents=str(data.get("contents") or ""),
            updated_at=str(data.get("updated_at") or _now()),
            tags=_normalise_tags(data.get("tags")),
            folder=str(data.get("folder") or ""),
            pinned=pinned,
        )


class NoteStore:
    """Читання, запис і обслуговування папки з нотатками."""

    def __init__(self, root: Path | str | None = None) -> None:
        """Створити сховище та підготувати потрібні папки."""
        # expanduser() перетворює "~" на домашню папку, resolve() робить
        # шлях абсолютним і прибирає "..".
        self.root = (Path(root).expanduser() if root else default_notes_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    @property
    def assets_dir(self) -> Path:
        """Папка для картинок усіх нотаток."""
        return self.root / "assets"

    @property
    def trash_dir(self) -> Path:
        """Службова папка для нотаток, які користувач прибрав у Trash.

        Вона починається з крапки, тому ``load()``, ``folders()`` і
        синхронізація не показують ці нотатки як звичайні. Файли в Trash
        можна відновити разом із папкою, у якій вони лежали.
        """
        return self.root / ".trash"

    @property
    def history_dir(self) -> Path:
        """Службова папка зі знімками попередніх версій нотаток."""
        return self.root / ".history"

    # ------------------------------------------------------------------
    # Читання та запис
    # ------------------------------------------------------------------
    def load(self) -> list[Note]:
        """Прочитати всі .md-файли, включно з вкладеними папками."""
        notes: list[Note] = []

        # rglob("*") означає "рекурсивно всі файли". Ми беремо тільки *.md.
        for path in sorted(self.root.rglob("*.md"), key=lambda item: str(item).casefold()):
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                continue

            # Папку assets не вважаємо папкою нотаток.
            if any(part.lower() == "assets" or part.startswith(".") for part in relative.parts):
                continue

            try:
                note = self._read_note(path)
            except (OSError, ValueError):
                continue

            # Якщо файл лежить у notes/Робота/План.md, то folder="Робота".
            note.folder = relative.parent.as_posix() if relative.parent != Path(".") else ""
            notes.append(note)

        # Закріплені нотатки завжди зверху; усередині груп — новіші першими.
        notes.sort(
            key=lambda note: (bool(note.pinned), note.updated_at),
            reverse=True,
        )
        return notes

    def save(self, note: Note) -> None:
        """Записати одну нотатку у відповідний .md-файл."""
        note.id = self._safe_id(note.id) or uuid4().hex
        note.label = note.label.strip() or "Untitled"
        note.tags = _normalise_tags(note.tags)
        note.folder = self._safe_folder(note.folder)
        note.updated_at = _now()

        path = self._note_path(note)
        old_path = Path(note.path) if note.path else None

        # json.dumps безпечно екранує лапки та інші символи в назві й тегах.
        text = (
            "---\n"
            f"id: {note.id}\n"
            f"title: {json.dumps(note.label, ensure_ascii=False)}\n"
            f"tags: {json.dumps(note.tags, ensure_ascii=False)}\n"
            f"pinned: {'true' if note.pinned else 'false'}\n"
            f"updated_at: {note.updated_at}\n"
            "---\n"
            f"{note.contents}"
        )
        self._snapshot_before_save(note, old_path)
        self._atomic_write(path, text)

        # Якщо нотатку переміщено в іншу папку або перейменовано, старий
        # файл видаляємо після успішного запису нового.
        if old_path is not None and old_path != path:
            try:
                old_path.unlink()
            except OSError:
                pass
            self._remove_empty_folder(old_path.parent)

        note.path = path

    def delete(self, note: Note) -> None:
        """Видалити нотатку, її .md-файл і папку з її картинками."""
        path = Path(note.path) if note.path else self._note_path(note)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

        # Картинки та історія цієї нотатки більше не потрібні.
        note_id = self._safe_id(note.id)
        if note_id:
            shutil.rmtree(self.assets_dir / note_id, ignore_errors=True)
            self.clear_history(note)

        self._remove_empty_folder(path.parent)

    def trash_note(self, note: Note) -> Path:
        """Перемістити нотатку в Trash, не втрачаючи її текст і картинки.

        Файл зберігає відносний шлях усередині ``.trash/``, тому після
        відновлення нотатка повертається у свою початкову папку. Картинки
        залишаються в ``assets/<id>/`` і видаляються лише під час
        остаточного очищення Trash.
        """
        source = Path(note.path) if note.path else self._note_path(note)
        try:
            relative = source.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Note is outside notes") from exc
        if relative.parts and relative.parts[0] == ".trash":
            raise ValueError("Note is already in Trash")

        target = self.trash_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Дуже рідкісний випадок: у Trash уже є файл із таким шляхом.
            # Додаємо короткий суфікс, щоб не перезаписати старі дані.
            target = target.with_name(
                f"{target.stem}-{uuid4().hex[:8]}{target.suffix}"
            )

        shutil.move(str(source), str(target))
        note.path = target
        self._remove_empty_folder(source.parent)
        return target

    def trashed_notes(self) -> list[Note]:
        """Повернути нотатки з Trash у порядку останнього переміщення."""
        if not self.trash_dir.is_dir():
            return []

        notes: list[Note] = []
        for path in sorted(self.trash_dir.rglob("*.md"), key=lambda item: str(item).casefold()):
            try:
                relative = path.relative_to(self.trash_dir)
                note = self._read_note(path)
            except (OSError, ValueError):
                continue
            note.folder = relative.parent.as_posix() if relative.parent != Path(".") else ""
            note.path = path
            notes.append(note)

        def modified(note: Note) -> float:
            try:
                return Path(note.path).stat().st_mtime if note.path else 0.0
            except OSError:
                return 0.0

        notes.sort(key=modified, reverse=True)
        return notes

    def restore_trashed(self, note: Note) -> Note:
        """Повернути нотатку з Trash на її попереднє місце."""
        source = Path(note.path) if note.path else None
        if source is None:
            raise ValueError("Trashed note has no file path")
        try:
            relative = source.relative_to(self.trash_dir)
        except ValueError as exc:
            raise ValueError("Note is not in Trash") from exc

        target = self.root / relative
        if target.exists():
            raise ValueError(f"Note already exists: {target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))

        note.folder = relative.parent.as_posix() if relative.parent != Path(".") else ""
        note.path = target
        self._remove_empty_folder(source.parent)
        return note

    def purge_trashed(self, note: Note) -> None:
        """Остаточно видалити одну нотатку з Trash та її картинки."""
        source = Path(note.path) if note.path else None
        if source is None:
            raise ValueError("Trashed note has no file path")
        try:
            source.relative_to(self.trash_dir)
        except ValueError as exc:
            raise ValueError("Note is not in Trash") from exc

        try:
            source.unlink()
        except FileNotFoundError:
            pass

        note_id = self._safe_id(note.id)
        if note_id:
            shutil.rmtree(self.assets_dir / note_id, ignore_errors=True)
            self.clear_history(note)
        self._remove_empty_folder(source.parent)

    def empty_trash(self) -> int:
        """Остаточно видалити всі нотатки з Trash; повернути їх кількість."""
        notes = self.trashed_notes()
        for note in notes:
            self.purge_trashed(note)
        self._remove_empty_folder(self.trash_dir)
        return len(notes)

    # ------------------------------------------------------------------
    # Папки
    # ------------------------------------------------------------------
    def folders(self) -> list[str]:
        """Повернути список папок нотаток відносно notes/."""
        result: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_dir():
                continue
            relative = path.relative_to(self.root)
            if any(part.lower() == "assets" or part.startswith(".") for part in relative.parts):
                continue
            result.append(relative.as_posix())
        return sorted(set(result), key=str.casefold)

    def create_folder(self, name: str) -> str:
        """Створити папку нотаток і повернути її нормалізовану назву."""
        folder = self._safe_folder(name)
        if not folder:
            raise ValueError("Folder name is empty")
        self._folder_path(folder).mkdir(parents=True, exist_ok=True)
        return folder

    def move_note(self, note: Note, folder: str) -> str:
        """Перемістити нотатку в іншу папку (порожній рядок = корінь)."""
        note.folder = self._safe_folder(folder)
        self.save(note)
        return note.folder

    def rename_folder(self, old_name: str, new_name: str) -> int:
        """Перейменувати папку, зберігши всі нотатки та id.

        Якщо нова папка вже існує, нотатки обох папок об'єднуються.
        Повертає кількість переміщених нотаток.
        """
        old_folder = self._safe_folder(old_name)
        new_folder = self._safe_folder(new_name)
        if not old_folder or not new_folder:
            raise ValueError("Folder name is empty")
        if old_folder == new_folder:
            return 0

        old_path = self._folder_path(old_folder)
        new_path = self._folder_path(new_folder)
        if not old_path.is_dir():
            raise ValueError(f"Folder does not exist: {old_folder}")
        if new_path.is_relative_to(old_path):
            raise ValueError("New folder cannot be inside the old folder")

        moved = 0
        for note in self.load():
            if note.folder != old_folder and not note.folder.startswith(old_folder + "/"):
                continue
            suffix = note.folder[len(old_folder):].lstrip("/")
            note.folder = f"{new_folder}/{suffix}" if suffix else new_folder
            self.save(note)
            moved += 1

        new_path.mkdir(parents=True, exist_ok=True)
        self._remove_empty_folder(old_path)
        return moved

    def delete_folder(self, folder: str) -> int:
        """Видалити папку, перенісши всі її нотатки в корінь.

        Ми не видаляємо нотатки разом із папкою: це захищає користувача
        від випадкової втрати тексту. Повертаємо кількість перенесених
        нотаток.
        """
        safe_folder = self._safe_folder(folder)
        if not safe_folder:
            return 0

        folder_path = self._folder_path(safe_folder)
        if not folder_path.exists():
            return 0

        moved = 0
        for path in list(folder_path.rglob("*.md")):
            try:
                note = self._read_note(path)
            except (OSError, ValueError):
                continue
            note.folder = ""
            self.save(note)
            moved += 1

        self._remove_empty_folder(folder_path)
        return moved

    # ------------------------------------------------------------------
    # Теги
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_tag(tag: str) -> str:
        """Прибрати пробіли та провідний ``#`` з назви тегу."""
        return str(tag).strip().lstrip("#").strip()

    def rename_tag(self, old_tag: str, new_tag: str) -> int:
        """Перейменувати тег у всіх нотатках і повернути їх кількість.

        Порівняння не враховує регістр. Якщо в нотатці вже є новий тег,
        повтор не створюється. Порожні назви заборонені.
        """
        old = self._clean_tag(old_tag)
        new = self._clean_tag(new_tag)
        if not old:
            raise ValueError("Old tag is empty")
        if not new:
            raise ValueError("New tag is empty")
        if old.casefold() == new.casefold():
            return 0

        changed = 0
        for note in self.load():
            if not any(tag.casefold() == old.casefold() for tag in note.tags):
                continue
            note.tags = [
                new if tag.casefold() == old.casefold() else tag
                for tag in note.tags
            ]
            note.tags = _normalise_tags(note.tags)
            self.save(note)
            changed += 1
        return changed

    def delete_tag(self, tag: str) -> int:
        """Видалити тег з усіх нотаток і повернути кількість змінених."""
        target = self._clean_tag(tag)
        if not target:
            return 0
        changed = 0
        for note in self.load():
            remaining = [
                value
                for value in note.tags
                if value.casefold() != target.casefold()
            ]
            if len(remaining) == len(note.tags):
                continue
            note.tags = remaining
            self.save(note)
            changed += 1
        return changed

    # ------------------------------------------------------------------
    # Картинки
    # ------------------------------------------------------------------
    def duplicate_note(self, note: Note, folder: str | None = None) -> Note:
        """Створити повну копію нотатки з новим id.

        Копія отримує новий id, тому її можна незалежно редагувати. Якщо
        в нотатці були картинки, вони копіюються в ``assets/<new-id>/``,
        а посилання в тексті замінюються на новий шлях.
        """
        source_id = self._safe_id(note.id)
        source_assets = self.assets_dir / source_id if source_id else None
        clone = Note(
            label=f"{note.label} (copy)",
            contents=note.contents,
            tags=list(note.tags),
            folder=note.folder if folder is None else folder,
            pinned=bool(note.pinned),
        )
        old_prefix = f"assets/{note.id}/"
        new_prefix = f"assets/{clone.id}/"
        clone.contents = clone.contents.replace(old_prefix, new_prefix)

        if source_assets is not None and source_assets.is_dir():
            shutil.copytree(
                source_assets,
                self.assets_dir / clone.id,
                dirs_exist_ok=True,
            )
        self.save(clone)
        return clone

    def create_version(self, note: Note, force: bool = True) -> Path | None:
        """Зберегти знімок поточної версії нотатки.

        Автоматичні знімки створюються не частіше ніж раз на п'ять
        хвилин. Ручний виклик з ``force=True`` створює знімок завжди.
        Знімок — це повна копія Markdown-файлу, тому його можна
        переглянути навіть без Omnify.
        """
        source = Path(note.path) if note.path else None
        if source is None or not source.is_file():
            return None
        try:
            relative = source.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Note is outside notes") from exc
        if relative.parts and relative.parts[0] in {".trash", ".history"}:
            return None

        note_id = self._safe_id(note.id)
        if not note_id:
            return None
        if not force and not self._history_due(note_id):
            return None
        return self._write_history_snapshot(source, note_id)

    def versions(self, note: Note) -> list[Note]:
        """Повернути знімки нотатки від найновішого до найстарішого."""
        note_id = self._safe_id(note.id)
        folder = Path(self.history_dir / note_id) if note_id else None
        if folder is None or not folder.is_dir():
            return []

        versions: list[Note] = []
        for path in sorted(folder.glob("*.md"), reverse=True):
            try:
                version = self._read_note(path)
            except (OSError, ValueError):
                continue
            version.folder = note.folder
            version.path = path
            versions.append(version)
        return versions

    def restore_version(self, note: Note, version: Note) -> Note:
        """Відновити текст, назву, теги й закріплення зі знімка.

        Поточна версія спочатку потрапляє в історію, тому відкат можна
        скасувати. Картинки не дублюються: вони живуть у ``assets/<id>/``
        і залишаються доступними, якщо файл не видаляли.
        """
        source = Path(version.path) if version.path else None
        if source is None or not source.is_file():
            raise ValueError("Version file not found")
        try:
            source.relative_to(self.history_dir)
        except ValueError as exc:
            raise ValueError("Version is not in history") from exc

        restored = self._read_note(source)
        self.create_version(note, force=True)
        note.label = restored.label
        note.contents = restored.contents
        note.tags = list(restored.tags)
        note.pinned = bool(restored.pinned)
        self.save(note)
        return note

    def delete_version(self, version: Note) -> None:
        """Видалити один знімок історії."""
        source = Path(version.path) if version.path else None
        if source is None:
            raise ValueError("Version file not found")
        try:
            source.relative_to(self.history_dir)
        except ValueError as exc:
            raise ValueError("Version is not in history") from exc
        try:
            source.unlink()
        except FileNotFoundError:
            pass
        self._remove_empty_folder(source.parent)
        self._remove_empty_folder(self.history_dir)

    def clear_history(self, note: Note) -> int:
        """Видалити всю історію нотатки; повернути кількість знімків."""
        note_id = self._safe_id(note.id)
        folder = self.history_dir / note_id if note_id else None
        if folder is None or not folder.is_dir():
            return 0
        count = len(list(folder.glob("*.md")))
        shutil.rmtree(folder, ignore_errors=True)
        self._remove_empty_folder(self.history_dir)
        return count

    def export_markdown(self, note: Note, target_root: Path | str) -> Path:
        """Експортувати нотатку в окрему теку разом із її картинками.

        У вибраній папці створюється підпапка з назвою нотатки, всередині
        лежить Markdown-файл і ``assets/<id>/``. Відносні посилання на
        зображення залишаються робочими, тому теку можна заархівувати або
        відкрити в іншому Markdown-редакторі.
        """
        source = Path(note.path) if note.path else None
        if source is None or not source.is_file():
            raise FileNotFoundError("Note file not found")
        try:
            relative = source.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Note is outside notes") from exc
        if relative.parts and relative.parts[0] in {".trash", ".history"}:
            raise ValueError("Cannot export a service note")

        target_root = Path(target_root).expanduser().resolve()
        if target_root == self.root:
            raise ValueError("Export folder must be different from notes")
        if target_root.is_relative_to(self.root):
            raise ValueError("Export folder cannot be inside notes")
        if self.root.is_relative_to(target_root):
            raise ValueError("Export folder cannot contain the notes folder")
        target_root.mkdir(parents=True, exist_ok=True)

        base = self._slugify(note.label)
        export_dir = target_root / base
        counter = 1
        while export_dir.exists():
            export_dir = target_root / f"{base}-{counter}"
            counter += 1
        export_dir.mkdir(parents=True)

        shutil.copy2(source, export_dir / source.name)
        note_id = self._safe_id(note.id)
        assets = self.assets_dir / note_id if note_id else None
        if assets is not None and assets.is_dir():
            shutil.copytree(assets, export_dir / "assets" / note_id)
        return export_dir

    def export_bundle_markdown(self, target_root: Path | str) -> tuple[Path, int]:
        """Експортувати всі активні нотатки як портативну Markdown-теку.

        Структура папок і ``assets/<id>/`` зберігаються, а ``index.md``
        містить посилання на всі нотатки. Приховані службові теки (кошик
        та історія) не експортуються.
        """
        target_root = Path(target_root).expanduser().resolve()
        if target_root == self.root:
            raise ValueError("Export folder must be different from notes")
        if target_root.is_relative_to(self.root):
            raise ValueError("Export folder cannot be inside notes")
        if self.root.is_relative_to(target_root):
            raise ValueError("Export folder cannot contain the notes folder")
        target_root.mkdir(parents=True, exist_ok=True)

        bundle = target_root / "omnify-notes"
        counter = 1
        while bundle.exists():
            bundle = target_root / f"omnify-notes-{counter}"
            counter += 1
        bundle.mkdir(parents=True)

        notes = self.load()
        lines = ["# Omnify notes", ""]
        for note in notes:
            if note.path is None:
                continue
            try:
                relative = note.path.relative_to(self.root)
            except ValueError:
                continue
            destination = bundle / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(note.path, destination)
            note_id = self._safe_id(note.id)
            assets = self.assets_dir / note_id if note_id else None
            if assets is not None and assets.is_dir():
                shutil.copytree(
                    assets,
                    bundle / "assets" / note_id,
                    dirs_exist_ok=True,
                )
            lines.append(f"- [{note.label}](<{relative.as_posix()}>)")
        (bundle / "index.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        return bundle, len(notes)

    def import_markdown(self, source: Path | str, folder: str = "") -> Note:
        """Імпортувати зовнішній Markdown-файл як нову нотатку.

        Якщо у файлі є front matter, використовуємо його назву, теги та
        картинки. Старий файл не змінюється: ми створюємо нову копію в
        ``notes/`` зі стабільним новим id.
        """
        source = Path(source).expanduser().resolve()
        if source.is_relative_to(self.root):
            raise ValueError("Markdown file is already inside notes")
        if source.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError("Only Markdown files can be imported")
        if not source.is_file():
            raise ValueError(f"Markdown file does not exist: {source}")

        note = self._read_note(source)
        note.path = None
        note.folder = self._safe_folder(folder) if folder else ""
        existing_ids = {item.id for item in self.load()}
        if note.id in existing_ids:
            note.id = uuid4().hex
        self.save(note)
        return note

    def import_image(self, note: Note, source: Path | str) -> str:
        """Скопіювати картинку в assets і повернути Markdown-шлях."""
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)

        note_id = self._safe_id(note.id) or uuid4().hex
        destination_dir = self.assets_dir / note_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / self._safe_attachment_name(source.name)

        if source.resolve() == destination.resolve():
            return destination.relative_to(self.root).as_posix()

        stem, suffix = destination.stem, destination.suffix
        counter = 1
        while destination.exists():
            destination = destination_dir / f"{stem}-{counter}{suffix}"
            counter += 1

        shutil.copy2(source, destination)
        return destination.relative_to(self.root).as_posix()

    def resolve_asset(self, relative_path: str) -> Path | None:
        """Безпечно перетворити відносний шлях у шлях на диску."""
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate

    # ------------------------------------------------------------------
    # Резервні копії та синхронізація
    # ------------------------------------------------------------------
    def create_backup(self, backup_dir: Path | str | None = None) -> Path:
        """Створити ZIP-архів усіх нотаток і картинок.

        Якщо ``backup_dir`` не передано, архів лежить у
        ``~/.local/share/omnify/backups``. Старі архіви (понад 10)
        автоматично видаляються.
        """
        destination_dir = (
            Path(backup_dir).expanduser() if backup_dir else default_backup_dir()
        )
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_resolved = destination_dir.resolve()

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = destination_dir / f"notes-{stamp}.zip"
        descriptor, temporary = tempfile.mkstemp(
            dir=str(destination_dir),
            prefix="notes-backup-",
            suffix=".zip.tmp",
        )
        os.close(descriptor)

        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in self.root.rglob("*"):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(self.root)
                    # Не додаємо сам архів, якщо backup_dir випадково
                    # знаходиться всередині notes/.
                    if path.resolve().is_relative_to(destination_resolved):
                        continue
                    # Приховуємо службові теки, але assets залишаємо.
                    if any(part.startswith(".") for part in relative.parts):
                        continue
                    archive.write(path, relative.as_posix())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

        # Залишаємо тільки 10 найновіших архівів.
        backups = sorted(destination_dir.glob("notes-*.zip"))
        for old in backups[:-10]:
            try:
                old.unlink()
            except OSError:
                pass
        return target

    def restore_backup(self, archive_path: Path | str) -> int:
        """Обережно відновити нотатки з ZIP-архіву.

        Архів не замінює всю теку notes, а зливається з нею: файли з
        архіву перезаписують однойменні, решта поточних нотаток лишається.
        Перед викликом UI створює страхувальну копію. Повертаємо кількість
        відновлених .md-файлів.
        """
        archive_path = Path(archive_path).expanduser()
        if not archive_path.is_file():
            raise FileNotFoundError(archive_path)

        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("Backup archive is damaged")

            # Не дозволяємо архіву писати файли поза notes/.
            for name in archive.namelist():
                member = Path(name)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"Unsafe path in backup: {name}")

            with tempfile.TemporaryDirectory() as temporary:
                temporary_path = Path(temporary)
                archive.extractall(temporary_path)

                restored = 0
                for source in temporary_path.rglob("*.md"):
                    relative = source.relative_to(temporary_path)
                    if any(
                        part.lower() == "assets" or part.startswith(".")
                        for part in relative.parts
                    ):
                        continue
                    target = self.root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    restored += 1

                # Картинки з архіву копіюємо окремо, щоб не втратити assets.
                restored_assets = temporary_path / "assets"
                if restored_assets.is_dir():
                    shutil.copytree(
                        restored_assets,
                        self.assets_dir,
                        dirs_exist_ok=True,
                    )

        return restored

    def sync_to(self, target: Path | str) -> Path:
        """Скопіювати всю папку notes в іншу теку (одностороння синхронізація).

        Таку теку можна покласти в Dropbox, Syncthing або на флешку.
        Це не двосторонній merge: файли в цілі оновлюються/додаються,
        але видалені в Omnify вручну з цілі не зникають.
        """
        target = Path(target).expanduser().resolve()
        if target == self.root:
            raise ValueError("Target folder must be different from notes")
        if target.is_relative_to(self.root):
            raise ValueError("Target folder cannot be inside notes")
        if self.root.is_relative_to(target):
            raise ValueError("Target folder cannot contain the notes folder")

        shutil.copytree(
            self.root,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".backups",
                ".history",
                ".trash",
                "*.tmp",
            ),
        )
        return target

    def sync_two_way(self, other: Path | str) -> tuple[int, int, int]:
        """Об'єднати нотатки з іншою текою в обидва боки.

        Нотатки впізнаються за стабільним ``id`` у front matter, а не за
        назвою файлу. Для однакового id перемагає новіша версія за
        ``updated_at``. Нотатки, яких немає з одного боку, додаються.
        Видалення під час такого merge не поширюються — для захисту від
        випадкової втрати даних.

        Повертає ``(imported, updated, exported)``: скільки нотаток
        додано з іншої теки, оновлено з неї та відправлено туди.
        """
        other = Path(other).expanduser().resolve()
        if other == self.root:
            raise ValueError("Sync folder must be different from notes")
        if other.is_relative_to(self.root):
            raise ValueError("Sync folder cannot be inside notes")
        if self.root.is_relative_to(other):
            raise ValueError("Sync folder cannot contain the notes folder")

        other_store = NoteStore(other)
        local_notes = {note.id: note for note in self.load()}
        other_notes = {note.id: note for note in other_store.load()}

        imported = 0
        updated = 0
        exported = 0

        for note_id, remote in other_notes.items():
            local = local_notes.get(note_id)
            if local is None:
                self.save(self._copy_note(remote))
                imported += 1
            elif remote.updated_at > local.updated_at:
                self.save(self._copy_note(remote, path=local.path))
                updated += 1

        for note_id, local in local_notes.items():
            remote = other_notes.get(note_id)
            if remote is None:
                other_store.save(self._copy_note(local))
                exported += 1
            elif local.updated_at > remote.updated_at:
                other_store.save(self._copy_note(local, path=remote.path))
                exported += 1

        # Картинки спільні для обох тек: спочатку забираємо відсутні з
        # іншої теки, потім відправляємо туди локальні.
        if other_store.assets_dir.is_dir():
            shutil.copytree(
                other_store.assets_dir,
                self.assets_dir,
                dirs_exist_ok=True,
            )
        if self.assets_dir.is_dir():
            shutil.copytree(
                self.assets_dir,
                other_store.assets_dir,
                dirs_exist_ok=True,
            )

        return imported, updated, exported

    def _snapshot_before_save(self, note: Note, old_path: Path | None) -> None:
        """Зберегти попередню версію перед перезаписом файлу."""
        if old_path is None or not old_path.is_file():
            return
        try:
            relative = old_path.relative_to(self.root)
        except ValueError:
            return
        if relative.parts and relative.parts[0] in {".trash", ".history"}:
            return

        note_id = self._safe_id(note.id)
        if not note_id or not self._history_due(note_id):
            return
        try:
            previous = self._read_note(old_path)
        except (OSError, ValueError):
            return

        previous.label = previous.label.strip() or "Untitled"
        previous.tags = _normalise_tags(previous.tags)
        current = (
            note.label,
            note.contents,
            tuple(note.tags),
            bool(note.pinned),
        )
        before = (
            previous.label,
            previous.contents,
            tuple(previous.tags),
            bool(previous.pinned),
        )
        if current == before:
            return

        try:
            self._write_history_snapshot(old_path, note_id)
        except OSError:
            # Історія — допоміжна функція; вона не повинна заважати
            # збереженню самої нотатки, якщо диск переповнений.
            pass

    def _history_due(self, note_id: str) -> bool:
        """Чи минуло достатньо часу для наступного авто-знімка."""
        folder = self.history_dir / note_id
        if not folder.is_dir():
            return True
        try:
            newest = max(
                (path.stat().st_mtime for path in folder.glob("*.md")),
                default=None,
            )
        except OSError:
            return True
        if newest is None:
            return True
        return time.time() - newest >= _HISTORY_INTERVAL_SECONDS

    def _write_history_snapshot(self, source: Path, note_id: str) -> Path:
        """Скопіювати файл нотатки в приховану теку історії."""
        target_dir = self.history_dir / note_id
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = target_dir / f"{stamp}.md"
        if target.exists():
            target = target_dir / f"{stamp}-{uuid4().hex[:8]}.md"
        shutil.copy2(source, target)
        # copy2 зберігає mtime оригіналу; для інтервалу авто-знімків
        # потрібен час створення самої версії.
        os.utime(target)
        self._trim_history(note_id)
        return target

    def _trim_history(self, note_id: str) -> None:
        """Залишити лише останні ``_HISTORY_LIMIT`` знімків."""
        folder = self.history_dir / note_id
        if not folder.is_dir():
            return
        files = sorted(folder.glob("*.md"))
        for path in files[:-_HISTORY_LIMIT]:
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _copy_note(note: Note, path: Path | None = None) -> Note:
        """Створити відв'язану копію нотатки для запису в іншу теку."""
        return Note(
            id=note.id,
            label=note.label,
            contents=note.contents,
            updated_at=note.updated_at,
            tags=list(note.tags),
            folder=note.folder,
            pinned=bool(note.pinned),
            path=path,
        )

    # ------------------------------------------------------------------
    # Приватні методи
    # ------------------------------------------------------------------
    def _read_note(self, path: Path) -> Note:
        """Прочитати один Markdown-файл."""
        text = path.read_text(encoding="utf-8")
        metadata: dict[str, str] = {}
        body = text

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
            stem_id = path.stem.rsplit("--", 1)[-1]
            note_id = self._safe_id(stem_id) or uuid4().hex

        title = self._decode_meta(metadata.get("title"))
        if not title:
            title = path.stem.rsplit("--", 1)[0].replace("-", " ").strip()
        if not title:
            title = "Untitled"

        updated_at = metadata.get("updated_at") or ""
        if not updated_at:
            try:
                updated_at = datetime.fromtimestamp(
                    path.stat().st_mtime,
                    timezone.utc,
                ).isoformat()
            except OSError:
                updated_at = _now()

        tags_value = metadata.get("tags", "")
        tags: list[str] = []
        if tags_value:
            try:
                decoded = json.loads(tags_value)
            except (TypeError, ValueError):
                decoded = tags_value
            tags = _normalise_tags(decoded)

        pinned = metadata.get("pinned", "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }

        return Note(
            id=note_id,
            label=title,
            contents=body,
            updated_at=updated_at,
            tags=tags,
            folder="",
            pinned=pinned,
            path=path,
        )

    @staticmethod
    def _decode_meta(value: str | None) -> str:
        """Розкодувати значення з front matter."""
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
    def _safe_folder(name: str) -> str:
        """Нормалізувати назву папки.

        Забороняємо вихід за межі notes, прибираємо слеші та
        зарезервовану теку assets.
        """
        parts: list[str] = []
        for part in str(name or "").replace("\\", "/").split("/"):
            part = part.strip()
            if part in ("", ".", ".."):
                continue
            part = re.sub(r"[^\w .-]+", "-", part, flags=re.UNICODE)
            part = part.strip(" .-")[:60]
            if not part or part.lower() == "assets" or part.startswith("."):
                continue
            parts.append(part)
        return "/".join(parts)

    @staticmethod
    def _slugify(label: str) -> str:
        """Перетворити назву нотатки на частину імені файлу."""
        text = unicodedata.normalize("NFKC", label).strip()
        text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-._")
        return text[:60].strip("-._") or "note"

    @classmethod
    def _safe_attachment_name(cls, name: str) -> str:
        """Зробити безпечне ім'я для картинки."""
        source = Path(name)
        stem = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE)
        stem = re.sub(r"-+", "-", stem).strip("-._") or "image"
        suffix = re.sub(r"[^0-9A-Za-z.]", "", source.suffix)
        return stem[:80] + suffix

    def _folder_path(self, folder: str) -> Path:
        """Повернути абсолютний шлях до папки нотаток."""
        safe_folder = self._safe_folder(folder)
        candidate = (self.root / safe_folder).resolve() if safe_folder else self.root
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError("Folder is outside notes")
        return candidate

    def _note_path(self, note: Note) -> Path:
        """Побудувати шлях до .md-файлу з урахуванням папки."""
        folder_path = self._folder_path(note.folder)
        return folder_path / f"{self._slugify(note.label)}--{note.id}.md"

    def _remove_empty_folder(self, folder: Path) -> None:
        """Видалити порожню папку, але не корінь notes."""
        try:
            folder = folder.resolve()
            folder.relative_to(self.root)
        except (OSError, ValueError):
            return
        if folder == self.root:
            return
        # Спочатку прибираємо порожні вкладені теки, потім саму папку.
        # rmdir не видаляє непорожню теку, тому нотатки в ній безпечні.
        candidates = [path for path in folder.rglob("*") if path.is_dir()]
        candidates.append(folder)
        for candidate in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
            try:
                candidate.rmdir()
            except OSError:
                pass

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Атомарно записати текст у файл."""
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


__all__ = [
    "Note",
    "NoteStore",
    "default_notes_dir",
    "default_backup_dir",
]
