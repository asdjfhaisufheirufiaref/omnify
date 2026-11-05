"""Omnify — a modular everyday-tools application.

This package is split into three layers:

* :mod:`omnify.core`    — the headless core (theming, persistence, the
  :class:`~omnify.core.BaseModule` interface shared by every tool).
* :mod:`omnify.app`     — the application shell (window, sidebar, navigation).
* :mod:`omnify.modules` — the individual tools. Each one is a small,
  self-contained module that plugs into the core through the
  :class:`~omnify.core.BaseModule` interface.

Adding a new tool means adding one file to ``omnify/modules`` and registering
it in the module registry — no changes to the core are required.
"""

__version__ = "0.1.0"
