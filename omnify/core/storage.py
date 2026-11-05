"""Local persistence for Omnify.

Every module stores its data as a small JSON document under a shared data
directory. The :class:`Storage` class provides namespaced ``load`` / ``save``
helpers and writes files *atomically* (write a temp file, then rename over the
target) so an interrupted write can never corrupt existing data.

The data directory defaults to ``$XDG_DATA_HOME/omnify`` (i.e.
``~/.local/share/omnify``) and can be overridden with the ``OMNIFY_DATA_DIR``
environment variable — useful for tests and for a fully self-contained run.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional


def default_data_dir() -> Path:
    """Return the directory Omnify should keep its data in.

    Resolution order:
    1. ``OMNIFY_DATA_DIR`` environment variable (if set).
    2. ``GLib.get_user_data_dir()/omnify`` (respects ``XDG_DATA_HOME``).
    3. ``~/.local/share/omnify`` as a last resort.
    """
    env = os.environ.get("OMNIFY_DATA_DIR")
    if env:
        return Path(env).expanduser()
    try:
        from gi.repository import GLib

        return Path(GLib.get_user_data_dir()) / "omnify"
    except Exception:
        return Path.home() / ".local" / "share" / "omnify"


class Storage:
    """A thin, atomic JSON store with one file per logical key."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root) if root else default_data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- path handling -------------------------------------------------
    def _path(self, key: str) -> Path:
        safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in key)
        return self.root / (safe + ".json")

    # -- public API ----------------------------------------------------
    def load(self, key: str, default: Any = None) -> Any:
        """Return the JSON document stored under *key*, or *default*."""
        with self._lock:
            path = self._path(key)
            if not path.exists():
                return default
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return default

    def save(self, key: str, value: Any) -> None:
        """Atomically write *value* as JSON under *key*."""
        with self._lock:
            path = self._path(key)
            fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=path.name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(value, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def delete(self, key: str) -> None:
        with self._lock:
            path = self._path(key)
            if path.exists():
                path.unlink()

    def clear_all(self) -> None:
        """Remove every stored document (used by the 'reset data' action)."""
        with self._lock:
            for path in list(self.root.glob("*.json")):
                try:
                    path.unlink()
                except OSError:
                    pass

    @property
    def path(self) -> Path:
        return self.root
