"""Backup policy, discovery and safety rules for Omnify notes.

The archive writer lives in :mod:`omnify.modules.notes_engine`; this module
owns everything that decides what a backup is and which files may be removed.
Keeping these rules headless makes the destructive parts testable without GTK.

Safety contract:

* Omnify only removes files whose names match its strict backup pattern and
  whose ZIP contents contain an Omnify manifest (new backups) or, for legacy
  archives, look like a notes archive.
* Starred backups are never removed automatically.
* Legacy archives are never removed automatically.
* The destination directory may contain other applications' files; they are
  ignored.
"""

from __future__ import annotations

import calendar
import os
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

#: Storage key used by :class:`omnify.core.storage.Storage`.
BACKUP_KEY = "backup"

#: Marker written into every new ZIP by ``NoteStore.create_backup``.
MANIFEST_NAME = "omnify-backup.json"

#: Strict filename pattern. ``notes-20261112-174500.zip`` is a regular
#: archive; ``notes-20261112-174500_STAR.backup`` is a starred archive.
BACKUP_PATTERN = re.compile(
    r"^notes-(?P<date>\d{8})-(?P<time>\d{6})(?:-\d+)?"
    r"(?P<star>_STAR)?\.(?P<kind>zip|backup)$"
)

_INTERVAL_FACTORS = {
    "minutes": 1,
    "hours": 60,
    "days": 24 * 60,
    "weeks": 7 * 24 * 60,
}
_PRUNE_UNITS = {"months", "years"}


