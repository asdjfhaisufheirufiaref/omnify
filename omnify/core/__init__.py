"""Core of the Omnify application.

Everything in this package is generic and shared by all tool modules:

* :mod:`~omnify.core.storage`       — local persistence (JSON, atomic writes).
* :mod:`~omnify.core.theme`         — light/dark theming via GTK CSS.
* :mod:`~omnify.core.module_context`— the :class:`ModuleContext` handle and
  :class:`BaseModule` interface shared by every tool.

The core deliberately stays headless: the main window, sidebar and navigation
live in :mod:`omnify.app`, and the individual tools live in
:mod:`omnify.modules`.
"""

from .storage import Storage, default_data_dir
from .theme import Theme
from .module_context import ModuleContext, BaseModule, Settings

__all__ = [
    "Storage",
    "default_data_dir",
    "Theme",
    "ModuleContext",
    "BaseModule",
    "Settings",
]
