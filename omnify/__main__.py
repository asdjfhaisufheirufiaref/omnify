"""Allow running Omnify as ``python3 -m omnify``."""

from __future__ import annotations

from omnify.main import main

raise SystemExit(main())
