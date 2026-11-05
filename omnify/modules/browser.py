"""Omnify Browser — a LibreWolf/Zen/Firefox privacy cockpit.

This module does not embed a web engine.  It manages a *standalone* browser:
a dedicated profile, Brave-style hardening prefs, an optional local proxy,
quick links and a user-local application-menu launcher.  The heavy lifting is
in the pure :mod:`omnify.modules.browser_engine` model.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.i18n import tr
from ..core.module_context import BaseModule
from .browser_migration import (
    BrowserSource,
    MigrationError,
    ProfileBusyError,
    discover_import_sources,
    import_firefox_profile,
)
from .browser_engine import (
    ACCEPT_LANGUAGE_CHOICES,
    BrowserConfig,
    BrowserInfo,
    BrowserProfile,
    Protections,
    ProxyConfig,
    SEARCH_ENGINES,
    TOR_DOWNLOAD_PAGE,
    TorBrowserInfo,
    detect_browser,
    detect_tor_browser,
    install_language_pack,
    install_standalone_launcher,
    launch_browser,
    launch_tor_browser,
    protection_summary,
    quick_link_url,
    screen_size_hint,
    url_from_input,
)


class BrowserModule(BaseModule):
    """Standalone private browser with Brave-like protections."""

    id = "browser"
    name = "Browser"
    icon = "🌐"
    description = (
        "A standalone LibreWolf-based browser with Brave-style tracker, "
        "cookie, fingerprinting and HTTPS protections."
    )
    group = "Tools"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._profile = BrowserProfile(context.storage.path / "browser" / "profile")
        self._config = self._profile.load_config()
        self._browser: Optional[BrowserInfo] = detect_browser()
        self._tor: Optional[TorBrowserInfo] = detect_tor_browser()
        self._switches: Dict[str, Any] = {}
        self._updating = False
        self._root = None
        self._migration_sources: list[BrowserSource] = []
        self._migration_combo = None
        self._migration_status = None
        self._migration_copy_button = None

    # ------------------------------------------------------------------
    # View construction
    # ------------------------------------------------------------------
    def build_view(self):
        from gi.repository import Gtk

        self._profile.ensure()
        self._profile.apply(self._config)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(24)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self._root = root

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        icon = Gtk.Label(label=self.icon)
        icon.set_markup('<span size="26000">🌐</span>')
        header.append(icon)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Browser", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        subtitle = Gtk.Label(label=self.description, xalign=0.0)
        subtitle.set_wrap(True)
        subtitle.get_style_context().add_class("omnify-dim")
        title_box.append(title)
        title_box.append(subtitle)
        header.append(title_box)
        root.append(header)

        root.append(self._build_hero(Gtk))
        root.append(self._build_privacy(Gtk))
        root.append(self._build_migration(Gtk))
        root.append(self._build_protections(Gtk))
        root.append(self._build_proxy(Gtk))
        root.append(self._build_profile(Gtk))
        self._refresh_browser_status()
        self._refresh_tor_status()
        self._refresh_shields_status()
        self._refresh_migration_sources()
        self._refresh_score()
        return root

    def _build_hero(self, Gtk) -> Any:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("omnify-card")

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._status_badge = Gtk.Label(label="Checking browser…", xalign=0.0)
        self._status_badge.get_style_context().add_class("browser-status")
        self._status_badge.set_hexpand(True)
        status_row.append(self._status_badge)
        open_button = Gtk.Button(label="Open browser")
        open_button.get_style_context().add_class("omnify-accent")
        open_button.connect("clicked", self._on_open_clicked)
        status_row.append(open_button)
        private_button = Gtk.Button(label="Private window")
        private_button.connect("clicked", self._on_private_clicked)
        status_row.append(private_button)
        tor_button = Gtk.Button(label="Maximum privacy")
        tor_button.set_tooltip_text(
            "Open Tor Browser: Tor routing plus Tor Browser's own anti-fingerprinting."
        )
        tor_button.connect("clicked", self._on_tor_clicked)
        status_row.append(tor_button)
        card.append(status_row)

        self._browser_detail = Gtk.Label(label="", xalign=0.0)
        self._browser_detail.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self._browser_detail.get_style_context().add_class("omnify-dim")
        self._browser_detail.get_style_context().add_class("omnify-mono")
        card.append(self._browser_detail)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._search_entry = Gtk.Entry()
        self._search_entry.set_placeholder_text(
            "Search the web or enter an address…"
        )
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("activate", self._on_search_activate)
        search_row.append(self._search_entry)
        search_button = Gtk.Button(label="Go")
        search_button.get_style_context().add_class("omnify-accent")
        search_button.connect("clicked", self._on_search_activate)
        search_row.append(search_button)
        card.append(search_row)

        quick_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        quick_row.set_homogeneous(False)
        for label, key in (
            ("Bookmarks", "bookmarks"),
            ("History", "history"),
            ("Downloads", "downloads"),
            ("Passwords", "passwords"),
            ("Extensions", "extensions"),
            ("Browser settings", "settings"),
        ):
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("calc-chip")
            button.connect(
                "clicked",
                lambda _button, target=key: self._launch([quick_link_url(target)]),
            )
            quick_row.append(button)
        card.append(quick_row)

        hint = Gtk.Label(
            label=(
                "The browser runs as a separate program with its own profile; "
                "Omnify only provisions and launches it."
            ),
            xalign=0.0,
        )
        hint.set_wrap(True)
        hint.get_style_context().add_class("omnify-dim")
        card.append(hint)
        return card

    def _build_privacy(self, Gtk) -> Any:
        """Private/Tor window, Shields counter and password-safety card."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")
        title = Gtk.Label(label="Private windows, Shields and passwords", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        card.append(title)

        tor_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._tor_status = Gtk.Label(label="", xalign=0.0)
        self._tor_status.set_hexpand(True)
        self._tor_status.set_wrap(True)
        self._tor_status.get_style_context().add_class("browser-status")
        tor_row.append(self._tor_status)
        self._tor_button = Gtk.Button(label="Open Tor Browser")
        self._tor_button.connect("clicked", self._on_tor_clicked)
        tor_row.append(self._tor_button)
        tor_install = Gtk.Button(label="Get Tor Browser")
        tor_install.connect("clicked", self._on_get_tor_clicked)
        tor_row.append(tor_install)
        card.append(tor_row)
        tor_note = Gtk.Label(
            label=(
                "Tor Browser routes traffic through the Tor network and uses a "
                "uniform fingerprint. It is the strongest practical privacy "
                "window; no browser can promise absolute anonymity."
            ),
            xalign=0.0,
        )
        tor_note.set_wrap(True)
        tor_note.get_style_context().add_class("omnify-dim")
        card.append(tor_note)

        shields_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._shields_status = Gtk.Label(label="", xalign=0.0)
        self._shields_status.set_hexpand(True)
        self._shields_status.set_wrap(True)
        self._shields_status.get_style_context().add_class("browser-status")
        shields_row.append(self._shields_status)
        self._shields_button = Gtk.Button(label="Reinstall Shields")
        self._shields_button.connect("clicked", self._on_reinstall_shields)
        shields_row.append(self._shields_button)
        addons = Gtk.Button(label="Add-ons")
        addons.connect(
            "clicked", lambda _button: self._launch([quick_link_url("extensions")])
        )
        shields_row.append(addons)
        card.append(shields_row)
        shields_note = Gtk.Label(
            label=(
                "Omnify Shields is a local-only companion to uBlock Origin. "
                "Its blocked-request count appears as a green badge on the "
                "browser toolbar; click the badge to pause it for one site."
            ),
            xalign=0.0,
        )
        shields_note.set_wrap(True)
        shields_note.get_style_context().add_class("omnify-dim")
        card.append(shields_note)

        password_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        password_label = Gtk.Label(
            label=(
                "Passwords are encrypted in the local Firefox store. For real "
                "protection, set a Primary Password in about:logins. Without "
                "one, anyone who can read your home folder may read the store."
            ),
            xalign=0.0,
        )
        password_label.set_hexpand(True)
        password_label.set_wrap(True)
        password_label.get_style_context().add_class("omnify-dim")
        password_row.append(password_label)
        password_button = Gtk.Button(label="Password settings")
        password_button.connect(
            "clicked", lambda _button: self._launch([quick_link_url("passwords")])
        )
        password_row.append(password_button)
        card.append(password_row)

        compatibility = Gtk.Label(
            label=(
                "Site refused to load or asked for a CAPTCHA? Disable Shields "
                "for that site from the toolbar badge, or use the Private "
                "window. Some sites also reject Tor by design."
            ),
            xalign=0.0,
        )
        compatibility.set_wrap(True)
        compatibility.get_style_context().add_class("omnify-dim")
        card.append(compatibility)
        return card

    def _build_migration(self, Gtk) -> Any:
        """One-click data migration from another installed browser."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")
        title = Gtk.Label(label="Switch from another browser", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        card.append(title)
        note = Gtk.Label(
            label=(
                "Copy bookmarks, history, saved passwords, cookies, extensions "
                "and open tabs from Firefox or LibreWolf. Chrome, Chromium, "
                "Brave, Edge and Vivaldi use Firefox's built-in importer "
                "because their encrypted vaults are not portable. The managed "
                "profile keeps Omnify's privacy protections either way."
            ),
            xalign=0.0,
        )
        note.set_wrap(True)
        note.get_style_context().add_class("omnify-dim")
        card.append(note)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._migration_combo = Gtk.ComboBoxText()
        self._migration_combo.set_hexpand(True)
        self._migration_combo.connect("changed", self._on_migration_source_changed)
        row.append(self._migration_combo)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda _button: self._refresh_migration_sources())
        row.append(refresh)
        card.append(row)

        self._migration_status = Gtk.Label(label="", xalign=0.0)
        self._migration_status.set_wrap(True)
        self._migration_status.get_style_context().add_class("omnify-dim")
        card.append(self._migration_status)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._migration_copy_button = Gtk.Button(label="Copy Firefox data")
        self._migration_copy_button.connect("clicked", self._on_copy_browser_data)
        buttons.append(self._migration_copy_button)
        open_importer = Gtk.Button(label="Open built-in importer")
        open_importer.connect(
            "clicked",
            lambda _button: self._launch(["about:preferences#general"]),
        )
        buttons.append(open_importer)
        card.append(buttons)
        importer_note = Gtk.Label(
            label=(
                "In the Firefox import wizard choose Brave (or another "
                "Chromium browser) and tick every data category. Firefox "
                "decrypts Brave's passwords and cookies through the system "
                "keyring; Omnify never sees those secrets."
            ),
            xalign=0.0,
        )
        importer_note.set_wrap(True)
        importer_note.get_style_context().add_class("omnify-dim")
        card.append(importer_note)
        safety = Gtk.Label(
            label=(
                "Close the managed browser before copying. Omnify creates a "
                "timestamped backup of overwritten files. Source security "
                "preferences are not copied on purpose."
            ),
            xalign=0.0,
        )
        safety.set_wrap(True)
        safety.get_style_context().add_class("omnify-dim")
        card.append(safety)
        return card

    def _refresh_migration_sources(self) -> None:
        """Re-scan installed browser profiles and update the combo box."""
        if self._migration_combo is None:
            return
        self._migration_sources = discover_import_sources(
            target_profile=self._profile.root
        )
        self._updating = True
        try:
            self._migration_combo.remove_all()
            if not self._migration_sources:
                self._migration_combo.append_text(tr("No other browser profiles found"))
                self._migration_combo.set_active(0)
            else:
                for source in self._migration_sources:
                    self._migration_combo.append_text(source.display_name)
                self._migration_combo.set_active(0)
        finally:
            self._updating = False
        self._on_migration_source_changed(self._migration_combo)

    def _selected_migration_source(self) -> Optional[BrowserSource]:
        combo = self._migration_combo
        if combo is None:
            return None
        index = combo.get_active()
        if index < 0 or index >= len(self._migration_sources):
            return None
        return self._migration_sources[index]

    def _on_migration_source_changed(self, _combo) -> None:
        if self._updating:
            return
        source = self._selected_migration_source()
        if self._migration_copy_button is not None:
            self._migration_copy_button.set_sensitive(
                bool(source and source.supports_direct_copy())
            )
        if self._migration_status is None:
            return
        if source is None:
            self._migration_status.set_text(tr("No other browser profiles found"))
        elif source.supports_direct_copy():
            self._migration_status.set_text(
                tr(
                    "Firefox-family profile detected at {path}. Direct copy is available.",
                    path=source.path,
                )
            )
        else:
            self._migration_status.set_text(
                tr(
                    "Chromium-family profile detected at {path}. Use the built-in "
                    "Firefox importer for passwords and site data.",
                    path=source.path,
                )
            )

    def _on_copy_browser_data(self, _button) -> None:
        source = self._selected_migration_source()
        if source is None:
            self.ctx.notify(tr("Choose a source browser first"))
            return
        if not source.supports_direct_copy():
            self.ctx.notify(
                tr("This browser must be imported with Firefox's built-in importer")
            )
            self._launch(["about:preferences#general"])
            return
        detail = tr(
            "This replaces bookmarks, history, passwords, cookies, forms, "
            "extensions and the session in the managed profile. A backup is "
            "created first. Close the managed browser now."
        )
        if not self._confirm_migration(detail):
            return
        try:
            result = import_firefox_profile(source, self._profile.root)
        except ProfileBusyError:
            self.ctx.notify(tr("Close the managed browser before importing data"))
            return
        except MigrationError as exc:
            self.ctx.notify(tr("Could not import browser data: {error}", error=exc))
            return
        except OSError as exc:
            self.ctx.notify(tr("Could not import browser data: {error}", error=exc))
            return
        if result.warnings:
            self._migration_status.set_text(" · ".join(result.warnings))
        else:
            self._migration_status.set_text(
                tr("Imported {count} items", count=result.copied_count)
                if result.copied_count
                else tr("Nothing was imported")
            )
        self.ctx.notify(
            tr(
                "Browser data imported ({count} files). Restart the browser.",
                count=result.copied_count,
            )
        )

    def _confirm_migration(self, detail: str) -> bool:
        try:
            from gi.repository import Gtk

            dialog = Gtk.AlertDialog()
            dialog.set_message(tr("Import data into the managed browser?"))
            dialog.set_detail(detail)
            dialog.set_buttons([tr("Cancel"), tr("Import")])
            dialog.set_cancel_button(0)
            dialog.set_default_button(0)
            dialog.set_modal(True)
            response = dialog.choose(self._root.get_root() if self._root else None)
            return response == 1
        except Exception:
            # Older GTK versions lack AlertDialog; the explicit button click is
            # already an intentional action, so continue with the import.
            return True

    def _protection_rows(self):
        return (
            (
                "tracking",
                "Ad & tracker blocking",
                "Strict Enhanced Tracking Protection, cryptominer, social and email tracker lists.",
            ),
            (
                "cookies",
                "Block third-party cookies",
                "Reject cross-site cookies and trackers while keeping first-party logins usable.",
            ),
            (
                "https_only",
                "HTTPS-only mode",
                "Upgrade every navigation to HTTPS and warn on sites that do not support it.",
            ),
            (
                "fingerprinting",
                "Fingerprint resistance",
                "Randomise or normalise browser fingerprints, including letterboxing.",
            ),
            (
                "webrtc",
                "WebRTC address guard",
                "Prevent WebRTC from exposing local IP addresses to websites.",
            ),
            (
                "doh",
                "Encrypted DNS (DoH)",
                "Resolve names through Quad9 over HTTPS instead of plaintext DNS.",
            ),
            (
                "global_privacy_control",
                "Global Privacy Control",
                "Send the GPC opt-out signal to every site that supports it.",
            ),
            (
                "clear_on_close",
                "Forget history and cookies on close",
                "Clear history, cookies, cache and sessions when the browser exits.",
            ),
            (
                "password_manager",
                "Built-in password manager",
                "Use the browser's encrypted local password store; no Omnify cloud.",
            ),
            (
                "telemetry_off",
                "No telemetry or sponsored tiles",
                "Disable studies, telemetry, Pocket and sponsored new-tab content.",
            ),
            (
                "memory_saver",
                "Memory saver",
                "Restore tabs lazily and unload background tabs under memory pressure.",
            ),
            (
                "shields",
                "Omnify Shields",
                "Install the local-only companion blocker and its per-tab counter.",
            ),
        )

    def _build_protections(self, Gtk) -> Any:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Brave-style protections", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        title.set_hexpand(True)
        title_row.append(title)
        self._score_label = Gtk.Label(label="", xalign=1.0)
        self._score_label.get_style_context().add_class("browser-score")
        title_row.append(self._score_label)
        card.append(title_row)

        self._updating = True
        for attr, label, detail in self._protection_rows():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text.set_hexpand(True)
            name = Gtk.Label(label=label, xalign=0.0)
            description = Gtk.Label(label=detail, xalign=0.0)
            description.set_wrap(True)
            description.get_style_context().add_class("omnify-dim")
            text.append(name)
            text.append(description)
            row.append(text)
            switch = Gtk.Switch()
            switch.set_active(bool(getattr(self._config.protections, attr)))
            switch.set_valign(Gtk.Align.CENTER)
            switch.connect("notify::active", self._on_protection_toggled, attr)
            self._switches[attr] = switch
            row.append(switch)
            card.append(row)
        self._updating = False

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        apply_button = Gtk.Button(label="Apply protections")
        apply_button.get_style_context().add_class("omnify-accent")
        apply_button.connect("clicked", self._on_apply_protections)
        action_row.append(apply_button)
        reset_button = Gtk.Button(label="Reset to recommended")
        reset_button.connect("clicked", self._on_reset_protections)
        action_row.append(reset_button)
        card.append(action_row)

        note = Gtk.Label(
            label="Changes are written to user.js and apply the next time the browser starts.",
            xalign=0.0,
        )
        note.set_wrap(True)
        note.get_style_context().add_class("omnify-dim")
        card.append(note)
        return card

    def _build_proxy(self, Gtk) -> Any:
        expander = Gtk.Expander(label="Proxy")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(8)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Mode", xalign=0.0))
        self._proxy_mode = Gtk.ComboBoxText()
        for text in ("System proxy", "Direct connection", "Manual proxy"):
            self._proxy_mode.append_text(text)
        self._proxy_mode.set_active(
            {"system": 0, "direct": 1, "manual": 2}.get(self._config.proxy.mode, 0)
        )
        self._proxy_mode.connect("changed", self._on_proxy_mode_changed)
        row.append(self._proxy_mode)
        row.append(Gtk.Label(label="Protocol", xalign=0.0))
        self._proxy_protocol = Gtk.ComboBoxText()
        for text in ("HTTP", "HTTPS", "SOCKS v5"):
            self._proxy_protocol.append_text(text)
        self._proxy_protocol.set_active(
            {"http": 0, "https": 1, "socks": 2}.get(self._config.proxy.protocol, 0)
        )
        row.append(self._proxy_protocol)
        body.append(row)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="Host", xalign=0.0))
        self._proxy_host = Gtk.Entry()
        self._proxy_host.set_placeholder_text("proxy.example.lan")
        self._proxy_host.set_text(self._config.proxy.host)
        self._proxy_host.set_hexpand(True)
        row.append(self._proxy_host)
        row.append(Gtk.Label(label="Port", xalign=0.0))
        self._proxy_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self._proxy_port.set_value(self._config.proxy.port or 8080)
        row.append(self._proxy_port)
        body.append(row)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        apply_button = Gtk.Button(label="Apply proxy")
        apply_button.connect("clicked", self._on_apply_proxy)
        action_row.append(apply_button)
        note = Gtk.Label(
            label="Manual proxies apply to HTTP, HTTPS and WebRTC traffic.",
            xalign=0.0,
        )
        note.get_style_context().add_class("omnify-dim")
        action_row.append(note)
        body.append(action_row)
        self._proxy_body = body
        expander.set_child(body)
        self._sync_proxy_sensitivity()
        return expander

    def _build_profile(self, Gtk) -> Any:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("omnify-card")
        title = Gtk.Label(label="Managed profile & launcher", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        card.append(title)

        profile_label = Gtk.Label(label=str(self._profile.root), xalign=0.0)
        profile_label.set_selectable(True)
        profile_label.set_ellipsize(3)
        profile_label.get_style_context().add_class("omnify-mono")
        profile_label.get_style_context().add_class("omnify-dim")
        card.append(profile_label)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_row.append(Gtk.Label(label="Search engine", xalign=0.0))
        self._engine_combo = Gtk.ComboBoxText()
        names = list(SEARCH_ENGINES)
        for name in names:
            self._engine_combo.append_text(name)
        current = next(
            (i for i, name in enumerate(names) if SEARCH_ENGINES[name] == self._config.search_url),
            0,
        )
        self._engine_combo.set_active(current)
        self._engine_combo.connect("changed", self._on_engine_changed)
        search_row.append(self._engine_combo)
        note = Gtk.Label(
            label="Used by Omnify's address/search bar; the browser also defaults to DuckDuckGo.",
            xalign=0.0,
        )
        note.set_wrap(True)
        note.get_style_context().add_class("omnify-dim")
        search_row.append(note)
        card.append(search_row)

        language_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        language_row.append(Gtk.Label(label="Browser language", xalign=0.0))
        self._browser_language_combo = Gtk.ComboBoxText()
        for label in (
            "LibreWolf default",
            "English",
            "Українська",
        ):
            self._browser_language_combo.append_text(label)
        self._browser_language_combo.set_active(
            {"": 0, "en": 1, "uk": 2}.get(self._config.language, 0)
        )
        self._browser_language_combo.connect(
            "changed", self._on_browser_language_changed
        )
        language_row.append(self._browser_language_combo)
        pack_button = Gtk.Button(label="Get Ukrainian language pack")
        pack_button.connect("clicked", self._on_install_language_pack)
        language_row.append(pack_button)
        card.append(language_row)

        site_language_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        site_language_row.append(
            Gtk.Label(label="Website language header", xalign=0.0)
        )
        self._accept_language_combo = Gtk.ComboBoxText()
        self._accept_language_keys = list(ACCEPT_LANGUAGE_CHOICES)
        for key in self._accept_language_keys:
            self._accept_language_combo.append_text(tr(ACCEPT_LANGUAGE_CHOICES[key]))
        try:
            active = self._accept_language_keys.index(self._config.accept_language)
        except ValueError:
            active = 0
        self._accept_language_combo.set_active(active)
        self._accept_language_combo.connect(
            "changed", self._on_accept_language_changed
        )
        site_language_row.append(self._accept_language_combo)
        card.append(site_language_row)
        site_language_note = Gtk.Label(
            label=(
                "This only sets the Accept-Language header. “Browser default” "
                "leaves Firefox's own language list; English is a common, "
                "slightly less unique choice, while Ukrainian keeps local "
                "versions of sites. The interface language above can differ. "
                "This does not add anonymity outside Tor Browser."
            ),
            xalign=0.0,
        )
        site_language_note.set_wrap(True)
        site_language_note.get_style_context().add_class("omnify-dim")
        card.append(site_language_note)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, callback in (
            ("Open profile folder", self._on_open_profile),
            ("Create app-menu launcher", self._on_create_launcher),
            ("Get LibreWolf", self._on_get_librewolf),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            action_row.append(button)
        card.append(action_row)

        hint = Gtk.Label(
            label=(
                "The standalone launcher does not require the Omnify window: "
                "run “omnify-browser” or choose Omnify Browser from the app menu."
            ),
            xalign=0.0,
        )
        hint.set_wrap(True)
        hint.get_style_context().add_class("omnify-dim")
        card.append(hint)
        return card

    # ------------------------------------------------------------------
    # State and callbacks
    # ------------------------------------------------------------------
    def _refresh_browser_status(self) -> None:
        if not hasattr(self, "_status_badge"):
            return
        self._browser = detect_browser()
        if self._browser is None:
            self._status_badge.set_text(tr("No supported browser found"))
            self._status_badge.set_tooltip_text("")
            self._browser_detail.set_text(
                tr("Install LibreWolf or Firefox, then reopen this module.")
            )
            self._set_status_class("browser-status-error")
            return
        if self._browser.is_librewolf:
            self._status_badge.set_text(tr("LibreWolf detected — hardened profile ready"))
            self._set_status_class("browser-status-ok")
        elif self._browser.key == "zen":
            self._status_badge.set_text(tr("Zen Browser detected"))
            self._set_status_class("browser-status-ok")
        else:
            self._status_badge.set_text(
                tr("LibreWolf not installed — Firefox fallback is active")
            )
            self._set_status_class("browser-status-warn")
        self._browser_detail.set_text(self._browser.path)
        self._browser_detail.set_tooltip_text(self._browser.path)

    def _set_status_class(self, css_class: str) -> None:
        context = self._status_badge.get_style_context()
        for name in ("browser-status-ok", "browser-status-warn", "browser-status-error"):
            context.remove_class(name)
        context.add_class(css_class)

    def _refresh_tor_status(self) -> None:
        if not hasattr(self, "_tor_status"):
            return
        self._tor = detect_tor_browser()
        context = self._tor_status.get_style_context()
        for name in ("browser-status-ok", "browser-status-warn"):
            context.remove_class(name)
        if self._tor is None:
            self._tor_status.set_text(
                tr("Tor Browser is not installed — use “Get Tor Browser”.")
            )
            context.add_class("browser-status-warn")
            self._tor_button.set_label(tr("Get Tor Browser"))
        else:
            self._tor_status.set_text(
                tr("Tor Browser ready — maximum-privacy window available")
            )
            context.add_class("browser-status-ok")
            self._tor_button.set_label(tr("Open Tor Browser"))

    def _refresh_shields_status(self) -> None:
        if not hasattr(self, "_shields_status"):
            return
        from .browser_shields import shield_status

        present, hosts = shield_status(self._profile.root)
        context = self._shields_status.get_style_context()
        for name in ("browser-status-ok", "browser-status-warn"):
            context.remove_class(name)
        if present:
            self._shields_status.set_text(
                tr(
                    "Omnify Shields installed — {count} tracker hosts blocked",
                    count=hosts,
                )
            )
            context.add_class("browser-status-ok")
        else:
            self._shields_status.set_text(
                tr("Omnify Shields is not installed — press “Reinstall Shields”.")
            )
            context.add_class("browser-status-warn")

    def _refresh_score(self) -> None:
        if not hasattr(self, "_score_label"):
            return
        summary = protection_summary(self._config)
        active = sum(1 for _name, enabled in summary if enabled)
        self._score_label.set_text(
            tr("{active}/{total} protections active", active=active, total=len(summary))
        )

    def _sync_switches(self) -> None:
        self._updating = True
        try:
            for attr, switch in self._switches.items():
                switch.set_active(bool(getattr(self._config.protections, attr)))
        finally:
            self._updating = False
        self._refresh_score()

    def _on_protection_toggled(self, switch, _pspec, attr: str) -> None:
        if self._updating:
            return
        setattr(self._config.protections, attr, bool(switch.get_active()))
        self._profile.apply(self._config)
        self._refresh_score()
        self.ctx.notify(tr("Protections updated — restart the browser to apply"))

    def _on_apply_protections(self, _button) -> None:
        self._profile.apply(self._config)
        self.ctx.notify(tr("Protections applied to the managed profile"))

    def _on_reset_protections(self, _button) -> None:
        self._config.protections = Protections()
        self._sync_switches()
        self._profile.apply(self._config)
        self.ctx.notify(tr("Recommended protections restored"))

    def _on_proxy_mode_changed(self, _combo) -> None:
        self._sync_proxy_sensitivity()

    def _sync_proxy_sensitivity(self) -> None:
        if not hasattr(self, "_proxy_host"):
            return
        manual = self._proxy_mode.get_active() == 2
        self._proxy_host.set_sensitive(manual)
        self._proxy_port.set_sensitive(manual)
        self._proxy_protocol.set_sensitive(manual)

    def _on_apply_proxy(self, _button) -> None:
        mode = ("system", "direct", "manual")[max(0, self._proxy_mode.get_active())]
        protocol = ("http", "https", "socks")[
            max(0, self._proxy_protocol.get_active())
        ]
        proxy = ProxyConfig(
            mode=mode,
            protocol=protocol,
            host=self._proxy_host.get_text().strip(),
            port=int(self._proxy_port.get_value()),
        )
        try:
            proxy.validate()
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        self._config.proxy = proxy
        self._profile.apply(self._config)
        self.ctx.notify(tr("Proxy settings applied"))

    def _on_engine_changed(self, combo) -> None:
        names = list(SEARCH_ENGINES)
        index = combo.get_active()
        if 0 <= index < len(names):
            self._config.search_url = SEARCH_ENGINES[names[index]]
            self._profile.save_config(self._config)

    def _on_browser_language_changed(self, combo) -> None:
        if self._updating:
            return
        language = ("", "en", "uk")[max(0, combo.get_active())]
        self._config.language = language
        self._profile.apply(self._config)
        if language == "uk":
            pack = self._profile.root / "extensions" / "langpack-uk@firefox.mozilla.org.xpi"
            if not pack.is_file():
                self.ctx.notify(
                    tr(
                        "Ukrainian language pack missing — use “Get Ukrainian language pack”"
                    )
                )
                return
        self.ctx.notify(tr("Browser language updated — restart the browser"))

    def _on_accept_language_changed(self, combo) -> None:
        if self._updating:
            return
        active = combo.get_active()
        if active < 0 or active >= len(self._accept_language_keys):
            return
        self._config.accept_language = self._accept_language_keys[active]
        self._profile.apply(self._config)
        self.ctx.notify(tr("Website language header updated — restart the browser"))

    def _on_install_language_pack(self, _button) -> None:
        if self._browser is None:
            self.ctx.notify(tr("No supported browser found"))
            return
        self.ctx.notify(tr("Downloading the Ukrainian language pack…"))
        try:
            install_language_pack(self._profile, self._browser, "uk")
        except Exception as exc:
            self.ctx.notify(tr("Could not install the language pack: {error}", error=exc))
            return
        self._config.language = "uk"
        self._profile.apply(self._config)
        if hasattr(self, "_browser_language_combo"):
            self._browser_language_combo.set_active(2)
        self.ctx.notify(tr("Ukrainian language pack installed — restart the browser"))

    def _on_search_activate(self, _widget) -> None:
        try:
            url = url_from_input(self._search_entry.get_text(), self._config.search_url)
        except ValueError as exc:
            self.ctx.notify(str(exc))
            return
        if not url:
            return
        self._launch([url])

    def _launch(self, urls, private: bool = False) -> None:
        if self._browser is None:
            self._browser = detect_browser()
        if self._browser is None:
            self.ctx.notify(tr("No supported browser found. Install LibreWolf or Firefox."))
            return
        self._profile.apply(self._config)
        try:
            launch_browser(
                self._browser,
                self._profile,
                urls=urls,
                private=private,
                screen_size=screen_size_hint(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.ctx.notify(tr("Could not start the browser: {error}", error=exc))
            return
        if urls:
            self._config.last_url = urls[0]
            self._profile.save_config(self._config)
        self.ctx.notify(
            tr("Opening {browser}", browser=self._browser.name)
            if not private
            else tr("Opening a private {browser} window", browser=self._browser.name)
        )

    def _on_open_clicked(self, _button) -> None:
        self._launch([])

    def _on_private_clicked(self, _button) -> None:
        self._launch([], private=True)

    def _on_tor_clicked(self, _button) -> None:
        self._tor = detect_tor_browser()
        if self._tor is None:
            self._on_get_tor_clicked(_button)
            return
        try:
            launch_tor_browser(self._tor)
        except (OSError, RuntimeError, ValueError) as exc:
            self.ctx.notify(tr("Could not start Tor Browser: {error}", error=exc))
            return
        self.ctx.notify(tr("Opening Tor Browser — maximum privacy"))
        self._refresh_tor_status()

    def _on_get_tor_clicked(self, _button) -> None:
        try:
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(TOR_DOWNLOAD_PAGE, None)
        except Exception as exc:
            self.ctx.notify(tr("Could not open the Tor Browser page: {error}", error=exc))

    def _on_reinstall_shields(self, _button) -> None:
        from .browser_shields import install_extension

        try:
            install_extension(self._profile.root)
        except OSError as exc:
            self.ctx.notify(tr("Could not install Shields: {error}", error=exc))
            return
        self._config.protections.shields = True
        self._profile.apply(self._config)
        self._refresh_shields_status()
        self._sync_switches()
        self.ctx.notify(tr("Omnify Shields installed — restart the browser"))

    def _on_open_profile(self, _button) -> None:
        try:
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(self._profile.root.as_uri(), None)
        except Exception as exc:
            self.ctx.notify(tr("Could not open the profile folder: {error}", error=exc))

    def _on_create_launcher(self, _button) -> None:
        try:
            paths = install_standalone_launcher(
                python_executable=sys.executable,
                refresh=True,
            )
        except OSError as exc:
            self.ctx.notify(tr("Could not create the launcher: {error}", error=exc))
            return
        self.ctx.notify(
            tr("Launcher installed: {path}", path=paths.launcher)
        )

    def _on_get_librewolf(self, _button) -> None:
        try:
            from gi.repository import Gio

            Gio.AppInfo.launch_default_for_uri(
                "https://librewolf.net/installation/", None
            )
        except Exception as exc:
            self.ctx.notify(tr("Could not open the download page: {error}", error=exc))

    # ------------------------------------------------------------------
    # BaseModule hooks
    # ------------------------------------------------------------------
    def retranslate(self) -> None:
        super().retranslate()
        self._refresh_language_combos()
        self._refresh_migration_sources()
        self._refresh_score()
        self._refresh_tor_status()
        self._refresh_shields_status()

    def _refresh_language_combos(self) -> None:
        """Re-populate language choices after a live EN↔UA switch."""
        if hasattr(self, "_browser_language_combo"):
            self._updating = True
            try:
                active = self._browser_language_combo.get_active()
                self._browser_language_combo.remove_all()
                for label in (
                    tr("LibreWolf default"),
                    tr("English"),
                    tr("Українська"),
                ):
                    self._browser_language_combo.append_text(label)
                self._browser_language_combo.set_active(active)
            finally:
                self._updating = False
        if hasattr(self, "_accept_language_combo"):
            self._updating = True
            try:
                active = self._accept_language_combo.get_active()
                self._accept_language_combo.remove_all()
                for key in self._accept_language_keys:
                    self._accept_language_combo.append_text(
                        tr(ACCEPT_LANGUAGE_CHOICES[key])
                    )
                self._accept_language_combo.set_active(active)
            finally:
                self._updating = False

    def on_show(self) -> None:
        self._refresh_browser_status()
        self._refresh_tor_status()
        self._refresh_shields_status()

    def on_hide(self) -> None:
        self._profile.save_config(self._config)

    def save_state(self) -> None:
        self._profile.save_config(self._config)
