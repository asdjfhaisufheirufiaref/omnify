"""Headless tests for the LibreWolf/Firefox profile and launcher model."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.browser_engine import (  # noqa: E402
    BrowserConfig,
    BrowserInfo,
    BrowserProfile,
    Protections,
    ProxyConfig,
    build_command,
    default_profile_dir,
    desktop_entry_text,
    detect_browser,
    install_standalone_launcher,
    launch_browser,
    protection_summary,
    quick_link_url,
    standalone_launcher_text,
    url_from_input,
    user_js_text,
    prefs_for,
)


class TestBrowserDetection(unittest.TestCase):
    def test_detects_candidate_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            librewolf = Path(tmp) / "librewolf"
            firefox = Path(tmp) / "firefox"
            librewolf.write_text("#!/bin/sh\n")
            firefox.write_text("#!/bin/sh\n")
            librewolf.chmod(0o755)
            firefox.chmod(0o755)
            info = detect_browser(
                candidates=(
                    ("librewolf", "LibreWolf", (str(librewolf),), ()),
                    ("firefox", "Firefox", (str(firefox),), ()),
                ),
                use_path=False,
            )
            self.assertIsNotNone(info)
            self.assertEqual(info.key, "librewolf")
            self.assertEqual(info.path, str(librewolf))

    def test_firefox_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            firefox = Path(tmp) / "firefox"
            firefox.write_text("#!/bin/sh\n")
            firefox.chmod(0o755)
            info = detect_browser(
                candidates=(("firefox", "Firefox", (str(firefox),), ()),),
                use_path=False,
            )
            self.assertEqual(info.name, "Firefox")
            self.assertFalse(info.is_librewolf)

    def test_override_wins_and_must_be_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "my-browser"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(0o755)
            info = detect_browser(override=str(fake), use_path=False)
            self.assertEqual(info.path, str(fake))
            self.assertEqual(info.key, "custom")
            missing = str(Path(tmp) / "missing")
            self.assertIsNone(
                detect_browser(
                    override=missing,
                    candidates=(("x", "X", (missing,), ()),),
                    use_path=False,
                )
            )

    def test_appimage_environment_and_profile_command(self):
        info = BrowserInfo("librewolf", "LibreWolf", "/tmp/LibreWolf.AppImage")
        self.assertEqual(info.environment, {"APPIMAGE_EXTRACT_AND_RUN": "1"})
        command = build_command(
            info,
            Path("/tmp/profile"),
            ["https://example.com"],
            private=True,
            new_instance=True,
        )
        self.assertEqual(command[0], "/tmp/LibreWolf.AppImage")
        self.assertIn("--profile", command)
        self.assertIn("--private-window", command)
        self.assertIn("--new-instance", command)
        self.assertEqual(command[-1], "https://example.com")

    def test_launch_dry_run_does_not_spawn(self):
        info = BrowserInfo("firefox", "Firefox", "/usr/bin/firefox")
        with tempfile.TemporaryDirectory() as tmp:
            profile = BrowserProfile(tmp)
            command = launch_browser(
                info, profile, ["about:home"], dry_run=True
            )
            self.assertEqual(command[0], "/usr/bin/firefox")
            self.assertTrue(profile.root.exists())


class TestProtections(unittest.TestCase):
    def test_defaults_are_brave_style(self):
        prefs = prefs_for(BrowserConfig())
        self.assertEqual(prefs["browser.contentblocking.category"], "strict")
        self.assertTrue(prefs["privacy.trackingprotection.enabled"])
        self.assertEqual(prefs["network.cookie.cookieBehavior"], 5)
        self.assertTrue(prefs["dom.security.https_only_mode"])
        self.assertTrue(prefs["privacy.resistFingerprinting"])
        self.assertFalse(prefs["privacy.resistFingerprinting.letterboxing"])
        self.assertTrue(prefs["media.peerconnection.ice.default_address_only"])
        self.assertEqual(prefs["network.trr.mode"], 2)
        self.assertTrue(prefs["privacy.globalprivacycontrol.enabled"])
        self.assertFalse(prefs["toolkit.telemetry.enabled"])
        self.assertFalse(prefs["browser.newtabpage.activity-stream.showSponsored"])
        self.assertFalse(prefs["browser.urlbar.suggest.quicksuggest.sponsored"])
        self.assertTrue(prefs["signon.rememberSignons"])
        self.assertTrue(prefs["devtools.enabled"])
        self.assertFalse(prefs["browser.tabs.warnOnClose"])
        self.assertFalse(prefs["browser.warnOnQuit"])

    def test_disabling_toggles_changes_prefs(self):
        protections = Protections(
            tracking=False,
            cookies=False,
            https_only=False,
            fingerprinting=False,
            webrtc=False,
            doh=False,
            telemetry_off=False,
            password_manager=False,
        )
        prefs = prefs_for(BrowserConfig(protections=protections))
        self.assertEqual(prefs["browser.contentblocking.category"], "standard")
        self.assertFalse(prefs["privacy.trackingprotection.enabled"])
        self.assertEqual(prefs["network.cookie.cookieBehavior"], 0)
        self.assertFalse(prefs["dom.security.https_only_mode"])
        self.assertFalse(prefs["privacy.resistFingerprinting"])
        self.assertFalse(prefs["media.peerconnection.ice.default_address_only"])
        self.assertEqual(prefs["network.trr.mode"], 5)
        self.assertTrue(prefs["toolkit.telemetry.enabled"])
        self.assertFalse(prefs["signon.rememberSignons"])

    def test_clear_on_close_only_sets_sanitize_prefs_when_enabled(self):
        off = prefs_for(BrowserConfig())
        self.assertFalse(off["privacy.sanitize.sanitizeOnShutdown"])
        on = prefs_for(BrowserConfig(protections=Protections(clear_on_close=True)))
        self.assertTrue(on["privacy.sanitize.sanitizeOnShutdown"])
        self.assertTrue(on["privacy.clearOnShutdown.history"])
        self.assertTrue(on["privacy.clearOnShutdown.cookies"])
        self.assertFalse(on["privacy.clearOnShutdown.siteSettings"])

    def test_user_js_is_deterministic_and_parsable(self):
        config = BrowserConfig(protections=Protections(cookies=False))
        prefs = prefs_for(config)
        text = user_js_text(prefs, generated_at=1_700_000_000)
        self.assertIn("// Generated: 2023-11-14", text)
        self.assertIn('user_pref("network.cookie.cookieBehavior", 0);', text)
        self.assertIn('user_pref("dom.security.https_only_mode", true);', text)
        self.assertTrue(text.endswith("\n"))
        pref_lines = [line for line in text.splitlines() if line.startswith("user_pref(")]
        self.assertEqual(len(pref_lines), len(set(pref_lines)))
        self.assertEqual(len(pref_lines), len(prefs))

    def test_protection_summary_reflects_config(self):
        summary = dict(protection_summary(BrowserConfig()))
        self.assertTrue(summary["Ad & tracker blocking"])
        self.assertTrue(summary["HTTPS-only mode"])
        self.assertFalse(summary["Forget data on close"])
        summary = dict(
            protection_summary(
                BrowserConfig(protections=Protections(tracking=False, clear_on_close=True))
            )
        )
        self.assertFalse(summary["Ad & tracker blocking"])
        self.assertTrue(summary["Forget data on close"])


class TestProfileAndConfig(unittest.TestCase):
    def test_profile_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = BrowserProfile(Path(tmp) / "profile")
            config = BrowserConfig(
                protections=Protections(clear_on_close=True),
                search_url="https://search.brave.com/search?q={query}",
                last_url="https://example.com",
            )
            path = profile.apply(config)
            self.assertEqual(path, profile.user_js)
            self.assertTrue(path.exists())
            loaded = profile.load_config()
            self.assertTrue(loaded.protections.clear_on_close)
            self.assertEqual(
                loaded.search_url, "https://search.brave.com/search?q={query}"
            )
            self.assertEqual(loaded.last_url, "https://example.com")

    def test_language_preferences_are_independent(self):
            config = BrowserConfig(language="uk", accept_language="browser")
            prefs = prefs_for(config)

            self.assertEqual(prefs["intl.locale.requested"], "uk")
            self.assertNotIn("intl.accept_languages", prefs)
            self.assertEqual(prefs["privacy.spoof_english"], 1)

            restored = BrowserConfig.from_dict(config.to_dict())
            self.assertEqual(restored.accept_language, "browser")

            explicit = prefs_for(
                BrowserConfig(language="uk", accept_language="en-US,en")
            )
            self.assertEqual(explicit["intl.accept_languages"], "en-US,en")

    def test_invalid_metadata_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = BrowserProfile(tmp)
            profile.ensure()
            profile.config_path.write_text("{not json")
            loaded = profile.load_config()
            self.assertEqual(loaded.search_url, "https://duckduckgo.com/?q={query}")
            self.assertTrue(loaded.protections.tracking)

    def test_search_template_must_contain_query(self):
        config = BrowserConfig.from_dict({"search_url": "https://example.com"})
        self.assertIn("{query}", config.search_url)

    def test_default_profile_dir_uses_omnify_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                default_profile_dir(tmp),
                Path(tmp) / "browser" / "profile",
            )


class TestProxy(unittest.TestCase):
    def test_system_and_direct_modes(self):
        self.assertEqual(
            BrowserConfig(proxy=ProxyConfig(mode="system")).proxy_prefs(),
            {"network.proxy.type": 5},
        )
        self.assertEqual(
            BrowserConfig(proxy=ProxyConfig(mode="direct")).proxy_prefs(),
            {"network.proxy.type": 0},
        )

    def test_manual_http_and_socks(self):
        http = BrowserConfig(
            proxy=ProxyConfig(mode="manual", protocol="http", host="127.0.0.1", port=8080)
        ).proxy_prefs()
        self.assertEqual(http["network.proxy.type"], 1)
        self.assertEqual(http["network.proxy.http"], "127.0.0.1")
        self.assertEqual(http["network.proxy.http_port"], 8080)
        socks = BrowserConfig(
            proxy=ProxyConfig(mode="manual", protocol="socks", host="proxy.lan", port=1080)
        ).proxy_prefs()
        self.assertEqual(socks["network.proxy.socks"], "proxy.lan")
        self.assertEqual(socks["network.proxy.socks_port"], 1080)
        self.assertTrue(socks["network.proxy.socks_remote_dns"])

    def test_manual_proxy_validation(self):
        with self.assertRaises(ValueError):
            ProxyConfig(mode="manual", host="", port=8080).validate()
        with self.assertRaises(ValueError):
            ProxyConfig(mode="manual", host="proxy.lan", port=0).validate()
        with self.assertRaises(ValueError):
            ProxyConfig(mode="manual", host="proxy.lan", port=70000).validate()
        with self.assertRaises(ValueError):
            ProxyConfig(mode="manual", host="bad host", port=8080).validate()

    def test_invalid_proxy_metadata_falls_back(self):
        proxy = ProxyConfig.from_dict({"mode": "weird", "protocol": "weird", "port": "x"})
        self.assertEqual(proxy.mode, "system")
        self.assertEqual(proxy.protocol, "http")
        self.assertEqual(proxy.port, 0)


class TestUrlsAndLauncher(unittest.TestCase):
    def test_url_from_input(self):
        self.assertEqual(
            url_from_input("cats and dogs"),
            "https://duckduckgo.com/?q=cats+and+dogs",
        )
        self.assertEqual(url_from_input("example.com"), "https://example.com")
        self.assertEqual(
            url_from_input("https://example.com/a?b=1"),
            "https://example.com/a?b=1",
        )
        self.assertEqual(url_from_input("about:preferences"), "about:preferences")
        self.assertEqual(url_from_input(""), "")
        with self.assertRaises(ValueError):
            url_from_input("javascript:alert(1)")

    def test_custom_search_template(self):
        self.assertEqual(
            url_from_input("firefox", "https://search.brave.com/search?q={query}"),
            "https://search.brave.com/search?q=firefox",
        )

    def test_quick_links(self):
        self.assertEqual(quick_link_url("bookmarks"), "about:bookmarks")
        self.assertEqual(quick_link_url("History"), "about:history")
        self.assertEqual(quick_link_url("unknown"), "about:home")

    def test_standalone_launcher_without_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            apps = Path(tmp) / "applications"
            paths = install_standalone_launcher(
                bin_dir=bin_dir,
                desktop_dir=apps,
                python_executable="/usr/bin/python3",
                refresh=False,
            )
            self.assertTrue(paths.launcher.exists())
            self.assertTrue(os.access(paths.launcher, os.X_OK))
            self.assertIn("omnify.browser_launcher", paths.launcher.read_text())
            desktop = paths.desktop.read_text()
            self.assertIn("[Desktop Entry]", desktop)
            self.assertIn("Name=Omnify Browser", desktop)
            self.assertIn("x-scheme-handler/https", desktop)
            self.assertIn(str(paths.launcher), desktop)

    def test_desktop_entry_quotes_spaces(self):
        text = desktop_entry_text("/tmp/My Browser/run")
        self.assertIn('Exec="/tmp/My Browser/run" %U', text)

    def test_launcher_text_quotes_python(self):
        text = standalone_launcher_text("/tmp/My Python/python")
        self.assertIn('exec "/tmp/My Python/python"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
