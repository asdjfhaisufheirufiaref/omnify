"""A small always-available capture window.

It is deliberately separate from the main window so it can be opened while
Omnify is hidden in the tray.  The text can come from the clipboard, which is
the bridge used by the browser context-menu helper and by global shortcuts.
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.i18n import tr


class QuickCaptureWindow:
    """Keyboard-first "capture text" dialog."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._window: Optional[Any] = None
        self._text: Optional[Any] = None
        self._targets: Optional[Any] = None
        self._prefill_clipboard = True

    def open(self, prefill: bool = True) -> None:
        from gi.repository import Gtk

        self._prefill_clipboard = prefill
        if self._window is None:
            self._build(Gtk)
        if self._window is None:
            return
        self._window.present()
        if self._text is not None:
            self._text.grab_focus()
            if not self._text.get_buffer().get_char_count() and prefill:
                self._read_clipboard(prefill=True)

    def _build(self, Gtk) -> None:
        window = Gtk.Window(title=tr("Quick capture"))
        window.set_default_size(620, 420)
        window.set_modal(False)
        window.set_destroy_with_parent(True)
        parent = getattr(self._app, "_window", None)
        if parent is not None:
            window.set_transient_for(parent)
        self._window = window

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        outer.set_margin_start(14)
        outer.set_margin_end(14)

        heading = Gtk.Label(label=tr("Quick capture"), xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        outer.append(heading)
        hint = Gtk.Label(
            label=tr(
                "Capture a thought, a selection or research notes. "
                "The clipboard is pre-filled when available."
            ),
            xalign=0.0,
        )
        hint.set_wrap(True)
        hint.get_style_context().add_class("omnify-dim")
        outer.append(hint)

        target_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        target_row.append(Gtk.Label(label=tr("Save to:")))
        self._targets = Gtk.ComboBoxText()
        self._targets.append("new", tr("New note"))
        self._targets.append("journal", tr("Today's journal"))
        saved_target = ""
        try:
            saved_target = str(
                self._app.ctx.settings.get("quick_capture_target", "new")
            )
        except Exception:
            saved_target = "new"
        self._targets.set_active_id(saved_target if saved_target in ("new", "journal") else "new")
        target_row.append(self._targets)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        target_row.append(spacer)
        clipboard_button = Gtk.Button(label=tr("Paste clipboard"))
        clipboard_button.connect("clicked", lambda _b: self._read_clipboard(True))
        target_row.append(clipboard_button)
        outer.append(target_row)

        self._text = Gtk.TextView()
        self._text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text.set_monospace(False)
        self._text.set_left_margin(10)
        self._text.set_right_margin(10)
        self._text.set_top_margin(10)
        self._text.set_bottom_margin(10)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._text)
        outer.append(scroller)

        status = Gtk.Label(
            label=tr("Ctrl+Enter saves · Esc closes"),
            xalign=0.0,
        )
        status.get_style_context().add_class("omnify-dim")
        outer.append(status)
        self._status = status

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        clear = Gtk.Button(label=tr("Clear"))
        clear.connect("clicked", lambda _b: self._text.get_buffer().set_text(""))
        buttons.append(clear)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        cancel = Gtk.Button(label=tr("Cancel"))
        cancel.connect("clicked", lambda _b: self._close())
        buttons.append(cancel)
        save = Gtk.Button(label=tr("Capture"))
        save.get_style_context().add_class("omnify-accent")
        save.connect("clicked", lambda _b: self._capture())
        buttons.append(save)
        outer.append(buttons)

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key)
        window.add_controller(controller)
        window.connect("destroy", self._on_destroy)
        window.set_child(outer)

    def _on_destroy(self, _window) -> None:
        self._window = None
        self._text = None

    def _close(self) -> None:
        if self._window is not None:
            self._window.close()

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        from gi.repository import Gdk

        if keyval == Gdk.KEY_Escape:
            self._close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (
            state & Gdk.ModifierType.CONTROL_MASK
        ):
            self._capture()
            return True
        return False

    def _read_clipboard(self, replace: bool = False) -> None:
        try:
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if display is None:
                return
            clipboard = display.get_clipboard()
        except Exception:
            return

        def finished(source, result) -> None:
            try:
                text = source.read_text_finish(result)
            except Exception:
                return
            if not text or self._text is None:
                return
            buffer = self._text.get_buffer()
            if replace or buffer.get_char_count() == 0:
                buffer.set_text(text)
                self._text.grab_focus()

        try:
            clipboard.read_text_async(None, finished)
        except Exception:
            pass

    def _capture(self) -> None:
        if self._text is None:
            return
        buffer = self._text.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, True).strip()
        if not text:
            if hasattr(self, "_status"):
                self._status.set_text(tr("Nothing to capture yet"))
            return
        target = "new"
        if self._targets is not None:
            target = self._targets.get_active_id() or "new"
        try:
            self._app.ctx.settings.set("quick_capture_target", target)
        except Exception:
            pass
        notes = getattr(self._app, "_modules", {}).get("notes")
        capture = getattr(notes, "capture_text", None)
        if not callable(capture):
            if hasattr(self, "_status"):
                self._status.set_text(tr("Notes module is not available"))
            return
        note_id = capture(text, target=target)
        if note_id is None:
            if hasattr(self, "_status"):
                self._status.set_text(tr("Could not create a note"))
            return
        self._close()
        try:
            self._app._open_note(note_id)
        except Exception:
            pass
        try:
            self._app.ctx.notify(tr("Captured to Notes"))
        except Exception:
            pass
