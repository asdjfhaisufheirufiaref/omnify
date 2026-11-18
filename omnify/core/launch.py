"""Pure parsing of Omnify's launch options.

GTK's command-line vfunc lives in :mod:`omnify.app.shell`, but the actual
argument scan is deliberately independent of GTK so it can be tested
headlessly.  Unknown options are ignored and left to the application /
desktop environment, matching the previous inline implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class LaunchRequest:
    """The launch options Omnify understands."""

    module: Optional[str] = None
    command: Optional[str] = None
    standalone: bool = False


def parse_launch_args(args: Iterable[str]) -> LaunchRequest:
    """Read ``--module``, ``--command`` and ``--window`` from *args*.

    Both ``--option value`` and ``--option=value`` forms are accepted.
    When an option is repeated the last value wins.  A trailing option
    without a value is ignored instead of raising, which keeps a malformed
    desktop entry from stopping an already-running instance.
    """
    items = [str(item) for item in args]
    module: Optional[str] = None
    command: Optional[str] = None
    standalone = False
    index = 0
    while index < len(items):
        arg = items[index]
        if arg == "--module" and index + 1 < len(items):
            module = items[index + 1]
            index += 2
            continue
        if arg.startswith("--module="):
            module = arg.split("=", 1)[1]
        elif arg in {"--window", "--standalone"}:
            standalone = True
        elif arg == "--command" and index + 1 < len(items):
            command = items[index + 1]
            index += 2
            continue
        elif arg.startswith("--command="):
            command = arg.split("=", 1)[1]
        index += 1
    return LaunchRequest(
        module=module or None,
        command=command or None,
        standalone=standalone,
    )
