"""A tiny append-only debug log for Omnify.

Omnify has no logging framework (stdlib + gi only), but several
components run unattended and fail *silently* when something is wrong:
a timer finishing while the window is hidden, sound playback through a
spawned player, a notification that never reached the desktop. When the
user reports "the sound stopped" there must be a place to look.

This module provides a single best-effort :func:`log` function that
appends timestamped lines to ``~/.local/share/omnify/debug.log`` (the
shared data directory). Every call is wrapped so logging can never
break the caller: a missing directory, a read-only home, a full disk —
all degrade to a no-op.
"""

from __future__ import annotations

import os
import time
from typing import Optional

_LOG_PATH: Optional[str] = None


def log_path() -> Optional[str]:
    """The debug log's path, or ``None`` when it cannot be determined.

    The directory is created on first use; the result is cached.
    """
    global _LOG_PATH
    if _LOG_PATH is not None:
        return _LOG_PATH or None
    try:
        path = os.path.join(
            os.path.expanduser("~"), ".local", "share", "omnify", "debug.log"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _LOG_PATH = path
    except OSError:
        _LOG_PATH = ""
    return _LOG_PATH or None


def log(message: str) -> None:
    """Append one timestamped line to the debug log (never raises)."""
    path = log_path()
    if path is None:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass
