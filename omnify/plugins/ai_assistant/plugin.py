"""AI Assistant: the first built-in Omnify plugin.

The plugin is intentionally thin: :mod:`omnify.plugins.ai_assistant.client`
does the HTTP work and this module only wires prompts, commands and settings
into the application.  It stays disabled by default; enabling it is an
explicit choice in Settings → Plugins.
"""

from __future__ import annotations

import datetime as _dt
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from omnify.core.i18n import tr
from omnify.plugins.ai_assistant.client import (
    AIError,
    AIConfig,
    AIClient,
    PROVIDER_ORDER,
    PROVIDER_PRESETS,
    parse_json_object,
    provider_defaults,
    sanitize_base_url,
)


class AIAssistantPlugin:
    """Commands and settings for the built-in AI assistant."""

    def __init__(self, api) -> None:
        self._api = api
        self._client = AIClient()
        self._busy = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def setup(self) -> None:
        api = self._api
        api.register_command(
            "ask",
            "Ask AI…",
            self.ask,
            category="AI",
            description="Ask any question using the configured model.",
        )
        api.register_command(
            "ask_note",
            "Ask AI about the current note…",
            self.ask_about_note,
            category="AI",
            description="Send the open note as context and ask a question.",
        )
        api.register_command(
            "summarize_note",
            "Summarize the current note with AI",
            self.summarize_note,
            category="AI",
            description="Create a concise summary and optionally save it as a note.",
        )
        api.register_command(
            "calendar_event",
            "Create a calendar event with AI…",
            self.calendar_event,
            category="AI",
            description="Describe an event in plain language; AI fills the fields.",
        )
        api.register_command(
            "research",
            "Research with AI and the browser…",
            self.research,
            category="AI",
            description="Open a web search and draft a structured research note.",
        )
        api.register_command(
            "settings",
            "AI Assistant settings…",
            self.open_settings,
            category="AI",
        )
        api.register_settings_panel("AI Assistant", self.build_settings_panel)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def _config(self) -> AIConfig:
        return AIConfig.from_settings(self._api.settings_dict())

    def _parent(self):
        try:
            from gi.repository import Gtk

            app = Gtk.Application.get_default()
            if app is not None:
                return app.get_active_window()
        except Exception:
            pass
        return None

    def _set_busy(self, message: str) -> None:
        self._api.notify(message)

    def _ensure_ready(self, parent) -> Optional[AIConfig]:
        try:
            config = self._config()
            config.validate()
        except AIError as exc:
            self._api.notify(str(exc))
            return None
        if not config.local and not self._privacy_acknowledged(config, parent):
            return None
        return config

    def _privacy_acknowledged(self, config: AIConfig, parent) -> bool:
        """Cloud providers need one explicit acknowledgement per provider."""
        key = f"privacy_ack_{config.provider}"
        if bool(self._api.get_setting(key, False)):
            return True
        try:
            from gi.repository import Gtk

            dialog = Gtk.AlertDialog()
            dialog.set_message(
                tr("Send this prompt to {provider}?", provider=config.provider.title())
            )
            dialog.set_detail(
                tr(
                    "This provider is not local. The text you send, including "
                    "any note content you choose to include, leaves this "
                    "computer and is handled under that provider's privacy "
                    "policy. Prefer a local model for private notes."
                )
            )
            dialog.set_buttons([tr("Cancel"), tr("Send to cloud provider")])
            dialog.set_cancel_button(0)
            dialog.set_default_button(0)
            dialog.set_modal(True)
            if dialog.choose(parent) != 1:
                return False
        except Exception:
            self._api.notify(
                tr("Cloud AI requires an explicit privacy acknowledgement in AI settings.")
            )
            return False
        self._api.set_setting(key, True)
        return True

    # ------------------------------------------------------------------
    # Command: ask
    # ------------------------------------------------------------------
    def ask(self) -> bool:
        self._prompt(
            tr("Ask AI"),
            tr("Question"),
            "",
            self._on_ask_question,
            multiline=True,
        )
        return True

    def _on_ask_question(self, question: str) -> None:
        question = question.strip()
        if not question:
            return
        self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a concise, helpful assistant inside a desktop "
                        "productivity app. Answer in the language of the user's "
                        "question. Markdown is allowed."
                    ),
                },
                {"role": "user", "content": question},
            ],
            on_success=lambda answer: self._show_answer(tr("AI answer"), answer),
        )

    def ask_about_note(self) -> bool:
        notes = self._api.get_module("notes")
        if notes is None or not callable(getattr(notes, "current_note_text", None)):
            self._api.notify(tr("Open a note first."))
            return False
        text = notes.current_note_text().strip()
        if not text:
            self._api.notify(tr("The current note is empty."))
            return False
        title = notes.current_note_title() or tr("Untitled")
        self._prompt(
            tr("Ask AI about this note"),
            tr("Question about “{title}”", title=title),
            "",
            lambda question: self._on_ask_note(title, text, question),
            multiline=True,
        )
        return True

    def _on_ask_note(self, title: str, note_text: str, question: str) -> None:
        question = question.strip()
        if not question:
            return
        self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You answer questions about the note supplied by the "
                        "user. Base your answer on the note and say clearly when "
                        "the note does not contain enough information. Answer in "
                        "the user's language."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Note: {title}\n\n{note_text}\n\nQuestion: {question}",
                },
            ],
            on_success=lambda answer: self._show_answer(
                tr("AI answer"), answer, save_title=f"AI · {title}"
            ),
        )

    # ------------------------------------------------------------------
    # Command: summarize
    # ------------------------------------------------------------------
    def summarize_note(self) -> bool:
        notes = self._api.get_module("notes")
        if notes is None or not callable(getattr(notes, "current_note_text", None)):
            self._api.notify(tr("Open a note first."))
            return False
        text = notes.current_note_text().strip()
        if not text:
            self._api.notify(tr("The current note is empty."))
            return False
        title = notes.current_note_title() or tr("Untitled")
        self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize the note for the user in the note's language. "
                        "Start with a one-paragraph summary, then list the key "
                        "points as bullets, then list any action items. Keep "
                        "facts exactly as written; do not invent details."
                    ),
                },
                {"role": "user", "content": f"Note: {title}\n\n{text}"},
            ],
            on_success=lambda answer: self._show_answer(
                tr("Summary · {title}", title=title),
                answer,
                save_title=tr("Summary · {title}", title=title),
            ),
        )
        return True

    # ------------------------------------------------------------------
    # Command: calendar event
    # ------------------------------------------------------------------
    def calendar_event(self) -> bool:
        today = _dt.date.today().isoformat()
        self._prompt(
            tr("Create a calendar event"),
            tr(
                "Describe the event, for example: “Dentist tomorrow at 15:00 "
                "for 45 minutes”. Today is {date}.",
                date=today,
            ),
            "",
            lambda text: self._on_calendar_text(text, today),
            multiline=True,
        )
        return True

    def _on_calendar_text(self, text: str, today: str) -> None:
        text = text.strip()
        if not text:
            return
        self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Convert the user's event description into one JSON "
                        "object and nothing else. Use this schema: "
                        '{"date":"YYYY-MM-DD","time":"HH:MM" or null,'
                        '"duration_minutes":45 or null,"title":"short title",'
                        '"notes":"optional details"}. '
                        f"Today is {today}. Resolve relative dates such as "
                        "tomorrow, next Monday and in two days. Use 24-hour "
                        "time. Never invent a date when the user gave none; "
                        "use today instead."
                    ),
                },
                {"role": "user", "content": text},
            ],
            on_success=self._on_calendar_json,
        )

    def _on_calendar_json(self, answer: str) -> None:
        try:
            payload = parse_json_object(answer)
            date_iso = str(payload.get("date") or "").strip()
            _dt.date.fromisoformat(date_iso)
            title = str(payload.get("title") or "").strip()
            if not title:
                raise AIError("The AI did not provide an event title")
            raw_time = payload.get("time")
            time_text: Optional[str] = None
            if raw_time not in (None, "", "null"):
                time_text = str(raw_time).strip()
                _dt.datetime.strptime(time_text, "%H:%M")
            try:
                duration = int(payload.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                duration = 0
            notes = str(payload.get("notes") or "").strip()
        except (AIError, ValueError) as exc:
            self._api.notify(tr("Could not understand the event: {error}", error=exc))
            return
        end_time: Optional[str] = None
        if time_text and duration > 0:
            start = _dt.datetime.strptime(f"{date_iso} {time_text}", "%Y-%m-%d %H:%M")
            end_time = (start + _dt.timedelta(minutes=duration)).strftime("%H:%M")
        self._confirm_event(date_iso, time_text, end_time, title, notes)

    def _confirm_event(
        self,
        date_iso: str,
        time_text: Optional[str],
        end_time: Optional[str],
        title: str,
        notes: str,
    ) -> None:
        detail = f"{date_iso} · {time_text or tr('all day')}"
        if end_time:
            detail += f"–{end_time}"
        if notes:
            detail += f"\n\n{notes}"
        try:
            from gi.repository import Gtk

            dialog = Gtk.AlertDialog()
            dialog.set_message(tr("Create “{title}”?", title=title))
            dialog.set_detail(detail)
            dialog.set_buttons([tr("Cancel"), tr("Create event")])
            dialog.set_cancel_button(0)
            dialog.set_default_button(1)
            dialog.set_modal(True)
            if dialog.choose(self._parent()) != 1:
                return
        except Exception:
            return
        calendar = self._api.get_module("calendar")
        if calendar is None or not callable(getattr(calendar, "create_event", None)):
            self._api.notify(tr("The Calendar module is not available."))
            return
        try:
            calendar.create_event(
                date_iso,
                time_text,
                title,
                notes,
                end_time=end_time,
            )
        except Exception as exc:
            self._api.notify(tr("Could not create the event: {error}", error=exc))
            return
        self._api.open_module("calendar")
        self._api.notify(tr("Event created: {title}", title=title))

    # ------------------------------------------------------------------
    # Command: research
    # ------------------------------------------------------------------
    def research(self) -> bool:
        self._prompt(
            tr("Research with AI"),
            tr("What should the browser search for?"),
            "",
            self._on_research_topic,
            multiline=False,
        )
        return True

    def _on_research_topic(self, topic: str) -> None:
        topic = topic.strip()
        if not topic:
            return
        browser = self._api.get_module("browser")
        if browser is not None and callable(getattr(browser, "search", None)):
            try:
                browser.search(topic)
            except Exception:
                pass
        self._complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a research assistant. You cannot see web pages "
                        "and must not claim you read them. Produce a useful "
                        "research note in Markdown: a short framing paragraph, "
                        "key questions to answer, suggested credible source "
                        "types, a checklist for evaluating sources, and a "
                        "section for findings the user will fill from the "
                        "opened browser. Answer in the user's language."
                    ),
                },
                {"role": "user", "content": f"Research topic: {topic}"},
            ],
            on_success=lambda answer: self._show_answer(
                tr("Research notes"),
                answer,
                save_title=tr("Research · {topic}", topic=topic),
            ),
        )
        return True

    def open_settings(self) -> bool:
        self._api.open_module("settings")
        self._api.notify(tr("Open Settings → AI Assistant to configure the model."))
        return True

    # ------------------------------------------------------------------
    # Async request plumbing
    # ------------------------------------------------------------------
    def _complete(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        on_success: Callable[[str], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        parent = self._parent()
        config = self._ensure_ready(parent)
        if config is None:
            return
        if self._busy:
            self._api.notify(tr("AI is already working on a request."))
            return
        self._busy = True
        self._set_busy(tr("AI is thinking…"))

        def worker() -> None:
            try:
                answer = self._client.complete(config, messages)
            except Exception as exc:
                message = str(exc) if isinstance(exc, AIError) else f"AI error: {exc}"
                self._finish(lambda: self._notify_error(message, on_error))
            else:
                self._finish(lambda: on_success(answer))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, callback: Callable[[], None]) -> None:
        try:
            from gi.repository import GLib

            def run() -> bool:
                self._busy = False
                try:
                    callback()
                except Exception as exc:
                    self._api.notify(f"AI: {exc}")
                return False

            GLib.idle_add(run)
        except Exception:
            self._busy = False
            callback()

    def _notify_error(self, message: str, on_error: Optional[Callable[[str], None]]) -> None:
        self._api.notify(message)
        if on_error is not None:
            try:
                on_error(message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Small GTK dialogs
    # ------------------------------------------------------------------
    def _prompt(
        self,
        title: str,
        label: str,
        default: str,
        on_submit: Callable[[str], None],
        *,
        multiline: bool = False,
    ) -> None:
        from gi.repository import Gtk

        parent = self._parent()
        window = Gtk.Window(title=title)
        if parent is not None:
            window.set_transient_for(parent)
        window.set_modal(True)
        window.set_destroy_with_parent(True)
        window.set_default_size(560 if multiline else 460, 360 if multiline else 190)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        heading = Gtk.Label(label=title, xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        outer.append(heading)
        prompt = Gtk.Label(label=label, xalign=0.0)
        prompt.set_wrap(True)
        prompt.get_style_context().add_class("omnify-dim")
        outer.append(prompt)

        entry = Gtk.Entry()
        text_view = None
        if multiline:
            text_view = Gtk.TextView()
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.set_left_margin(8)
            text_view.set_right_margin(8)
            text_view.set_top_margin(8)
            text_view.set_bottom_margin(8)
            if default:
                text_view.get_buffer().set_text(default)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            scroller.set_child(text_view)
            outer.append(scroller)
        else:
            entry.set_text(default)
            entry.connect("activate", lambda _e: submit())
            outer.append(entry)

        def value() -> str:
            if text_view is not None:
                buffer = text_view.get_buffer()
                start, end = buffer.get_bounds()
                return buffer.get_text(start, end, True)
            return entry.get_text()

        def submit() -> None:
            text = value().strip()
            if not text:
                return
            window.close()
            on_submit(text)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        cancel = Gtk.Button(label=tr("Cancel"))
        cancel.connect("clicked", lambda _b: window.close())
        buttons.append(cancel)
        ok = Gtk.Button(label=tr("Continue"))
        ok.get_style_context().add_class("omnify-accent")
        ok.connect("clicked", lambda _b: submit())
        buttons.append(ok)
        outer.append(buttons)

        window.set_child(outer)
        window.present()
        if text_view is not None:
            text_view.grab_focus()
        else:
            entry.grab_focus()

    def _show_answer(
        self,
        title: str,
        answer: str,
        *,
        save_title: Optional[str] = None,
    ) -> None:
        from gi.repository import Gtk

        window = Gtk.Window(title=title)
        parent = self._parent()
        if parent is not None:
            window.set_transient_for(parent)
        window.set_modal(False)
        window.set_destroy_with_parent(True)
        window.set_default_size(760, 560)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        heading = Gtk.Label(label=title, xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        outer.append(heading)

        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(10)
        view.set_right_margin(10)
        view.set_top_margin(10)
        view.set_bottom_margin(10)
        view.get_buffer().set_text(answer)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(view)
        outer.append(scroller)

        status = Gtk.Label(label="", xalign=0.0)
        status.get_style_context().add_class("omnify-dim")
        outer.append(status)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        def copy() -> None:
            try:
                from gi.repository import Gdk, GObject

                clipboard = Gdk.Display.get_default().get_clipboard()
                value = GObject.Value()
                value.init(GObject.TYPE_STRING)
                value.set_string(answer)
                clipboard.set(value)
                status.set_text(tr("Copied to the clipboard"))
            except Exception:
                pass

        copy_button = Gtk.Button(label=tr("Copy"))
        copy_button.connect("clicked", lambda _b: copy())
        buttons.append(copy_button)

        if save_title:
            save_button = Gtk.Button(label=tr("Save as note"))
            save_button.get_style_context().add_class("omnify-accent")

            def save() -> None:
                notes = self._api.get_module("notes")
                capture = getattr(notes, "capture_text", None)
                if notes is None or not callable(capture):
                    status.set_text(tr("The Notes module is not available"))
                    return
                note_id = capture(answer, title=save_title)
                if not note_id:
                    status.set_text(tr("Could not create a note"))
                    return
                try:
                    notes.open_note(note_id)
                    self._api.open_module("notes")
                except Exception:
                    pass
                status.set_text(tr("Saved to Notes"))
                window.close()

            save_button.connect("clicked", lambda _b: save())
            buttons.append(save_button)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        close_button = Gtk.Button(label=tr("Close"))
        close_button.connect("clicked", lambda _b: window.close())
        buttons.append(close_button)
        outer.append(buttons)

        window.set_child(outer)
        window.present()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def build_settings_panel(self):
        from gi.repository import Gtk

        values = self._api.settings_dict()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")

        heading = Gtk.Label(label=tr("AI Assistant"), xalign=0.0)
        heading.get_style_context().add_class("omnify-card-title")
        card.append(heading)
        intro = Gtk.Label(
            label=tr(
                "Local providers keep prompts on this computer. Cloud "
                "providers receive the prompt and any note text you include; "
                "they ask for confirmation once before the first request."
            ),
            xalign=0.0,
        )
        intro.set_wrap(True)
        intro.get_style_context().add_class("omnify-dim")
        card.append(intro)

        provider_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        provider_row.append(Gtk.Label(label=tr("Provider"), xalign=0.0))
        provider_combo = Gtk.ComboBoxText()
        for provider in PROVIDER_ORDER:
            provider_combo.append(provider, PROVIDER_PRESETS[provider]["label"])
        current = str(values.get("provider") or "local")
        if current not in PROVIDER_PRESETS:
            current = "custom"
        provider_combo.set_active_id(current)
        provider_combo.set_hexpand(True)
        provider_row.append(provider_combo)
        card.append(provider_row)

        warning = Gtk.Label(label="", xalign=0.0)
        warning.set_wrap(True)
        card.append(warning)

        base_entry = Gtk.Entry()
        model_entry = Gtk.Entry()
        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        key_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        for label_text, entry, placeholder in (
            (
                tr("Base URL"),
                base_entry,
                tr("http://localhost:11434/v1"),
            ),
            (tr("Model"), model_entry, tr("llama3.1")),
            (tr("API key"), key_entry, tr("optional for local models")),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label=label_text, xalign=0.0))
            entry.set_hexpand(True)
            entry.set_placeholder_text(placeholder)
            row.append(entry)
            card.append(row)

        base_entry.set_text(str(values.get("base_url") or ""))
        model_entry.set_text(str(values.get("model") or ""))
        key_entry.set_text(str(values.get("api_key") or ""))
        key_note = Gtk.Label(
            label=tr(
                "The key is stored in Omnify's local settings. For stronger "
                "separation, leave it empty and set OMNIFY_AI_API_KEY."
            ),
            xalign=0.0,
        )
        key_note.set_wrap(True)
        key_note.get_style_context().add_class("omnify-dim")
        card.append(key_note)

        advanced = Gtk.Expander(label=tr("Advanced"))
        advanced_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        advanced_body.set_margin_top(6)

        temperature_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        temperature_row.append(Gtk.Label(label=tr("Temperature"), xalign=0.0))
        temperature = Gtk.SpinButton.new_with_range(0.0, 2.0, 0.1)
        try:
            temperature.set_value(float(values.get("temperature", 0.3)))
        except (TypeError, ValueError):
            temperature.set_value(0.3)
        temperature_row.append(temperature)
        temperature_row.append(Gtk.Label(label=tr("Timeout (seconds)"), xalign=0.0))
        timeout = Gtk.SpinButton.new_with_range(10, 600, 10)
        try:
            timeout.set_value(float(values.get("timeout", 90)))
        except (TypeError, ValueError):
            timeout.set_value(90)
        temperature_row.append(timeout)
        advanced_body.append(temperature_row)
        advanced.set_child(advanced_body)
        card.append(advanced)

        status = Gtk.Label(label="", xalign=0.0)
        status.set_wrap(True)
        status.get_style_context().add_class("omnify-dim")
        card.append(status)

        def current_values() -> Dict[str, Any]:
            provider = provider_combo.get_active_id() or "custom"
            return {
                "provider": provider,
                "base_url": base_entry.get_text().strip(),
                "model": model_entry.get_text().strip(),
                "api_key": key_entry.get_text().strip(),
                "temperature": temperature.get_value(),
                "timeout": timeout.get_value(),
            }

        def update_warning(*_args) -> None:
            provider = provider_combo.get_active_id() or "custom"
            try:
                config = AIConfig.from_settings(current_values())
                local = config.local
            except Exception:
                local = provider == "local"
            if local:
                warning.set_text(
                    tr("Local endpoint: prompts stay on this computer.")
                )
                warning.get_style_context().remove_class("browser-status-error")
                warning.get_style_context().add_class("browser-status-ok")
            else:
                warning.set_text(
                    tr(
                        "⚠ Cloud provider: prompts and any included note text "
                        "leave this computer. Never send secrets you are not "
                        "allowed to share."
                    )
                )
                warning.get_style_context().remove_class("browser-status-ok")
                warning.get_style_context().add_class("browser-status-error")

        def load_defaults(*_args) -> None:
            provider = provider_combo.get_active_id() or "custom"
            defaults = provider_defaults(provider)
            base_entry.set_text(defaults.get("base_url", ""))
            model_entry.set_text(defaults.get("model", ""))
            update_warning()

        provider_combo.connect("changed", load_defaults)
        base_entry.connect("changed", update_warning)
        update_warning()

        def save(_button=None) -> None:
            raw = current_values()
            provider = raw["provider"]
            try:
                config = AIConfig.from_settings(raw)
                config.validate()
            except AIError as exc:
                status.set_text(str(exc))
                return
            previous_provider = str(self._api.get_setting("provider", "local"))
            previous_base = str(self._api.get_setting("base_url", ""))
            for key, value in raw.items():
                self._api.set_setting(key, value)
            if provider != previous_provider or config.base_url != previous_base:
                self._api.set_setting(f"privacy_ack_{provider}", False)
            self._api.notify(tr("AI settings saved"))
            status.set_text(tr("Saved. The model is ready to use."))

        def test(_button=None) -> None:
            try:
                config = AIConfig.from_settings(current_values())
                config.validate()
            except AIError as exc:
                status.set_text(str(exc))
                return
            status.set_text(tr("Testing the connection…"))

            def worker() -> None:
                try:
                    answer = self._client.test(config)
                except Exception as exc:
                    message = str(exc)
                else:
                    message = tr("Connection OK: {answer}", answer=answer)

                def done() -> bool:
                    status.set_text(message)
                    return False

                try:
                    from gi.repository import GLib

                    GLib.idle_add(done)
                except Exception:
                    status.set_text(message)

            threading.Thread(target=worker, daemon=True).start()

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        defaults_button = Gtk.Button(label=tr("Load defaults"))
        defaults_button.connect("clicked", load_defaults)
        buttons.append(defaults_button)
        test_button = Gtk.Button(label=tr("Test connection"))
        test_button.connect("clicked", test)
        buttons.append(test_button)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        buttons.append(spacer)
        save_button = Gtk.Button(label=tr("Save AI settings"))
        save_button.get_style_context().add_class("omnify-accent")
        save_button.connect("clicked", save)
        buttons.append(save_button)
        card.append(buttons)
        return card


def setup(api) -> None:
    AIAssistantPlugin(api).setup()
