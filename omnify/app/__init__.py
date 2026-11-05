"""The Omnify application shell (window, sidebar, navigation).

This package sits on top of the headless :mod:`omnify.core` and wires the
tools registered in :mod:`omnify.modules` into a single ``Gtk.Application``
window. It is the only place in the app that creates toplevel GTK widgets.
"""

from .shell import OmnifyApp

__all__ = ["OmnifyApp"]
