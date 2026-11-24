"""File Explorer module: browse folders, preview files and manage them safely.

The model (:class:`FileExplorer`) is GTK-free and testable: filesystem reads
go through injectable callables, and destructive actions (trash/delete) are
injectable so tests never touch the user's real trash.  Copy/move operations
are ordinary filesystem operations, exercised against temporary directories.

The GUI (:class:`FileExplorerModule`) adds the familiar Explorer layout:
places sidebar, back/forward/up history, editable location bar, live filter,
sortable columns, hidden-file toggle, multi-selection, a right-hand preview
pane, clipboard-style copy/cut/paste, new-folder/rename prompts, trash and
permanent-delete confirmations, a Properties dialog and command-palette
shortcuts.
"""

from __future__ import annotations

import os
import shutil
import stat as stat_module
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, unquote, urlparse

from ..core.commands import Command
from ..core.i18n import tr
from ..core.module_context import BaseModule

#: User-dir keys understood by ``~/.config/user-dirs.dirs``.
_XDG_KEYS = (
    ("desktop", "XDG_DESKTOP_DIR"),
    ("documents", "XDG_DOCUMENTS_DIR"),
    ("downloads", "XDG_DOWNLOAD_DIR"),
    ("music", "XDG_MUSIC_DIR"),
    ("pictures", "XDG_PICTURES_DIR"),
    ("videos", "XDG_VIDEOS_DIR"),
    ("publicshare", "XDG_PUBLICSHARE_DIR"),
    ("templates", "XDG_TEMPLATES_DIR"),
)

#: Extension → human-readable kind.  Folders and symlinks are handled first.
_KINDS: Dict[str, str] = {
    ".txt": "Text file",
    ".md": "Markdown document",
    ".markdown": "Markdown document",
    ".rst": "Text file",
    ".log": "Log file",
    ".pdf": "PDF document",
    ".png": "Image",
    ".jpg": "Image",
    ".jpeg": "Image",
    ".gif": "Image",
    ".webp": "Image",
    ".bmp": "Image",
    ".svg": "Image",
    ".tif": "Image",
    ".tiff": "Image",
    ".avif": "Image",
    ".heic": "Image",
    ".mp3": "Audio",
    ".flac": "Audio",
    ".ogg": "Audio",
    ".opus": "Audio",
    ".wav": "Audio",
    ".m4a": "Audio",
    ".aac": "Audio",
    ".mp4": "Video",
    ".mkv": "Video",
    ".webm": "Video",
    ".avi": "Video",
    ".mov": "Video",
    ".zip": "Archive",
    ".tar": "Archive",
    ".gz": "Archive",
    ".tgz": "Archive",
    ".xz": "Archive",
    ".bz2": "Archive",
    ".zst": "Archive",
    ".7z": "Archive",
    ".rar": "Archive",
    ".doc": "Document",
    ".docx": "Document",
    ".odt": "Document",
    ".rtf": "Document",
    ".xls": "Spreadsheet",
    ".xlsx": "Spreadsheet",
    ".ods": "Spreadsheet",
    ".csv": "Spreadsheet",
    ".ppt": "Presentation",
    ".pptx": "Presentation",
    ".odp": "Presentation",
    ".py": "Source code",
    ".js": "Source code",
    ".ts": "Source code",
    ".tsx": "Source code",
    ".jsx": "Source code",
    ".c": "Source code",
    ".h": "Source code",
    ".cpp": "Source code",
    ".rs": "Source code",
    ".go": "Source code",
    ".java": "Source code",
    ".sh": "Script",
    ".html": "Web page",
    ".css": "Stylesheet",
    ".json": "JSON file",
    ".toml": "Configuration",
    ".yaml": "Configuration",
    ".yml": "Configuration",
    ".ini": "Configuration",
    ".cfg": "Configuration",
    ".conf": "Configuration",
}

_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}
)
_TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".c",
        ".h",
        ".cpp",
        ".rs",
        ".go",
        ".java",
        ".sh",
        ".html",
        ".css",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".svg",
        ".xml",
        ".desktop",
        ".service",
    }
)


# ---------------------------------------------------------------------------
# Pure helpers and parsers
# ---------------------------------------------------------------------------

def format_file_size(size: Optional[float]) -> str:
    """Format a byte count as ``"0 B"`` / ``"1.5 MiB"`` / ``"2.0 GiB"``."""
    if size is None:
        return "—"
    amount = max(0.0, float(size))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(amount)} B"
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TiB"


def format_modified(timestamp: Optional[float]) -> str:
    """Format a modification time as ``YYYY-MM-DD HH:MM`` (or ``—``)."""
    if timestamp is None:
        return "—"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


def human_kind(
    name: str,
    is_dir: bool = False,
    is_symlink: bool = False,
    mode: int = 0,
) -> str:
    """Return a short human-readable type for a file entry."""
    if is_symlink:
        return "Folder link" if is_dir else "Symbolic link"
    if is_dir:
        return "Folder"
    suffix = os.path.splitext(name)[1].lower()
    kind = _KINDS.get(suffix)
    if kind:
        return kind
    if mode & 0o111:
        return "Executable"
    return "File"


def parse_user_dirs(text: str, home: str) -> Dict[str, str]:
    """Parse ``user-dirs.dirs`` into ``{short_key: absolute_path}``.

    ``$HOME`` is substituted, quotes are stripped and unknown keys are
    ignored.  Missing entries are simply absent; callers supply defaults.
    """
    data: Dict[str, str] = {}
    keys = dict(_XDG_KEYS)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        short = next((short for short, xdg in keys.items() if xdg == key), None)
        if short is None:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        value = value.replace("$HOME", home)
        if value.startswith("~"):
            value = home + value[1:]
        data[short] = os.path.abspath(value)
    return data


def resolve_location(text: str, base: str, home: str) -> str:
    """Resolve a typed location: absolute, relative, ``~`` or ``file://``."""
    raw = (text or "").strip()
    if not raw:
        return os.path.abspath(base)
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    if raw == "~" or raw.startswith("~/"):
        raw = home + raw[1:]
    raw = os.path.expandvars(raw)
    if not os.path.isabs(raw):
        raw = os.path.join(base, raw)
    return os.path.abspath(raw)


def validate_name(name: str) -> str:
    """Validate one file name; raise ``ValueError`` with a clear message."""
    value = (name or "").strip()
    if not value:
        raise ValueError("A name is required")
    if value in {".", ".."}:
        raise ValueError("This name is reserved")
    if "/" in value or "\0" in value:
        raise ValueError("A name cannot contain '/'")
    return value


def unique_child_name(
    directory: str,
    desired: str,
    exists: Callable[[str], bool] = os.path.lexists,
) -> str:
    """Return a non-existing name in *directory*, adding ``(copy)``/numbers."""
    name = validate_name(desired)
    if not exists(os.path.join(directory, name)):
        return name
    stem, suffix = os.path.splitext(name)
    if not stem:
        stem, suffix = name, ""
    candidate = f"{stem} (copy){suffix}"
    if not exists(os.path.join(directory, candidate)):
        return candidate
    index = 2
    while True:
        candidate = f"{stem} (copy {index}){suffix}"
        if not exists(os.path.join(directory, candidate)):
            return candidate
        index += 1


def filter_entries(
    entries: Sequence["FileEntry"], query: str
) -> List["FileEntry"]:
    """Case-insensitive name filter; a blank query returns every entry."""
    needle = (query or "").strip().casefold()
    if not needle:
        return list(entries)
    return [entry for entry in entries if needle in entry.name.casefold()]


_SORT_KEYS = {
    "name": lambda entry: entry.name.casefold(),
    "size": lambda entry: entry.size,
    "type": lambda entry: entry.kind.casefold(),
    "modified": lambda entry: entry.modified,
}