def default_backup_dir() -> Path:
    """Return the default backup directory.

    ``OMNIFY_BACKUP_DIR`` is honoured for tests and portable installations.
    """
    env = os.environ.get("OMNIFY_BACKUP_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "omnify" / "backups"


def _as_int(value, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


@dataclass
class BackupConfig:
    """User-configurable backup policy, persisted as ``backup.json``."""

    enabled: bool = True
    interval_value: int = 1
    interval_unit: str = "days"
    destination: str = ""
    limit_count: int = 10
    auto_prune: bool = False
    prune_value: int = 1
    prune_unit: str = "years"
    # Internal: prevents the same limit warning from appearing on every tick.
    last_limit_notice_count: int = 0

    @classmethod
    def from_dict(cls, raw: dict | None) -> "BackupConfig":
        raw = raw if isinstance(raw, dict) else {}
        interval_unit = str(raw.get("interval_unit", "days"))
        if interval_unit not in _INTERVAL_FACTORS:
            interval_unit = "days"
        prune_unit = str(raw.get("prune_unit", "years"))
        if prune_unit not in _PRUNE_UNITS:
            prune_unit = "years"
        return cls(
            enabled=bool(raw.get("enabled", True)),
            interval_value=_as_int(raw.get("interval_value", 1), 1),
            interval_unit=interval_unit,
            destination=str(raw.get("destination", "") or ""),
            limit_count=_as_int(raw.get("limit_count", 10), 10),
            auto_prune=bool(raw.get("auto_prune", False)),
            prune_value=_as_int(raw.get("prune_value", 1), 1),
            prune_unit=prune_unit,
            last_limit_notice_count=max(
                0, _as_int(raw.get("last_limit_notice_count", 0), 0, 0)
            ),
        )

    @classmethod
    def load(cls, storage) -> "BackupConfig":
        return cls.from_dict(storage.load(BACKUP_KEY, {}))

    def save(self, storage) -> None:
        storage.save(BACKUP_KEY, asdict(self))

    def destination_path(self) -> Path:
        """Resolved destination directory (custom path or the default)."""
        text = self.destination.strip()
        return Path(text).expanduser() if text else default_backup_dir()

    @property
    def interval_minutes(self) -> int:
        """Interval in minutes; never less than five minutes."""
        factor = _INTERVAL_FACTORS.get(self.interval_unit, 24 * 60)
        return max(5, self.interval_value * factor)

    @property
    def prune_months(self) -> int:
        """Age threshold expressed in calendar months."""
        value = max(1, int(self.prune_value))
        return value * 12 if self.prune_unit == "years" else value


@dataclass(frozen=True)
class BackupRecord:
    """One archive discovered in the backup directory."""

    path: Path
    created: datetime
    size: int
    starred: bool
    managed: bool
    legacy: bool

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stamp(self) -> str:
        return self.created.strftime("%Y-%m-%d %H:%M")


def _zip_has_manifest(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            return MANIFEST_NAME in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _zip_looks_like_notes(path: Path) -> bool:
    """Conservative legacy check: a valid ZIP with Markdown at its root."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if archive.testzip() is not None:
                return False
    except (OSError, zipfile.BadZipFile):
        return False
    for name in names:
        member = Path(name)
        if member.is_absolute() or ".." in member.parts:
            continue
        if member.suffix.lower() == ".md" and len(member.parts) >= 1:
            return True
    return False


def _parse_stamp(path: Path) -> datetime | None:
    match = BACKUP_PATTERN.match(path.name)
    if match is None:
        return None
    try:
        return datetime.strptime(
            f"{match.group('date')}{match.group('time')}", "%Y%m%d%H%M%S"
        )
    except ValueError:
        return None


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _subtract_months(moment: datetime, months: int) -> datetime:
    """Subtract calendar months, clamping the day where needed."""
    months = max(0, int(months))
    year = moment.year
    month = moment.month - months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


class BackupManager:
    """List, star, manually delete and safely prune Omnify backups."""

    def __init__(self, config: BackupConfig | None = None) -> None:
        self.config = config or BackupConfig()

    @property
    def destination(self) -> Path:
        return self.config.destination_path()

    # -- discovery -----------------------------------------------------
    def is_omnify_backup(self, path: Path | str) -> bool:
        """Return True only for an Omnify-created (or legacy notes) archive."""
        candidate = Path(path)
        if not candidate.is_file() or BACKUP_PATTERN.match(candidate.name) is None:
            return False
        if _zip_has_manifest(candidate):
            return True
        # Legacy backups predate the manifest. They are visible for manual
        # actions, but ``prune_old`` never removes them automatically.
        return _zip_looks_like_notes(candidate)

    def describe(self, path: Path | str) -> BackupRecord | None:
        candidate = Path(path)
        if not self.is_omnify_backup(candidate):
            return None
        match = BACKUP_PATTERN.match(candidate.name)
        assert match is not None
        created = _parse_stamp(candidate)
        if created is None:
            created = datetime.fromtimestamp(candidate.stat().st_mtime)
        managed = _zip_has_manifest(candidate)
        return BackupRecord(
            path=candidate,
            created=created,
            size=candidate.stat().st_size,
            starred=bool(match.group("star")),
            managed=managed,
            legacy=not managed,
        )

    def list_backups(self) -> list[BackupRecord]:
        directory = self.destination
        if not directory.is_dir():
            return []
        records: list[BackupRecord] = []
        try:
            candidates = list(directory.glob("notes-*"))
        except OSError:
            return []
        for candidate in candidates:
            record = self.describe(candidate)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: (item.created, item.path.name), reverse=True)
        return records

    def latest(self) -> BackupRecord | None:
        records = self.list_backups()
        return records[0] if records else None

    def is_due(self, now: datetime | None = None) -> bool:
        """Whether a new automatic backup should be created now."""
        latest = self.latest()
        if latest is None:
            return True
        moment = now or datetime.now()
        return (moment - latest.created).total_seconds() >= (
            self.config.interval_minutes * 60
        )

    # -- limit notifications ------------------------------------------
    def take_limit_notice(self) -> tuple[int, int] | None:
        """Return ``(count, limit)`` once when the configured limit is crossed."""
        count = len(self.list_backups())
        limit = max(1, self.config.limit_count)
        if count > limit:
            if self.config.last_limit_notice_count != count:
                self.config.last_limit_notice_count = count
                return count, limit
        elif self.config.last_limit_notice_count:
            self.config.last_limit_notice_count = 0
        return None

    # -- manual operations --------------------------------------------
    def star(self, path: Path | str) -> Path:
        """Rename an archive to ``..._STAR.backup``; return the new path."""
        candidate = Path(path)
        record = self.describe(candidate)
        if record is None:
            raise ValueError("Not an Omnify backup")
        if record.starred:
            return candidate
        base = candidate.stem
        if base.endswith("_STAR"):
            base = base[:-5]
        target = _unique_path(candidate.with_name(f"{base}_STAR.backup"))
        candidate.rename(target)
        return target

    def unstar(self, path: Path | str) -> Path:
        """Remove the star marker and restore the ``.zip`` name."""
        candidate = Path(path)
        record = self.describe(candidate)
        if record is None:
            raise ValueError("Not an Omnify backup")
        if not record.starred:
            return candidate
        base = candidate.stem
        if base.endswith("_STAR"):
            base = base[:-5]
        target = _unique_path(candidate.with_name(f"{base}.zip"))
        candidate.rename(target)
        return target

    def delete(self, path: Path | str) -> None:
        """Delete one archive after validating that Omnify owns it."""
        candidate = Path(path)
        if self.describe(candidate) is None:
            raise ValueError("Refusing to delete a non-Omnify file")
        candidate.unlink()

    # -- automatic pruning --------------------------------------------
    def prune_old(self, now: datetime | None = None) -> int:
        """Remove old, unstarred, manifest-backed archives.

        Starred archives, legacy archives and unrelated files are never
        touched. Returns the number of removed files.
        """
        if not self.config.auto_prune:
            return 0
        moment = now or datetime.now()
        cutoff = _subtract_months(moment, self.config.prune_months)
        removed = 0
        for record in self.list_backups():
            if record.starred or not record.managed:
                continue
            if record.created >= cutoff:
                continue
            try:
                record.path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def summary(self) -> dict[str, int]:
        records = self.list_backups()
        return {
            "count": len(records),
            "starred": sum(1 for record in records if record.starred),
            "bytes": sum(record.size for record in records),
            "legacy": sum(1 for record in records if record.legacy),
            "limit": max(1, self.config.limit_count),
            "exceeded": int(len(records) > max(1, self.config.limit_count)),
        }

    @staticmethod
    def size_text(value: int) -> str:
        return _format_bytes(value)

    def maintain(self) -> tuple[int, tuple[int, int] | None]:
        """Run pruning and produce a one-shot limit warning if needed."""
        removed = self.prune_old()
        notice = self.take_limit_notice()
        return removed, notice
