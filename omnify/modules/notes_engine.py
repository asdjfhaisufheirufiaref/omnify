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

Кожна нотатка — окремий .md-файл. Це важливо: файли можна читати
навіть без Omnify, копіювати на флешку або синхронізувати через
Dropbox/Syncthing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Цей рядок відділяє службовий блок front matter від тексту нотатки.
_FRONTMATTER_END = "\n---\n"


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
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        """Створити нотатку зі словника (міграція старого notes.json)."""
        return cls(
            id=str(data.get("id") or uuid4().hex),
            label=str(data.get("label") or "Untitled"),
            contents=str(data.get("contents") or ""),
            updated_at=str(data.get("updated_at") or _now()),
            tags=_normalise_tags(data.get("tags")),
            folder=str(data.get("folder") or ""),
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

        # Новіші нотатки показуємо першими.
        notes.sort(key=lambda note: note.updated_at, reverse=True)
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
            f"updated_at: {note.updated_at}\n"
            "---\n"
            f"{note.contents}"
        )
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

        # Картинки цієї нотатки більше не потрібні.
        note_id = self._safe_id(note.id)
        if note_id:
            shutil.rmtree(self.assets_dir / note_id, ignore_errors=True)

        self._remove_empty_folder(path.parent)

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
    # Картинки
    # ------------------------------------------------------------------
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

        shutil.copytree(
            self.root,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".backups", "*.tmp"),
        )
        return target

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

        return Note(
            id=note_id,
            label=title,
            contents=body,
            updated_at=updated_at,
            tags=tags,
            folder="",
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
        try:
            folder.rmdir()
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