def sort_entries(
    entries: Sequence["FileEntry"],
    key: str = "name",
    descending: bool = False,
) -> List["FileEntry"]:
    """Sort entries with folders always first, then by *key*."""
    key_fn = _SORT_KEYS.get(key, _SORT_KEYS["name"])
    folders = [entry for entry in entries if entry.is_dir]
    files = [entry for entry in entries if not entry.is_dir]
    folders.sort(key=key_fn, reverse=descending)
    files.sort(key=key_fn, reverse=descending)
    return folders + files


def preview_kind(path: str) -> str:
    """Return ``"image"``, ``"text"`` or ``"none"`` for a preview pane."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return "none"


def breadcrumb_parts(path: str, home: str) -> List[Tuple[str, str]]:
    """Split *path* into clickable ``(label, absolute_path)`` crumbs.

    Paths inside the home directory start with a friendly ``Home`` crumb;
    other paths start at the filesystem root.  This is a pure helper so the
    breadcrumb bar can be tested without GTK.
    """
    target = os.path.abspath(path)
    home = os.path.abspath(home)
    if target == home:
        return [("Home", home)]
    if _is_within(target, home):
        crumbs: List[Tuple[str, str]] = [("Home", home)]
        current = home
        for segment in os.path.relpath(target, home).split(os.sep):
            current = os.path.join(current, segment)
            crumbs.append((segment, current))
        return crumbs
    crumbs = [("File System", os.path.abspath(os.sep))]
    current = os.path.abspath(os.sep)
    for segment in target.strip(os.sep).split(os.sep):
        if not segment:
            continue
        current = os.path.join(current, segment)
        crumbs.append((segment, current))
    return crumbs


def terminal_argv(terminal: str, directory: str) -> List[str]:
    """Return the argv that opens *terminal* in *directory*.

    Different emulators spell "start here" differently; this table keeps the
    logic testable and avoids shell quoting entirely.
    """
    name = os.path.basename(terminal)
    flags = {
        "gnome-terminal": ["--working-directory", directory],
        "kgx": ["--working-directory", directory],
        "konsole": ["--workdir", directory],
        "xfce4-terminal": ["--working-directory", directory],
        "mate-terminal": ["--working-directory", directory],
        "tilix": ["--working-directory", directory],
        "alacritty": ["--working-directory", directory],
        "kitty": ["--directory", directory],
        "wezterm": ["start", "--cwd", directory],
    }
    return [terminal] + flags.get(name, [])


def find_terminal(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """Return the first installed terminal emulator, or ``None``."""
    for candidate in (
        "x-terminal-emulator",
        "gnome-terminal",
        "kgx",
        "konsole",
        "xfce4-terminal",
        "alacritty",
        "kitty",
        "wezterm",
    ):
        try:
            path = which(candidate)
        except Exception:
            path = None
        if path:
            return path
    return None


def disk_usage(
    path: str, statvfs: Callable[[str], object] = os.statvfs
) -> Optional[Tuple[int, int, int]]:
    """Return ``(total, used, free)`` bytes for *path*, or ``None``."""
    try:
        stats = statvfs(path)
        total = int(stats.f_blocks * stats.f_frsize)
        free = int(stats.f_bavail * stats.f_frsize)
    except Exception:
        return None
    if total <= 0:
        return None
    return (total, max(0, total - free), max(0, free))



def _default_stat(path: str) -> os.stat_result:
    """Stat *path*, falling back to ``lstat`` for broken symlinks."""
    try:
        return os.stat(path)
    except OSError:
        return os.lstat(path)


def _default_read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _default_read_bytes(path: str, limit: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(limit)


def _default_trash(path: str, trash_dir: Optional[str] = None) -> str:
    """Move *path* to the XDG trash and write its ``.trashinfo`` entry.

    Returns the path inside ``Trash/files``.  This is a dependency-free
    implementation of the freedesktop trash layout; ``gio trash`` is not
    required.  Moving across filesystems falls back to ``shutil.move``.
    """
    base = trash_dir or os.path.join(
        os.environ.get("XDG_DATA_HOME")
        or os.path.expanduser("~/.local/share"),
        "Trash",
    )
    files_dir = os.path.join(base, "files")
    info_dir = os.path.join(base, "info")
    os.makedirs(files_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)

    name = os.path.basename(path.rstrip(os.sep)) or "item"
    if name.startswith("."):
        stem, suffix = name, ""
    else:
        stem, suffix = os.path.splitext(name)
    if not stem:
        stem, suffix = name, ""
    candidate = name
    index = 2
    while os.path.lexists(os.path.join(files_dir, candidate)) or os.path.exists(
        os.path.join(info_dir, candidate + ".trashinfo")
    ):
        candidate = f"{stem} {index}{suffix}"
        index += 1
    target = os.path.join(files_dir, candidate)
    shutil.move(path, target)
    absolute = os.path.abspath(path)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(
        os.path.join(info_dir, candidate + ".trashinfo"), "w", encoding="utf-8"
    ) as fh:
        fh.write(
            "[Trash Info]\n"
            f"Path={quote(absolute, safe='/')}\n"
            f"DeletionDate={stamp}\n"
        )
    return target


def _owner_name(uid: int) -> str:
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        import grp

        return grp.getgrgid(gid).gr_name
    except Exception:
        return str(gid)


def _is_within(path: str, parent: str) -> bool:
    """Return whether *path* is *parent* or lies below it."""
    path = os.path.abspath(path)
    parent = os.path.abspath(parent)
    if path == parent:
        return True
    return path.startswith(parent.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FileEntry:
    """One directory entry."""

    name: str
    path: str
    is_dir: bool
    is_symlink: bool
    size: int
    modified: float
    mode: int
    kind: str

    @property
    def sort_key(self) -> str:
        return self.name.casefold()


@dataclass(frozen=True)
class Place:
    """One sidebar shortcut."""

    key: str
    label: str
    path: str
    icon: str


class NavigationHistory:
    """A small back/forward history with a stable current entry."""

    def __init__(self, initial: Optional[str] = None) -> None:
        self._items: List[str] = [initial] if initial else []
        self._index = 0 if self._items else -1

    @property
    def current(self) -> Optional[str]:
        if not self._items or self._index < 0:
            return None
        return self._items[self._index]

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return 0 <= self._index < len(self._items) - 1

    def visit(self, path: str) -> bool:
        """Record a new location, dropping any forward branch."""
        if path == self.current:
            return False
        del self._items[self._index + 1 :]
        self._items.append(path)
        self._index = len(self._items) - 1
        return True

    def replace_current(self, path: str) -> None:
        """Use *path* without adding a history step (initial load)."""
        if not self._items:
            self._items.append(path)
            self._index = 0
        else:
            self._items[self._index] = path

    def back(self) -> Optional[str]:
        if not self.can_back:
            return None
        self._index -= 1
        return self.current

    def forward(self) -> Optional[str]:
        if not self.can_forward:
            return None
        self._index += 1
        return self.current


class FileExplorer:
    """Read-only listing plus safe file operations over an injectable FS."""

    def __init__(
        self,
        home: Optional[str] = None,
        list_dir: Callable[[str], Iterable[str]] = os.listdir,
        stat: Callable[[str], os.stat_result] = _default_stat,
        is_symlink: Callable[[str], bool] = os.path.islink,
        read_text: Callable[[str], str] = _default_read_text,
        read_bytes: Callable[[str, int], bytes] = _default_read_bytes,
        trash: Optional[Callable[[str], object]] = None,
        delete: Optional[Callable[[str], object]] = None,
        is_dir: Callable[[str], bool] = os.path.isdir,
        trash_dir: Optional[str] = None,
    ) -> None:
        self.home = os.path.abspath(home or os.path.expanduser("~"))
        self._list_dir = list_dir
        self._stat = stat
        self._is_symlink = is_symlink
        self._read_text = read_text
        self._read_bytes = read_bytes
        self._trash = trash or (lambda path: _default_trash(path, trash_dir))
        self._delete = delete or self._default_delete
        self._is_dir = is_dir

    # -- listing -------------------------------------------------------------
    def list_entries(
        self, directory: str, show_hidden: bool = False
    ) -> List[FileEntry]:
        """List *directory* as :class:`FileEntry` rows (unreadable entries skipped)."""
        entries: List[FileEntry] = []
        for name in self._list_dir(directory):
            if not show_hidden and name.startswith("."):
                continue
            path = os.path.join(directory, name)
            try:
                info = self._stat(path)
                link = bool(self._is_symlink(path))
            except OSError:
                continue
            is_directory = stat_module.S_ISDIR(info.st_mode)
            entries.append(
                FileEntry(
                    name=name,
                    path=path,
                    is_dir=is_directory,
                    is_symlink=link,
                    size=0 if is_directory else int(info.st_size),
                    modified=float(info.st_mtime),
                    mode=int(info.st_mode),
                    kind=human_kind(name, is_directory, link, int(info.st_mode)),
                )
            )
        return entries

    def places(self) -> List[Place]:
        """Sidebar places resolved from XDG user directories."""
        home = self.home
        config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            home, ".config"
        )
        try:
            text = self._read_text(os.path.join(config_home, "user-dirs.dirs"))
        except Exception:
            text = ""
        user = parse_user_dirs(text, home)
        defaults = {
            "desktop": os.path.join(home, "Desktop"),
            "documents": os.path.join(home, "Documents"),
            "downloads": os.path.join(home, "Downloads"),
            "music": os.path.join(home, "Music"),
            "pictures": os.path.join(home, "Pictures"),
            "videos": os.path.join(home, "Videos"),
        }
        labels = {
            "desktop": ("Desktop", "user-desktop"),
            "documents": ("Documents", "folder-documents"),
            "downloads": ("Downloads", "folder-download"),
            "music": ("Music", "folder-music"),
            "pictures": ("Pictures", "folder-pictures"),
            "videos": ("Videos", "folder-videos"),
        }
        places = [Place("home", "Home", home, "user-home")]
        for key in ("desktop", "documents", "downloads", "music", "pictures", "videos"):
            path = user.get(key, defaults[key])
            try:
                if not self._is_dir(path):
                    continue
            except Exception:
                continue
            label, icon = labels[key]
            places.append(Place(key, label, path, icon))
        places.append(Place("root", "File System", os.path.abspath(os.sep), "drive-harddisk"))
        return places

    def describe(self, path: str) -> List[Tuple[str, str]]:
        """Properties rows for the Properties dialog."""
        name = os.path.basename(path.rstrip(os.sep)) or path
        try:
            info = self._stat(path)
            link = bool(self._is_symlink(path))
        except OSError as exc:
            return [("Name", name), ("Path", path), ("Error", str(exc))]
        is_directory = stat_module.S_ISDIR(info.st_mode)
        kind = human_kind(name, is_directory, link, int(info.st_mode))
        rows: List[Tuple[str, str]] = [
            ("Name", name),
            ("Path", path),
            ("Type", kind),
        ]
        if is_directory:
            try:
                rows.append(("Items", str(len(list(self._list_dir(path))))))
            except OSError:
                rows.append(("Items", "—"))
        else:
            rows.append(("Size", format_file_size(info.st_size)))
        rows.extend(
            [
                ("Modified", format_modified(info.st_mtime)),
                ("Permissions", stat_module.filemode(info.st_mode)),
                ("Owner", _owner_name(info.st_uid)),
                ("Group", _group_name(info.st_gid)),
            ]
        )
        if link:
            try:
                rows.append(("Target", os.readlink(path)))
            except OSError:
                pass
        return rows

    # -- safe operations -----------------------------------------------------
    def create_folder(self, parent: str, name: str) -> str:
        """Create a uniquely named folder and return its path."""
        desired = unique_child_name(parent, name)
        path = os.path.join(parent, desired)
        os.makedirs(path, exist_ok=False)
        return path

    def create_file(self, parent: str, name: str, text: str = "") -> str:
        """Create a uniquely named empty text file and return its path."""
        desired = unique_child_name(parent, name)
        path = os.path.join(parent, desired)
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def free_space(self, path: str) -> Optional[Tuple[int, int, int]]:
        """Return ``(total, used, free)`` bytes for the mount of *path*."""
        return disk_usage(path)


    def rename(self, path: str, new_name: str) -> str:
        """Rename one entry; refuse to overwrite an existing sibling."""
        name = validate_name(new_name)
        parent = os.path.dirname(path)
        target = os.path.join(parent, name)
        if os.path.abspath(target) == os.path.abspath(path):
            return path
        if os.path.lexists(target):
            raise FileExistsError(f"{name} already exists")
        os.rename(path, target)
        return target

    def copy_paths(self, paths: Sequence[str], destination: str) -> List[str]:
        """Copy files/folders into *destination*, avoiding overwrites."""
        copied: List[str] = []
        for source in paths:
            source = os.path.abspath(source)
            if not os.path.lexists(source):
                raise FileNotFoundError(source)
            if os.path.isdir(source) and _is_within(destination, source):
                raise ValueError("Cannot copy a folder into itself")
            name = unique_child_name(destination, os.path.basename(source.rstrip(os.sep)))
            target = os.path.join(destination, name)
            if os.path.isdir(source) and not os.path.islink(source):
                shutil.copytree(source, target, symlinks=True)
            elif os.path.islink(source):
                os.symlink(os.readlink(source), target)
            else:
                shutil.copy2(source, target)
            copied.append(target)
        return copied

    def move_paths(self, paths: Sequence[str], destination: str) -> List[str]:
        """Move files/folders into *destination*, avoiding overwrites."""
        moved: List[str] = []
        for source in paths:
            source = os.path.abspath(source)
            if not os.path.lexists(source):
                raise FileNotFoundError(source)
            if os.path.dirname(source) == os.path.abspath(destination):
                continue
            if os.path.isdir(source) and _is_within(destination, source):
                raise ValueError("Cannot move a folder into itself")
            name = unique_child_name(destination, os.path.basename(source.rstrip(os.sep)))
            target = os.path.join(destination, name)
            shutil.move(source, target)
            moved.append(target)
        return moved

    def trash_paths(self, paths: Sequence[str]) -> None:
        """Move every entry to the trash (raises on the first failure)."""
        for path in paths:
            self._trash(path)

    def delete_paths(self, paths: Sequence[str]) -> None:
        """Permanently delete every entry (raises on the first failure)."""
        for path in paths:
            self._delete(path)

    @staticmethod
    def _default_delete(path: str) -> None:
        if os.path.islink(path) or not os.path.isdir(path):
            os.unlink(path)
        else:
            shutil.rmtree(path)

    def preview_text(self, path: str, limit: int = 8192) -> Optional[str]:
        """Return a text preview, or ``None`` for binary/unreadable files."""
        try:
            raw = self._read_bytes(path, limit + 1)
        except OSError:
            return None
        if b"\0" in raw[:4096]:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except UnicodeDecodeError:
                return None
        if len(raw) > limit:
            text = text[:limit] + "\n…"
        return text


# ---------------------------------------------------------------------------
# GTK view
# ---------------------------------------------------------------------------

class FileExplorerModule(BaseModule):
    """A Windows-Explorer-style file browser (id ``files``)."""

    id = "files"
    name = "File Explorer"
    icon = "\U0001F4C1"
    description = (
        "Browse folders, preview files and manage copy, move, rename and "
        "trash operations."
    )
    group = "Tools"

    _STATE_KEY = "files"
    _COLUMNS = (
        ("name", "Name"),
        ("size", "Size"),
        ("type", "Type"),
        ("modified", "Modified"),
    )

    # -- commands ------------------------------------------------------------
    def commands(self):
        def ready() -> bool:
            return getattr(self, "_listbox", None) is not None

        def has_selection() -> bool:
            return ready() and bool(self._selected_entries())

        def single_selection() -> bool:
            return ready() and len(self._selected_entries()) == 1

        return (
            Command(
                "files.back",
                "File Explorer: Back",
                self._go_back,
                category="File Explorer",
                default_keys=("alt+left",),
                scope="files",
                when=lambda: ready() and self._history.can_back,
            ),
            Command(
                "files.forward",
                "File Explorer: Forward",
                self._go_forward,
                category="File Explorer",
                default_keys=("alt+right",),
                scope="files",
                when=lambda: ready() and self._history.can_forward,
            ),
            Command(
                "files.up",
                "File Explorer: Parent folder",
                self._go_up,
                category="File Explorer",
                default_keys=("alt+up",),
                scope="files",
                when=lambda: ready() and self._history.current not in (None, "/"),
            ),
            Command(
                "files.home",
                "File Explorer: Home",
                self._go_home,
                category="File Explorer",
                default_keys=("alt+home",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.refresh",
                "Refresh File Explorer",
                self._command_refresh,
                category="File Explorer",
                default_keys=("f5",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.focus_location",
                "File Explorer: Edit location",
                self._focus_location,
                category="File Explorer",
                default_keys=("ctrl+l",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.search",
                "File Explorer: Filter this folder",
                self._focus_search,
                category="File Explorer",
                default_keys=("ctrl+f",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.toggle_hidden",
                "File Explorer: Toggle hidden files",
                self._toggle_hidden,
                category="File Explorer",
                default_keys=("ctrl+h",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.toggle_preview",
                "File Explorer: Toggle preview pane",
                self._toggle_preview,
                category="File Explorer",
                default_keys=("f9",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.new_folder",
                "File Explorer: New folder",
                self._new_folder,
                category="File Explorer",
                default_keys=("ctrl+shift+n",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.new_file",
                "File Explorer: New file",
                self._new_file,
                category="File Explorer",
                scope="files",
                when=ready,
            ),
            Command(
                "files.terminal",
                "File Explorer: Open terminal here",
                self._open_terminal,
                category="File Explorer",
                default_keys=("ctrl+alt+t",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.rename",
                "File Explorer: Rename",
                self._rename_selection,
                category="File Explorer",
                default_keys=("f2",),
                scope="files",
                when=single_selection,
            ),
            Command(
                "files.copy",
                "File Explorer: Copy",
                lambda: self._copy_selection(False),
                category="File Explorer",
                default_keys=("ctrl+c",),
                scope="files",
                when=has_selection,
            ),
            Command(
                "files.cut",
                "File Explorer: Cut",
                lambda: self._copy_selection(True),
                category="File Explorer",
                default_keys=("ctrl+x",),
                scope="files",
                when=has_selection,
            ),
            Command(
                "files.paste",
                "File Explorer: Paste",
                self._paste,
                category="File Explorer",
                default_keys=("ctrl+v",),
                scope="files",
                when=ready,
            ),
            Command(
                "files.trash",
                "File Explorer: Move to Trash",
                self._trash_selection,
                category="File Explorer",
                default_keys=("delete",),
                scope="files",
                when=has_selection,
            ),
            Command(
                "files.delete",
                "File Explorer: Delete permanently",
                self._delete_selection,
                category="File Explorer",
                default_keys=("shift+delete",),
                scope="files",
                when=has_selection,
            ),
            Command(
                "files.properties",
                "File Explorer: Properties",
                self._show_properties,
                category="File Explorer",
                default_keys=("ctrl+i",),
                scope="files",
                when=single_selection,
            ),
            Command(
                "files.copy_path",
                "File Explorer: Copy path",
                self._copy_path,
                category="File Explorer",
                scope="files",
                when=has_selection,
            ),
            Command(
                "files.open_in_editor",
                "File Explorer: Open in Text Editor",
                self._open_selection_in_editor,
                category="File Explorer",
                scope="files",
                when=single_selection,
            ),
        )

    # -- command callbacks ---------------------------------------------------
    def _command_refresh(self) -> bool:
        if getattr(self, "_listbox", None) is None:
            return False
        self._refresh()
        return True

    def _focus_location(self) -> bool:
        if getattr(self, "_path_entry", None) is None:
            return False
        self._path_entry.grab_focus()
        self._path_entry.select_region(0, -1)
        return True

    def _focus_search(self) -> bool:
        if getattr(self, "_search_entry", None) is None:
            return False
        self._search_entry.grab_focus()
        self._search_entry.select_region(0, -1)
        return True

    def _toggle_hidden(self) -> bool:
        if getattr(self, "_hidden_button", None) is None:
            return False
        self._hidden_button.set_active(not self._hidden_button.get_active())
        return True

    def _toggle_preview(self) -> bool:
        if getattr(self, "_preview_button", None) is None:
            return False
        self._preview_button.set_active(not self._preview_button.get_active())
        return True

    def _go_back(self) -> bool:
        if getattr(self, "_history", None) is None:
            return False
        target = self._history.back()
        if not target:
            return False
        self._navigate(target, record=False)
        return True

    def _go_forward(self) -> bool:
        if getattr(self, "_history", None) is None:
            return False
        target = self._history.forward()
        if not target:
            return False
        self._navigate(target, record=False)
        return True

    def _go_up(self) -> bool:
        if getattr(self, "_history", None) is None:
            return False
        current = self._history.current
        if not current or current == os.path.abspath(os.sep):
            return False
        self._navigate(os.path.dirname(current))
        return True

    def _go_home(self) -> bool:
        if not getattr(self, "_model", None):
            return False
        self._navigate(self._model.home)
        return True

    # -- construction --------------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        self._model = FileExplorer()
        self._history = NavigationHistory()
        self._sort_key = "name"
        self._sort_desc = False
        self._show_hidden = False
        self._entries: List[FileEntry] = []
        self._rows: Dict[str, object] = {}
        self._clipboard: List[str] = []
        self._clipboard_cut = False
        self._prompt_dialog = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.get_style_context().add_class("files-shell")
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(10)
        root.set_margin_end(10)

        root.append(self._build_toolbar())

        self._main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._main_paned.set_vexpand(True)
        self._main_paned.set_position(196)
        self._main_paned.set_start_child(self._build_sidebar())
        self._main_paned.set_end_child(self._build_content())
        root.append(self._main_paned)

        self._root = root

        start = self._load_start_path()
        self._history.replace_current(start)
        self._refresh()
        return root

    def _toolbar_button(self, label: str, tooltip: str, callback):
        from gi.repository import Gtk

        button = Gtk.Button(label=label)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda _button: callback())
        return button

    def _build_toolbar(self):
        from gi.repository import Gtk

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrap.get_style_context().add_class("files-toolbar")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._back_button = self._toolbar_button(
            "←", tr("Back"), self._go_back
        )
        self._forward_button = self._toolbar_button(
            "→", tr("Forward"), self._go_forward
        )
        self._up_button = self._toolbar_button(
            "↑", tr("Parent folder"), self._go_up
        )
        self._refresh_button = self._toolbar_button(
            "⟳", tr("Refresh"), self._command_refresh
        )
        self._new_folder_button = self._toolbar_button(
            "＋", tr("New folder"), self._new_folder
        )
        self._new_file_button = self._toolbar_button(
            "🗋", tr("New file"), self._new_file
        )
        self._terminal_button = self._toolbar_button(
            ">_", tr("Open terminal here"), self._open_terminal
        )
        for button in (
            self._back_button,
            self._forward_button,
            self._up_button,
            self._refresh_button,
            self._new_folder_button,
            self._new_file_button,
            self._terminal_button,
        ):
            bar.append(button)

        self._path_entry = Gtk.Entry()
        self._path_entry.set_hexpand(True)
        self._path_entry.set_placeholder_text(tr("Location"))
        self._path_entry.get_style_context().add_class("files-location")
        self._path_entry.connect("activate", self._on_location_activated)
        bar.append(self._path_entry)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_width_chars(18)
        self._search_entry.set_placeholder_text(tr("Filter this folder…"))
        self._search_entry.connect("search-changed", self._on_search_changed)
        bar.append(self._search_entry)

        self._hidden_button = Gtk.ToggleButton(label=tr("Hidden"))
        self._hidden_button.set_tooltip_text(tr("Show hidden files"))
        self._hidden_button.get_style_context().add_class("omni-pill")
        self._hidden_button.connect("toggled", self._on_hidden_toggled)
        bar.append(self._hidden_button)

        self._preview_button = Gtk.ToggleButton(label=tr("Preview"))
        self._preview_button.set_tooltip_text(tr("Show the preview pane"))
        self._preview_button.get_style_context().add_class("omni-pill")
        self._preview_button.set_active(True)
        self._preview_button.connect("toggled", self._on_preview_toggled)
        bar.append(self._preview_button)
        wrap.append(bar)

        crumbs_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._breadcrumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self._breadcrumb_box.get_style_context().add_class("files-breadcrumb")
        self._breadcrumb_box.set_hexpand(True)
        crumbs_row.append(self._breadcrumb_box)

        self._free_space = Gtk.Label(label="")
        self._free_space.get_style_context().add_class("omnify-dim")
        self._free_space.get_style_context().add_class("omni-row-meta")
        crumbs_row.append(self._free_space)
        wrap.append(crumbs_row)
        return wrap

    def _build_sidebar(self):
        from gi.repository import Gtk, Pango

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        shell.get_style_context().add_class("files-sidebar")
        shell.set_size_request(178, -1)

        title = Gtk.Label(label=tr("Places"), xalign=0.0)
        title.get_style_context().add_class("omni-section-title")
        title.set_margin_start(4)
        title.set_margin_top(2)
        shell.append(title)

        self._places_list = Gtk.ListBox()
        self._places_list.get_style_context().add_class("omni-list")
        self._places_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._places_list.connect("row-activated", self._on_place_activated)
        for place in self._model.places():
            row = Gtk.ListBoxRow()
            row._omnify_path = place.path
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
            line.set_margin_top(5)
            line.set_margin_bottom(5)
            line.set_margin_start(6)
            line.set_margin_end(6)
            tile = Gtk.Box()
            tile.get_style_context().add_class("omni-icon-tile")
            tile.get_style_context().add_class("omni-icon-tile-neutral")
            icon = Gtk.Image.new_from_icon_name(place.icon)
            icon.set_pixel_size(16)
            tile.append(icon)
            label = Gtk.Label(label=tr(place.label), xalign=0.0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(True)
            line.append(tile)
            line.append(label)
            row.set_child(line)
            self._places_list.append(row)
        shell.append(self._places_list)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        shell.append(spacer)

        usage_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        usage_card.get_style_context().add_class("omni-panel-flat")
        usage_title = Gtk.Label(label=tr("Disk space"), xalign=0.0)
        usage_title.get_style_context().add_class("omni-section-title")
        usage_card.append(usage_title)
        self._sidebar_usage = Gtk.Label(label="—", xalign=0.0)
        self._sidebar_usage.get_style_context().add_class("omni-row-meta")
        self._sidebar_usage.set_wrap(True)
        usage_card.append(self._sidebar_usage)
        shell.append(usage_card)
        return shell

    def _build_content(self):
        from gi.repository import Gtk

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(560)

        list_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.get_style_context().add_class("tm-header")
        self._header_buttons: Dict[str, object] = {}
        for key, label in self._COLUMNS:
            button = Gtk.Button(label=tr(label))
            button.set_halign(Gtk.Align.START)
            if key == "name":
                button.set_hexpand(True)
            button.connect("clicked", self._on_sort_clicked, key)
            header.append(button)
            self._header_buttons[key] = button
        list_area.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self._listbox = Gtk.ListBox()
        self._listbox.get_style_context().add_class("files-list")
        self._listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self._listbox.set_activate_on_single_click(False)
        self._listbox.connect("row-activated", self._on_row_activated)
        self._listbox.connect("selected-rows-changed", self._on_selection_changed)
        scroller.set_child(self._listbox)
        list_area.append(scroller)

        self._status = Gtk.Label(label="")
        self._status.get_style_context().add_class("files-status")
        self._status.set_xalign(0.0)
        self._status.set_margin_start(6)
        list_area.append(self._status)
        paned.set_start_child(list_area)

        self._preview_scroller = Gtk.ScrolledWindow()
        self._preview_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._preview_scroller.set_size_request(290, -1)
        self._preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._preview_box.set_margin_top(2)
        self._preview_box.set_margin_bottom(8)
        self._preview_box.set_margin_start(4)
        self._preview_box.set_margin_end(4)
        self._preview_scroller.set_child(self._preview_box)
        paned.set_end_child(self._preview_scroller)
        self._content_paned = paned
        return paned

    # -- startup -------------------------------------------------------------
    def _load_start_path(self) -> str:
        fallback = self._model.home
        try:
            state = self.ctx.storage.load(self._STATE_KEY, {}) if self.ctx else {}
        except Exception:
            state = {}
        path = state.get("path") if isinstance(state, dict) else None
        if isinstance(path, str) and os.path.isdir(path):
            return path
        return fallback

    def _save_state(self) -> None:
        current = self._history.current if getattr(self, "_history", None) else None
        if not current or self.ctx is None:
            return
        try:
            self.ctx.storage.save(self._STATE_KEY, {"path": current})
        except Exception:
            pass

    # -- navigation ----------------------------------------------------------
    def _navigate(self, path: str, record: bool = True) -> bool:
        target = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(target):
            self._notify(tr("Folder not found: {path}", path=target))
            self._path_entry.set_text(self._history.current or "")
            return False
        if record:
            self._history.visit(target)
        else:
            self._history.replace_current(target)
        self._refresh()
        self._save_state()
        return True

    def _on_location_activated(self, _entry) -> None:
        current = self._history.current or self._model.home
        target = resolve_location(
            self._path_entry.get_text(), current, self._model.home
        )
        self._navigate(target)

    def _on_place_activated(self, _listbox, row) -> None:
        path = getattr(row, "_omnify_path", None)
        if path:
            self._navigate(path)

    def _update_nav_buttons(self) -> None:
        current = self._history.current
        self._back_button.set_sensitive(self._history.can_back)
        self._forward_button.set_sensitive(self._history.can_forward)
        self._up_button.set_sensitive(
            bool(current) and current != os.path.abspath(os.sep)
        )
        self._path_entry.set_text(current or "")
        self._update_breadcrumbs()
        self._update_free_space()

    def _update_breadcrumbs(self) -> None:
        """Rebuild the clickable path trail below the toolbar."""
        if getattr(self, "_breadcrumb_box", None) is None:
            return
        from gi.repository import Gtk

        while True:
            child = self._breadcrumb_box.get_first_child()
            if child is None:
                break
            self._breadcrumb_box.remove(child)
        current = self._history.current
        if not current:
            return
        crumbs = breadcrumb_parts(current, self._model.home)
        show = crumbs
        if len(crumbs) > 6:
            show = crumbs[:1] + [("…", "")] + crumbs[-4:]
        for index, (label, path) in enumerate(show):
            if path:
                button = Gtk.Button(
                    label=tr(label) if label in ("Home", "File System") else label
                )
                button.connect(
                    "clicked", lambda _button, target=path: self._navigate(target)
                )
                self._breadcrumb_box.append(button)
            else:
                gap = Gtk.Label(label=label)
                gap.get_style_context().add_class("omnify-dim")
                self._breadcrumb_box.append(gap)
            if index < len(show) - 1:
                separator = Gtk.Label(label="›")
                separator.get_style_context().add_class("omnify-dim")
                self._breadcrumb_box.append(separator)

    def _update_free_space(self) -> None:
        """Show the free space of the mount holding the current folder."""
        if getattr(self, "_free_space", None) is None:
            return
        current = self._history.current if getattr(self, "_history", None) else None
        usage = self._model.free_space(current) if current else None
        if usage is None:
            self._free_space.set_label("")
            self._sidebar_usage.set_label(tr("Not available"))
            return
        total, used, free = usage
        percent = (100.0 * used / total) if total else 0.0
        self._free_space.set_label(
            tr("{free} free", free=format_file_size(free))
        )
        self._sidebar_usage.set_label(
            tr(
                "{free} free of {total}\n{percent}% used",
                free=format_file_size(free),
                total=format_file_size(total),
                percent=f"{percent:.0f}",
            )
        )

    # -- listing and sorting -------------------------------------------------
    def _refresh(self) -> None:
        current = self._history.current
        if not current:
            return
        try:
            entries = self._model.list_entries(current, self._show_hidden)
        except OSError as exc:
            self._notify(tr("Could not open folder: {error}", error=str(exc)))
            entries = []
        self._entries = sort_entries(entries, self._sort_key, self._sort_desc)
        self._render_rows()
        self._update_nav_buttons()
        self._update_sort_headers()
        self._update_status()
        self._update_preview()

    def _visible_entries(self) -> List[FileEntry]:
        return filter_entries(self._entries, self._search_entry.get_text())

    def _render_rows(self) -> None:
        from gi.repository import Gtk, Pango

        while True:
            child = self._listbox.get_first_child()
            if child is None:
                break
            self._listbox.remove(child)
        self._rows.clear()
        visible = self._visible_entries()
        if not visible:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            empty.get_style_context().add_class("omni-empty")
            empty.set_margin_top(18)
            empty.set_margin_bottom(18)
            empty.set_margin_start(18)
            empty.set_margin_end(18)
            headline = Gtk.Label(label=tr("This folder is empty"), xalign=0.0)
            headline.get_style_context().add_class("omni-row-title")
            hint = Gtk.Label(
                label=tr("Create a folder or file, or drop something here."),
                xalign=0.0,
            )
            hint.get_style_context().add_class("omni-row-sub")
            empty.append(headline)
            empty.append(hint)
            row.set_child(empty)
            self._listbox.append(row)
            self._listbox.unselect_all()
            return
        for entry in visible:
            row = Gtk.ListBoxRow()
            row._omnify_path = entry.path
            row._omnify_entry = entry
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            line.set_margin_top(5)
            line.set_margin_bottom(5)
            line.set_margin_start(7)
            line.set_margin_end(9)

            tile = Gtk.Box()
            tile.get_style_context().add_class("omni-icon-tile")
            icon = Gtk.Image.new_from_icon_name(self._icon_name(entry))
            icon.set_pixel_size(18)
            tile.append(icon)
            line.append(tile)

            name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            name_box.set_hexpand(True)
            name = Gtk.Label(label=entry.name, xalign=0.0)
            name.get_style_context().add_class("files-row-name")
            name.set_ellipsize(Pango.EllipsizeMode.END)
            if entry.name.startswith("."):
                name.get_style_context().add_class("omnify-dim")
            name_box.append(name)
            subtitle = Gtk.Label(
                label=entry.path if entry.is_dir else tr("Open with the default app"),
                xalign=0.0,
            )
            subtitle.get_style_context().add_class("omni-row-sub")
            subtitle.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name_box.append(subtitle)
            line.append(name_box)

            size = Gtk.Label(
                label="—" if entry.is_dir else format_file_size(entry.size),
                xalign=1.0,
            )
            size.set_width_chars(10)
            size.get_style_context().add_class("files-row-size")
            size.get_style_context().add_class("omnify-mono")
            line.append(size)

            kind = Gtk.Label(label=tr(entry.kind), xalign=0.0)
            kind.set_width_chars(15)
            kind.set_ellipsize(Pango.EllipsizeMode.END)
            kind.get_style_context().add_class("files-row-kind")
            line.append(kind)

            modified = Gtk.Label(
                label=format_modified(entry.modified), xalign=0.0
            )
            modified.set_width_chars(16)
            modified.get_style_context().add_class("files-row-date")
            modified.get_style_context().add_class("omnify-mono")
            line.append(modified)

            row.set_child(line)
            row.set_tooltip_text(entry.path)
            gesture = Gtk.GestureClick()
            gesture.set_button(3)
            gesture.connect("pressed", self._on_row_right_press, row)
            row.add_controller(gesture)
            self._listbox.append(row)
            self._rows[entry.path] = row
        self._listbox.unselect_all()

    def _update_sort_headers(self) -> None:
        for key, label in self._COLUMNS:
            button = self._header_buttons.get(key)
            if button is None:
                continue
            arrow = ""
            if key == self._sort_key:
                arrow = " " + ("▼" if self._sort_desc else "▲")
            button.set_label(tr(label) + arrow)

    def _on_sort_clicked(self, _button, key: str) -> None:
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = False
        self._entries = sort_entries(
            self._entries, self._sort_key, self._sort_desc
        )
        self._render_rows()
        self._update_sort_headers()
        self._update_status()
        self._update_preview()

    def _on_search_changed(self, _entry) -> None:
        if getattr(self, "_listbox", None) is None:
            return
        self._render_rows()
        self._update_status()
        self._update_preview()

    def _on_hidden_toggled(self, button) -> None:
        self._show_hidden = button.get_active()
        if getattr(self, "_history", None) and self._history.current:
            self._refresh()

    def _on_preview_toggled(self, button) -> None:
        self._preview_scroller.set_visible(button.get_active())

    # -- selection and status ------------------------------------------------
    def _selected_rows(self) -> List[object]:
        try:
            return list(self._listbox.get_selected_rows())
        except Exception:
            return []

    def _selected_entries(self) -> List[FileEntry]:
        paths: List[str] = []
        for row in self._selected_rows():
            path = getattr(row, "_omnify_path", None)
            if path:
                paths.append(path)
        known = {entry.path: entry for entry in self._entries}
        return [known[path] for path in paths if path in known]

    def _update_status(self) -> None:
        visible = len(self._visible_entries())
        selected = len(self._selected_entries())
        text = tr("{count} items", count=visible)
        if selected:
            text += " · " + tr("{count} selected", count=selected)
        self._status.set_label(text)

    def _on_selection_changed(self, _listbox) -> None:
        self._update_status()
        self._update_preview()

    def _on_row_activated(self, _listbox, row) -> None:
        entry = getattr(row, "_omnify_entry", None)
        if entry is not None:
            self._open_entry(entry)

    # -- opening entries -----------------------------------------------------
    def _open_entry(self, entry: FileEntry) -> None:
        if entry.is_dir:
            self._navigate(entry.path)
            return
        try:
            from gi.repository import Gio, Gtk

            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(entry.path))
            launcher.launch(self._root.get_root(), None, self._on_launch_done)
        except Exception as exc:
            self._notify(tr("Could not open file: {error}", error=str(exc)))

    def _on_launch_done(self, launcher, result) -> None:
        try:
            launcher.launch_finish(result)
        except Exception as exc:
            self._notify(tr("Could not open file: {error}", error=str(exc)))

    def _open_selection_in_editor(self) -> bool:
        entries = self._selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            return False
        editor = self.ctx.get_module("editor") if self.ctx else None
        opener = getattr(editor, "open_path", None)
        if not callable(opener):
            self._notify(tr("Text Editor is not available"))
            return False
        try:
            self.ctx.open_module("editor")
            opener(entries[0].path)
        except Exception as exc:
            self._notify(tr("Could not open the file: {error}", error=str(exc)))
            return False
        return True

    # -- context menu --------------------------------------------------------
    def _popup_menu(self, anchor, items) -> None:
        from gi.repository import Gtk

        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, action in items:
            button = Gtk.Button(label=tr(label))
            button.set_halign(Gtk.Align.FILL)
            button.set_margin_top(1)
            button.set_margin_bottom(1)
            button.connect("clicked", self._on_menu_action, action)
            menu_box.append(button)
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pad.set_margin_top(8)
        pad.set_margin_bottom(8)
        pad.set_margin_start(8)
        pad.set_margin_end(8)
        pad.append(menu_box)
        popover = Gtk.PopoverMenu()
        popover.set_child(pad)
        popover.set_parent(anchor)
        popover.popup()
        child = menu_box.get_first_child()
        while child is not None:
            child.connect("clicked", popover.popdown)
            child = child.get_next_sibling()

    def _on_menu_action(self, _button, action) -> None:
        action()

    def _on_row_right_press(self, _gesture, _n, _x, _y, row) -> None:
        if row not in self._selected_rows():
            self._listbox.unselect_all()
            self._listbox.select_row(row)
        entries = self._selected_entries()
        if not entries:
            return
        items = []
        if len(entries) == 1:
            entry = entries[0]
            items.append(("Open", lambda e=entry: self._open_entry(e)))
            if not entry.is_dir:
                items.append(
                    (
                        "Open in Text Editor",
                        lambda e=entry: self._open_in_editor(e),
                    )
                )
            items.append(("New folder", self._new_folder))
            items.append(("Rename", self._rename_selection))
        items.append(("Copy", lambda: self._copy_selection(False)))
        items.append(("Cut", lambda: self._copy_selection(True)))
        items.append(("Paste", self._paste))
        items.append(("Move to Trash", self._trash_selection))
        items.append(("Delete permanently", self._delete_selection))
        if len(entries) == 1:
            items.append(("Copy path", self._copy_path))
            items.append(("Properties", self._show_properties))
        self._popup_menu(row, items)

    def _open_in_editor(self, entry: FileEntry) -> None:
        editor = self.ctx.get_module("editor") if self.ctx else None
        opener = getattr(editor, "open_path", None)
        if not callable(opener):
            self._notify(tr("Text Editor is not available"))
            return
        try:
            self.ctx.open_module("editor")
            opener(entry.path)
        except Exception as exc:
            self._notify(tr("Could not open the file: {error}", error=str(exc)))

    # -- clipboard -----------------------------------------------------------
    def _copy_selection(self, cut: bool) -> bool:
        entries = self._selected_entries()
        if not entries:
            return False
        self._clipboard = [entry.path for entry in entries]
        self._clipboard_cut = cut
        count = len(entries)
        self._notify(
            tr("{count} item(s) ready to move", count=count)
            if cut
            else tr("{count} item(s) ready to copy", count=count)
        )
        return True

    def _paste(self) -> bool:
        if not getattr(self, "_clipboard", None):
            self._notify(tr("Clipboard is empty"))
            return False
        destination = self._history.current
        if not destination:
            return False
        try:
            if self._clipboard_cut:
                result = self._model.move_paths(self._clipboard, destination)
            else:
                result = self._model.copy_paths(self._clipboard, destination)
        except Exception as exc:
            self._notify(tr("Paste failed: {error}", error=str(exc)))
            return False
        if self._clipboard_cut:
            self._clipboard = []
            self._clipboard_cut = False
        self._refresh()
        self._notify(tr("Pasted {count} item(s)", count=len(result)))
        return True

    # -- new folder / rename -------------------------------------------------
    def _prompt_text(self, title: str, label: str, initial: str, callback) -> None:
        from gi.repository import Gtk

        dialog = Gtk.Dialog()
        dialog.set_title(title)
        dialog.set_modal(True)
        try:
            dialog.set_transient_for(self._root.get_root())
        except Exception:
            pass
        dialog.add_button(tr("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(tr("OK"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_activates_default(True)
        entry.select_region(0, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(Gtk.Label(label=label, xalign=0.0))
        box.append(entry)
        dialog.get_content_area().append(box)

        def on_response(dlg, response) -> None:
            value = entry.get_text() if response == Gtk.ResponseType.OK else None
            dlg.destroy()
            self._prompt_dialog = None
            callback(value)

        dialog.connect("response", on_response)
        self._prompt_dialog = dialog
        dialog.show()
        entry.grab_focus()

    def _new_folder(self) -> bool:
        current = self._history.current if getattr(self, "_history", None) else None
        if not current:
            return False
        try:
            suggestion = unique_child_name(current, tr("New folder"))
        except ValueError:
            suggestion = tr("New folder")
        self._prompt_text(
            tr("New folder"),
            tr("Folder name"),
            suggestion,
            self._create_folder,
        )
        return True

    def _create_folder(self, name: Optional[str]) -> None:
        if name is None:
            return
        try:
            path = self._model.create_folder(self._history.current, name)
        except Exception as exc:
            self._notify(tr("Could not create the folder: {error}", error=str(exc)))
            return
        self._refresh()
        self._notify(tr("Folder created: {name}", name=os.path.basename(path)))

    def _new_file(self) -> bool:
        current = self._history.current if getattr(self, "_history", None) else None
        if not current:
            return False
        try:
            suggestion = unique_child_name(current, tr("New file.txt"))
        except ValueError:
            suggestion = tr("New file.txt")
        self._prompt_text(
            tr("New file"),
            tr("File name"),
            suggestion,
            self._create_file,
        )
        return True

    def _create_file(self, name: Optional[str]) -> None:
        if name is None:
            return
        try:
            path = self._model.create_file(self._history.current, name)
        except Exception as exc:
            self._notify(tr("Could not create the file: {error}", error=str(exc)))
            return
        self._refresh()
        row = self._rows.get(path)
        if row is not None:
            self._listbox.select_row(row)
        self._notify(tr("File created: {name}", name=os.path.basename(path)))

    def _open_terminal(self) -> bool:
        current = self._history.current if getattr(self, "_history", None) else None
        if not current:
            return False
        terminal = find_terminal()
        if terminal is None:
            self._notify(tr("No terminal emulator was found"))
            return False
        argv = terminal_argv(terminal, current)
        try:
            subprocess.Popen(
                argv,
                cwd=current,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._notify(tr("Could not open the terminal: {error}", error=str(exc)))
            return False
        self._notify(tr("Terminal opened in this folder"))
        return True

    def _rename_selection(self) -> bool:
        entries = self._selected_entries()
        if len(entries) != 1:
            return False
        entry = entries[0]
        self._prompt_text(
            tr("Rename"),
            tr("New name"),
            entry.name,
            lambda name: self._rename_entry(entry, name),
        )
        return True

    def _rename_entry(self, entry: FileEntry, name: Optional[str]) -> None:
        if name is None or name == entry.name:
            return
        try:
            target = self._model.rename(entry.path, name)
        except Exception as exc:
            self._notify(tr("Rename failed: {error}", error=str(exc)))
            return
        self._refresh()
        row = self._rows.get(target)
        if row is not None:
            self._listbox.select_row(row)
        self._notify(tr("Renamed to {name}", name=os.path.basename(target)))

    # -- trash / delete / properties ----------------------------------------
    def _confirm(self, message: str, detail: str, confirm_label: str) -> bool:
        from gi.repository import Gtk

        dialog = Gtk.AlertDialog()
        dialog.set_message(message)
        dialog.set_detail(detail)
        dialog.set_buttons([tr("Cancel"), confirm_label])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)
        dialog.set_modal(True)
        try:
            response = dialog.choose(self._root.get_root())
        except Exception:
            return False
        return response == 1

    def _selection_detail(self, entries: Sequence[FileEntry]) -> str:
        names = [entry.name for entry in entries]
        shown = "\n".join(names[:8])
        if len(names) > 8:
            shown += "\n" + tr("… and {count} more", count=len(names) - 8)
        return shown

    def _trash_selection(self) -> bool:
        entries = self._selected_entries()
        if not entries:
            return False
        if not self._confirm(
            tr("Move {count} item(s) to Trash?", count=len(entries)),
            self._selection_detail(entries),
            tr("Move to Trash"),
        ):
            return False
        try:
            self._model.trash_paths([entry.path for entry in entries])
        except Exception as exc:
            self._notify(tr("Could not move to Trash: {error}", error=str(exc)))
            return False
        self._refresh()
        self._notify(tr("Moved {count} item(s) to Trash", count=len(entries)))
        return True

    def _delete_selection(self) -> bool:
        entries = self._selected_entries()
        if not entries:
            return False
        if not self._confirm(
            tr("Delete {count} item(s) permanently?", count=len(entries)),
            tr("This cannot be undone.")
            + "\n\n"
            + self._selection_detail(entries),
            tr("Delete permanently"),
        ):
            return False
        try:
            self._model.delete_paths([entry.path for entry in entries])
        except Exception as exc:
            self._notify(tr("Delete failed: {error}", error=str(exc)))
            return False
        self._refresh()
        self._notify(tr("Deleted {count} item(s)", count=len(entries)))
        return True

    def _show_properties(self) -> bool:
        entries = self._selected_entries()
        if len(entries) != 1:
            return False
        entry = entries[0]
        rows = self._model.describe(entry.path)
        detail = "\n".join(f"{tr(label)}: {value}" for label, value in rows)
        from gi.repository import Gtk

        dialog = Gtk.AlertDialog()
        dialog.set_message(entry.name)
        dialog.set_detail(detail)
        dialog.set_buttons([tr("OK")])
        dialog.set_modal(True)
        try:
            dialog.choose(self._root.get_root())
        except Exception:
            pass
        return True

    def _copy_path(self) -> bool:
        entries = self._selected_entries()
        if not entries:
            return False
        text = "\n".join(entry.path for entry in entries)
        try:
            from gi.repository import GObject

            clipboard = self._root.get_clipboard()
            value = GObject.Value()
            value.init(GObject.TYPE_STRING)
            value.set_string(text)
            clipboard.set(value)
        except Exception as exc:
            self._notify(tr("Could not copy the path: {error}", error=str(exc)))
            return False
        self._notify(tr("Path copied"))
        return True

    # -- preview -------------------------------------------------------------
    def _clear_preview(self) -> None:
        while True:
            child = self._preview_box.get_first_child()
            if child is None:
                break
            self._preview_box.remove(child)

    def _preview_heading(self, icon_name: str, title: str) -> None:
        from gi.repository import Gtk, Pango

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tile = Gtk.Box()
        tile.get_style_context().add_class("omni-icon-tile")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(26)
        tile.append(icon)
        header.append(tile)

        title_label = Gtk.Label(label=title, xalign=0.0)
        title_label.get_style_context().add_class("files-preview-title")
        title_label.set_hexpand(True)
        title_label.set_wrap(True)
        title_label.set_selectable(True)
        header.append(title_label)
        self._preview_card.append(header)

    def _preview_chip(self, label: str, value: str) -> None:
        from gi.repository import Gtk, Pango

        chip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        chip.get_style_context().add_class("omni-chip")
        caption = Gtk.Label(label=tr(label), xalign=0.0)
        caption.get_style_context().add_class("omni-chip-label")
        content = Gtk.Label(label=value, xalign=0.0)
        content.get_style_context().add_class("omni-chip-value")
        content.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        chip.append(caption)
        chip.append(content)
        self._preview_meta.append(chip)

    def _preview_row(self, label: str, value: str) -> None:
        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name = Gtk.Label(label=tr(label), xalign=0.0)
        name.get_style_context().add_class("omni-section-title")
        content = Gtk.Label(label=value, xalign=0.0)
        content.get_style_context().add_class("omni-row-meta")
        content.set_wrap(True)
        content.set_selectable(True)
        box.append(name)
        box.append(content)
        self._preview_card.append(box)

    def _preview_empty(self, icon_name: str, title: str, hint: str) -> None:
        from gi.repository import Gtk

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        empty.get_style_context().add_class("omni-empty")
        empty.set_valign(Gtk.Align.START)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(38)
        icon.set_halign(Gtk.Align.START)
        icon.get_style_context().add_class("omnify-dim")
        empty.append(icon)
        headline = Gtk.Label(label=title, xalign=0.0)
        headline.get_style_context().add_class("omni-row-title")
        headline.set_wrap(True)
        empty.append(headline)
        detail = Gtk.Label(label=hint, xalign=0.0)
        detail.get_style_context().add_class("omni-row-sub")
        detail.set_wrap(True)
        empty.append(detail)
        self._preview_box.append(empty)

    def _update_preview(self) -> None:
        if getattr(self, "_preview_box", None) is None:
            return
        from gi.repository import Gtk, Pango

        entries = self._selected_entries()
        self._clear_preview()
        if not entries:
            self._preview_empty(
                "folder-open-symbolic",
                tr("Nothing selected"),
                tr("Pick a file or folder to see its details and a preview."),
            )
            return
        if len(entries) > 1:
            self._preview_empty(
                "edit-select-all-symbolic",
                tr("{count} items selected", count=len(entries)),
                tr("Use the context menu to copy, move or delete them together."),
            )
            return

        entry = entries[0]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        card.get_style_context().add_class("files-preview")
        self._preview_box.append(card)
        self._preview_card = card
        self._preview_heading(self._icon_name(entry), entry.name)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        meta.set_homogeneous(True)
        self._preview_meta = meta
        self._preview_chip("Type", tr(entry.kind))
        if entry.is_dir:
            try:
                count = len(list(os.scandir(entry.path)))
            except OSError:
                count = 0
            self._preview_chip("Items", str(count))
        else:
            self._preview_chip("Size", format_file_size(entry.size))
        card.append(meta)

        modified_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        modified_caption = Gtk.Label(label=tr("Modified"), xalign=0.0)
        modified_caption.get_style_context().add_class("omni-chip-label")
        modified_value = Gtk.Label(
            label=format_modified(entry.modified), xalign=1.0
        )
        modified_value.set_hexpand(True)
        modified_value.set_ellipsize(Pango.EllipsizeMode.END)
        modified_value.get_style_context().add_class("omni-row-meta")
        modified_row.append(modified_caption)
        modified_row.append(modified_value)
        card.append(modified_row)

        divider = Gtk.Box()
        divider.get_style_context().add_class("omni-divider")
        card.append(divider)
        self._preview_row("Location", os.path.dirname(entry.path))

        if entry.is_dir:
            open_hint = Gtk.Label(
                label=tr("Double-click to open this folder"), xalign=0.0
            )
            open_hint.get_style_context().add_class("omni-row-sub")
            open_hint.set_wrap(True)
            card.append(open_hint)
            return

        kind = preview_kind(entry.path)
        if kind == "image":
            try:
                picture = Gtk.Picture.new_for_filename(entry.path)
                picture.set_size_request(-1, 210)
                try:
                    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
                except Exception:
                    pass
                picture.get_style_context().add_class("files-preview-image")
                card.append(picture)
            except Exception:
                self._preview_row("Preview", tr("No preview available"))
        elif kind == "text":
            text = self._model.preview_text(entry.path, 4096)
            if text is not None:
                caption = Gtk.Label(label=tr("Preview"), xalign=0.0)
                caption.get_style_context().add_class("omni-section-title")
                card.append(caption)
                preview = Gtk.Label(label=text, xalign=0.0)
                preview.set_wrap(True)
                preview.set_selectable(True)
                preview.get_style_context().add_class("files-preview-text")
                card.append(preview)
            else:
                self._preview_row("Preview", tr("No preview available"))
        else:
            self._preview_row("Preview", tr("No preview available"))

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _icon_name(entry: FileEntry) -> str:
        kind = entry.kind
        if kind == "Folder":
            return "folder"
        if kind == "Folder link":
            return "folder-symbolic"
        if kind == "Symbolic link":
            return "emblem-symbolic-link"
        if kind == "Image":
            return "image-x-generic"
        if kind == "Audio":
            return "audio-x-generic"
        if kind == "Video":
            return "video-x-generic"
        if kind == "Archive":
            return "package-x-generic"
        if kind in ("Document", "PDF document", "Markdown document", "Text file"):
            return "x-office-document"
        if kind in ("Source code", "Script", "JSON file", "Configuration", "Stylesheet"):
            return "text-x-generic"
        if kind == "Executable":
            return "application-x-executable"
        return "text-x-generic"

    def _notify(self, message: str) -> None:
        try:
            self.ctx.notify(message)
        except Exception:
            pass

    def retranslate(self) -> None:
        super().retranslate()
        if getattr(self, "_header_buttons", None):
            self._update_sort_headers()
        if getattr(self, "_status", None):
            self._update_status()
        if getattr(self, "_preview_box", None):
            self._update_preview()
