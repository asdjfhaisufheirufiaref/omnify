"""Entry point for Omnify (``python3 -m omnify``)."""

from __future__ import annotations

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    """Launch the application and return its exit code.

    ``--module <id>`` opens Omnify on a specific tool.  GTK's command-line
    handling forwards the option to an already-running instance, so every
    per-module application entry reuses the one alarm/timer owner instead of
    starting duplicate background clocks.
    """
    # Imported lazily so that ``import omnify.main`` does not pull in GTK.
    from omnify.app import OmnifyApp

    arguments = list(sys.argv if argv is None else argv)
    if not arguments:
        arguments = ["omnify"]
    return OmnifyApp().run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
