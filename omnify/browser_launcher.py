"""Standalone ``omnify-browser`` launcher.

This module intentionally depends only on the standard library and the
headless :mod:`omnify.modules.browser_engine`.  It can be installed as a
regular application-menu entry and run without starting the full Omnify shell.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import List, Optional

from .modules.browser_engine import (
    BrowserProfile,
    default_profile_dir,
    detect_browser,
    detect_tor_browser,
    launch_browser,
    launch_tor_browser,
    screen_size_hint,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omnify-browser",
        description="Launch LibreWolf/Zen/Firefox with Omnify's privacy profile.",
    )
    parser.add_argument(
        "--profile",
        default=str(default_profile_dir()),
        help="Managed profile directory (default: ~/.local/share/omnify/browser/profile)",
    )
    parser.add_argument(
        "--browser",
        default=None,
        help="Explicit browser executable (LibreWolf is detected automatically)",
    )
    parser.add_argument(
        "--private", action="store_true", help="Open a private window"
    )
    parser.add_argument(
        "--tor",
        action="store_true",
        help="Open Tor Browser (maximum privacy) instead of the managed browser",
    )
    parser.add_argument(
        "--new-instance",
        action="store_true",
        help="Ask the browser for a separate instance",
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Do not rewrite user.js before launching",
    )
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the command instead of starting the browser",
    )
    parser.add_argument("urls", nargs="*", help="URLs or about: pages to open")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    profile = BrowserProfile(Path(args.profile).expanduser())
    if args.tor:
        tor = detect_tor_browser()
        if tor is None:
            print(
                "omnify-browser: Tor Browser is not installed. Install it from "
                "https://www.torproject.org/download/ or open Omnify Browser "
                "→ Get Tor Browser.",
                file=sys.stderr,
            )
            return 2
        try:
            command = launch_tor_browser(
                tor, urls=args.urls, dry_run=args.print_command
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"omnify-browser: {exc}", file=sys.stderr)
            return 1
        if args.print_command:
            print(shlex.join(command))
        return 0
    profile.ensure()
    if not args.no_apply:
        profile.apply(profile.load_config())
    browser = detect_browser(override=args.browser)
    if browser is None:
        print(
            "omnify-browser: no supported browser found. Install LibreWolf "
            "or Firefox, or pass --browser PATH.",
            file=sys.stderr,
        )
        return 2
    try:
        command = launch_browser(
            browser,
            profile,
            urls=args.urls,
            private=args.private,
            new_instance=args.new_instance,
            dry_run=args.print_command,
            screen_size=screen_size_hint(),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"omnify-browser: {exc}", file=sys.stderr)
        return 1
    if args.print_command:
        print(shlex.join(command))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
