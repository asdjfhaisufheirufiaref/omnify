"""Browser data migration for Omnify's managed LibreWolf/Firefox profile.

The module is deliberately independent of GTK and of the browser process.  It
discovers Firefox-family profiles and Chromium-family profile directories, then
copies the portable user data that Firefox can read natively.

What is copied
--------------
* places.sqlite, favicons.sqlite (bookmarks, history, downloads, tags)
* logins.json, key4.db, pkcs11.txt (saved passwords, encrypted as before)
* cookies.sqlite, permissions.sqlite, content-prefs.sqlite (site data)
* formhistory.sqlite
* extensions/*.xpi and extension-preferences.json
* sessionstore.jsonlz4 and sessionstore-backups/*

What is deliberately not copied
-------------------------------
The source profile's ``prefs.js`` is not copied wholesale.  It commonly turns
privacy protections off, points at the source profile's private extension IDs,
or contains stale paths.  Omnify writes its hardening prefs after migration, so
the imported data is usable without weakening the managed profile.

Chromium-family browsers (Chrome, Chromium, Brave, Edge, Vivaldi) keep their
passwords and cookies in formats Firefox does not understand.  For those
sources the browser's built-in Firefox migration wizard is the safe path; the
UI opens it rather than pretending that a raw file copy is a complete import.
"""

from __future__ import annotations

import configparser
import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


#: Categories shown by the UI.  Keys are stable; values are English labels.
CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("bookmarks", "Bookmarks and history"),
    ("passwords", "Saved passwords"),
    ("cookies", "Cookies and site permissions"),
    ("forms", "Form and search history"),
    ("extensions", "Extensions"),
    ("session", "Open tabs and session"),
)

#: Files copied for each category.  Directories are expanded one level.
_CATEGORY_FILES: Dict[str, Tuple[str, ...]] = {
    "bookmarks": ("places.sqlite", "favicons.sqlite"),
    "passwords": ("logins.json", "key4.db", "pkcs11.txt"),
    "cookies": ("cookies.sqlite", "permissions.sqlite", "content-prefs.sqlite"),
    "forms": ("formhistory.sqlite",),
    "extensions": ("extension-preferences.json",),
    "session": ("sessionstore.jsonlz4",),
}

#: Firefox-family directories that contain a profiles.ini.
_FIREFOX_ROOTS: Tuple[Tuple[str, str, str], ...] = (
    # key, display name, profiles.ini path below $HOME
    ("firefox", "Firefox", ".mozilla/firefox/profiles.ini"),
    ("librewolf", "LibreWolf", ".librewolf/profiles.ini"),
    ("zen", "Zen Browser", ".zen/profiles.ini"),
    # Snap and Flatpak homes are common on Ubuntu and Silverblue.
    ("firefox", "Firefox (Snap)", "snap/firefox/common/.mozilla/firefox/profiles.ini"),
    (
        "firefox",
        "Firefox (Flatpak)",
        ".var/app/org.mozilla.firefox/.mozilla/firefox/profiles.ini",
    ),
    (
        "librewolf",
        "LibreWolf (Flatpak)",
        ".var/app/io.gitlab.librewolf-community/.librewolf/profiles.ini",
    ),
)

#: Chromium-family profile roots below $HOME.
_CHROMIUM_ROOTS: Tuple[Tuple[str, str, str], ...] = (
    ("chrome", "Google Chrome", ".config/google-chrome"),
    ("chromium", "Chromium", ".config/chromium"),
    ("brave", "Brave", ".config/BraveSoftware/Brave-Browser"),
    ("edge", "Microsoft Edge", ".config/microsoft-edge"),
    ("vivaldi", "Vivaldi", ".config/vivaldi"),
    ("chrome", "Chrome (Snap)", "snap/chromium/common/chromium"),
    ("brave", "Brave (Snap)", "snap/brave/common/profile"),
    (
        "chrome",
        "Chrome (Flatpak)",
        ".var/app/com.google.Chrome/config/google-chrome",
    ),
    (
        "brave",
        "Brave (Flatpak)",
        ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    ),
)


class MigrationError(RuntimeError):
    """A migration cannot be performed safely."""


class ProfileBusyError(MigrationError):
    """The target browser is running and holds the profile lock."""


@dataclass(frozen=True)
class BrowserSource:
    """One discoverable source profile."""

    key: str
    name: str
    family: str  # "firefox" or "chromium"
    path: Path
    profile_name: str = ""
    is_default: bool = False

    @property
    def display_name(self) -> str:
        suffix = f" — {self.profile_name}" if self.profile_name else ""
        return f"{self.name}{suffix}"

    def supports_direct_copy(self) -> bool:
        return self.family == "firefox"


@dataclass(frozen=True)
class ImportPlanEntry:
    """One source/destination pair in an import plan."""

    category: str
    source: Path
    destination: Path
    is_directory: bool = False


@dataclass
class ImportResult:
    """Outcome of a Firefox-profile import."""

    copied: List[Path] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    backup_dir: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def copied_count(self) -> int:
        return len(self.copied)


def _profiles_from_ini(path: Path) -> List[Tuple[str, Path, bool]]:
    """Return ``(name, absolute_profile_path, is_default)`` from profiles.ini."""
    if not path.is_file():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        return []
    result: List[Tuple[str, Path, bool]] = []
    default_names = set()
    for section in parser.sections():
        if section.startswith("Install") and parser.has_option(section, "Default"):
            default_names.add(parser.get(section, "Default", fallback=""))
    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        raw_path = parser.get(section, "Path", fallback="").strip()
        if not raw_path:
            continue
        profile_path = Path(raw_path).expanduser()
        if not profile_path.is_absolute():
            profile_path = path.parent / profile_path
        name = parser.get(section, "Name", fallback=profile_path.name).strip()
        is_default = bool(
            parser.get(section, "Default", fallback="").strip() in {"1", "yes", "true"}
            or name in default_names
        )
        if profile_path.is_dir():
            result.append((name or profile_path.name, profile_path, is_default))
    # A default flag is useful in the combo box; keep one deterministic choice.
    if result and not any(default for _, _, default in result):
        result[0] = (result[0][0], result[0][1], True)
    return result


def _chromium_profiles(root: Path, key: str, name: str) -> List[BrowserSource]:
    """Find Chromium profile subdirectories under *root*."""
    if not root.is_dir():
        return []
    sources: List[BrowserSource] = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        if candidate.name == "Guest Profile" or candidate.name.startswith("System "):
            continue
        preferences = candidate / "Preferences"
        if not preferences.is_file() and candidate.name != "Default":
            continue
        profile_name = candidate.name
        try:
            data = json.loads(preferences.read_text(encoding="utf-8", errors="replace"))
            profile_name = str(
                data.get("profile", {}).get("name", profile_name) or profile_name
            )
        except (OSError, ValueError, AttributeError):
            pass
        sources.append(
            BrowserSource(
                key=key,
                name=name,
                family="chromium",
                path=candidate,
                profile_name=profile_name,
                is_default=candidate.name == "Default",
            )
        )
    return sources


def discover_import_sources(
    home: Optional[Path | str] = None,
    target_profile: Optional[Path | str] = None,
) -> List[BrowserSource]:
    """Discover candidate browser profiles without modifying anything.

    The managed Omnify profile is excluded so the UI never offers to import a
    profile into itself.  Results are ordered with likely defaults first.
    """
    base = Path(home).expanduser() if home is not None else Path.home()
    target = Path(target_profile).expanduser().resolve() if target_profile else None
    sources: List[BrowserSource] = []

    for key, name, relative in _FIREFOX_ROOTS:
        ini = base / relative
        for profile_name, profile_path, is_default in _profiles_from_ini(ini):
            try:
                resolved = profile_path.resolve()
            except OSError:
                continue
            if target is not None and resolved == target:
                continue
            sources.append(
                BrowserSource(
                    key=key,
                    name=name,
                    family="firefox",
                    path=resolved,
                    profile_name=profile_name,
                    is_default=is_default,
                )
            )

    for key, name, relative in _CHROMIUM_ROOTS:
        sources.extend(_chromium_profiles(base / relative, key, name))

    # Remove duplicate paths (for example Snap and Flatpak entries that point
    # at the same real directory after symlinks) while preserving order.
    seen = set()
    unique: List[BrowserSource] = []
    for source in sources:
        marker = (source.family, str(source.path))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(source)

    unique.sort(
        key=lambda source: (
            0 if source.family == "firefox" else 1,
            0 if source.is_default else 1,
            source.name.lower(),
            source.profile_name.lower(),
        )
    )
    return unique


def _copy_sqlite(source: Path, destination: Path) -> None:
    """Take a consistent SQLite snapshot, even if the source is running."""
    temporary = destination.with_suffix(destination.suffix + ".omnify-tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(str(temporary))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(temporary, destination)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".omnify-tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def build_import_plan(
    source: BrowserSource,
    target_profile: Path | str,
    categories: Optional[Iterable[str]] = None,
) -> List[ImportPlanEntry]:
    """Return the file operations for a Firefox-family source.

    Missing source files are simply absent from the plan.  Chromium sources
    raise :class:`MigrationError`: Firefox's encrypted Chromium vaults cannot
    be safely copied into a Gecko profile.
    """
    if not source.supports_direct_copy():
        raise MigrationError(
            "Chromium-family profiles must use Firefox's built-in importer"
        )
    target = Path(target_profile).expanduser()
    chosen = set(categories or (key for key, _label in CATEGORIES))
    entries: List[ImportPlanEntry] = []
    for category, filenames in _CATEGORY_FILES.items():
        if category not in chosen:
            continue
        for filename in filenames:
            source_file = source.path / filename
            if source_file.is_file():
                entries.append(
                    ImportPlanEntry(category, source_file, target / filename)
                )
    if "extensions" in chosen:
        extension_dir = source.path / "extensions"
        if extension_dir.is_dir():
            for xpi in sorted(extension_dir.glob("*.xpi")):
                if xpi.name.startswith("omnify-shields@"):
                    continue
                entries.append(
                    ImportPlanEntry(
                        "extensions", xpi, target / "extensions" / xpi.name
                    )
                )
    if "session" in chosen:
        backup_dir = source.path / "sessionstore-backups"
        if backup_dir.is_dir():
            for item in sorted(backup_dir.glob("*.jsonlz4")):
                entries.append(
                    ImportPlanEntry(
                        "session",
                        item,
                        target / "sessionstore-backups" / item.name,
                    )
                )
    return entries


def target_profile_is_busy(target_profile: Path | str) -> bool:
    """Best-effort detection of a running Firefox process using the profile."""
    target = Path(target_profile).expanduser()
    for name in ("parent.lock", "lock"):
        lock = target / name
        try:
            if lock.exists() or lock.is_symlink():
                return True
        except OSError:
            continue
    return False


def _backup_destination(entry: ImportPlanEntry, backup_root: Path) -> None:
    if not entry.destination.exists():
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    relative = entry.destination.name
    destination = backup_root / relative
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        counter = 1
        while destination.exists():
            destination = backup_root / f"{stem}-{counter}{suffix}"
            counter += 1
    if entry.destination.is_dir():
        shutil.copytree(entry.destination, destination)
    else:
        shutil.copy2(entry.destination, destination)


def import_firefox_profile(
    source: BrowserSource,
    target_profile: Path | str,
    categories: Optional[Iterable[str]] = None,
    backup: bool = True,
) -> ImportResult:
    """Copy a Firefox-family profile's portable data into *target_profile*.

    The target browser must be closed.  A timestamped backup of every
    overwritten target file is created by default.  The source may be running:
    SQLite files are snapshotted through SQLite's backup API.
    """
    if not source.supports_direct_copy():
        raise MigrationError(
            "Chromium-family profiles must use Firefox's built-in importer"
        )
    target = Path(target_profile).expanduser()
    if source.path.resolve() == target.resolve():
        raise MigrationError("Source and destination profiles are the same")
    if target_profile_is_busy(target):
        raise ProfileBusyError(
            "Close the managed Omnify browser before importing data"
        )
    target.mkdir(parents=True, exist_ok=True)
    plan = build_import_plan(source, target, categories)
    result = ImportResult()
    if not plan:
        result.warnings.append("No importable files were found in the source")
        return result

    backup_root: Optional[Path] = None
    if backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_root = target.parent / f"import-backup-{stamp}"
        result.backup_dir = backup_root
        for entry in plan:
            try:
                _backup_destination(entry, backup_root)
            except OSError as exc:
                result.warnings.append(
                    f"Could not back up {entry.destination.name}: {exc}"
                )

    for entry in plan:
        try:
            if entry.source.suffix == ".sqlite":
                _copy_sqlite(entry.source, entry.destination)
            else:
                _copy_file(entry.source, entry.destination)
        except (OSError, sqlite3.Error) as exc:
            result.warnings.append(f"Could not import {entry.source.name}: {exc}")
            continue
        result.copied.append(entry.destination)
    return result


def import_summary(result: ImportResult) -> str:
    """Human-readable one-line summary used by notifications."""
    if not result.copied:
        return "Nothing was imported"
    if len(result.copied) == 1:
        return "Imported 1 item"
    return f"Imported {len(result.copied)} items"
