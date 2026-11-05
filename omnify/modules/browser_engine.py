"""Headless browser provisioning, hardening and launch model.

Omnify does not reimplement a browser engine.  It provisions a *standalone*
LibreWolf/Zen/Firefox profile with Brave-style default protections, a private
search entry point and an optional system launcher.  LibreWolf is preferred;
Firefox is used as a graceful fallback on machines where LibreWolf has not
been installed yet.

The module is intentionally GTK-free and side-effect free until one of the
explicit action helpers is called.  That makes the pref generation, profile
format and command construction easy to test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus, urlparse

#: Default search endpoint.  A template must contain ``{query}``.
DEFAULT_SEARCH_URL = "https://duckduckgo.com/?q={query}"
SEARCH_ENGINES: Dict[str, str] = {
    "DuckDuckGo": DEFAULT_SEARCH_URL,
    "Brave Search": "https://search.brave.com/search?q={query}",
    "Startpage": "https://www.startpage.com/sp/search?query={query}",
    "Wikipedia": "https://en.wikipedia.org/w/index.php?search={query}",
}

#: Browser discovery order.  Relative paths are expanded from ``$HOME``.
BROWSER_CANDIDATES: Tuple[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "librewolf",
        "LibreWolf",
        (
            "~/.local/share/omnify/browser/LibreWolf.AppImage",
            "~/.local/bin/librewolf",
            "/usr/bin/librewolf",
            "/usr/local/bin/librewolf",
            "/opt/librewolf/librewolf",
            "/usr/lib/librewolf/librewolf",
        ),
        ("librewolf",),
    ),
    (
        "zen",
        "Zen Browser",
        (
            "~/.local/bin/zen-browser",
            "/usr/bin/zen-browser",
            "/usr/bin/zen",
            "/opt/zen-browser/zen",
        ),
        ("zen-browser", "zen"),
    ),
    (
        "firefox",
        "Firefox",
        (
            "/usr/bin/firefox",
            "/usr/local/bin/firefox",
            "/opt/firefox/firefox",
        ),
        ("firefox",),
    ),
)

#: Name of the marker file written inside the managed profile.
CONFIG_FILENAME = "omnify-browser.json"
LAUNCHER_NAME = "omnify-browser"
DESKTOP_NAME = "omnify-browser.desktop"

#: A portable Tor Browser can be installed by Omnify next to LibreWolf.
#: Tor Browser is deliberately kept as a separate, unmodified application:
#: its own launcher starts the Tor daemon and uses Tor Browser's security
#: level, which is stronger than proxying a normal Firefox profile.
TOR_BROWSER_CANDIDATES: Tuple[str, ...] = (
    "~/.local/share/omnify/browser/tor-browser/Browser/start-tor-browser",
    "~/.local/share/torbrowser/tbb/x86_64/tor-browser/Browser/start-tor-browser",
    "~/.local/share/torbrowser/tbb/aarch64/tor-browser/Browser/start-tor-browser",
    "/opt/tor-browser/Browser/start-tor-browser",
    "~/.local/bin/torbrowser-launcher",
    "/usr/bin/torbrowser-launcher",
)
TOR_DOWNLOAD_PAGE = "https://www.torproject.org/download/"
FIREFOX_LANGPACK_URL = (
    "https://archive.mozilla.org/pub/firefox/releases/"
    "{version}/linux-x86_64/xpi/{code}.xpi"
)
#: Safe, explicit HTTP language choices.  Arbitrary user input is never written
#: into a request header.
ACCEPT_LANGUAGE_CHOICES: Dict[str, str] = {
    "browser": "Browser default",
    "en-US,en": "English (privacy default)",
    "uk-UA,uk,en": "Ukrainian",
}


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


@dataclass(frozen=True)
class BrowserInfo:
    """A detected browser executable."""

    key: str
    name: str
    path: str

    @property
    def is_librewolf(self) -> bool:
        return self.key == "librewolf"

    @property
    def is_appimage(self) -> bool:
        return self.path.lower().endswith(".appimage")

    @property
    def environment(self) -> Dict[str, str]:
        """Extra environment needed to run portable AppImages without FUSE."""
        if self.is_appimage:
            return {"APPIMAGE_EXTRACT_AND_RUN": "1"}
        return {}


@dataclass(frozen=True)
class TorBrowserInfo:
    """A detected Tor Browser launcher."""

    path: str
    name: str = "Tor Browser"

    @property
    def is_tor_browser(self) -> bool:
        return "tor-browser" in self.path.lower() or Path(self.path).name in {
            "tor-browser",
            "torbrowser-launcher",
        }


@dataclass
class Protections:
    """The toggles exposed by the Browser module.

    Defaults mirror the Brave starting point: strict tracker/ad blocking,
    third-party cookie rejection, HTTPS upgrades, fingerprinting resistance,
    a WebRTC address-leak guard, encrypted DNS, Global Privacy Control and
    telemetry/sponsored-content opt-outs.
    """

    tracking: bool = True
    cookies: bool = True
    https_only: bool = True
    fingerprinting: bool = True
    webrtc: bool = True
    doh: bool = True
    global_privacy_control: bool = True
    clear_on_close: bool = False
    password_manager: bool = True
    telemetry_off: bool = True
    sponsored_off: bool = True
    session_restore: bool = True
    #: Keep a small footprint by unloading background tabs when memory is low.
    memory_saver: bool = True
    #: Install the local Omnify Shields companion extension.
    shields: bool = True

    def to_dict(self) -> Dict[str, bool]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "Protections":
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        values = {}
        for key in defaults.to_dict():
            value = data.get(key, getattr(defaults, key))
            values[key] = bool(value)
        return cls(**values)


@dataclass
class ProxyConfig:
    """Firefox/LibreWolf proxy preferences in a small validated form."""

    mode: str = "system"  # system | direct | manual
    protocol: str = "http"  # http | https | socks
    host: str = ""
    port: int = 0

    def validate(self) -> None:
        if self.mode not in {"system", "direct", "manual"}:
            raise ValueError("Proxy mode must be system, direct or manual")
        if self.protocol not in {"http", "https", "socks"}:
            raise ValueError("Proxy protocol must be http, https or socks")
        if self.mode == "manual":
            host = self.host.strip()
            if not host or any(ch.isspace() for ch in host):
                raise ValueError("Proxy host is required and cannot contain spaces")
            if not (1 <= int(self.port) <= 65535):
                raise ValueError("Proxy port must be between 1 and 65535")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "protocol": self.protocol,
            "host": self.host,
            "port": int(self.port),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ProxyConfig":
        if not isinstance(data, dict):
            return cls()
        mode = str(data.get("mode", "system"))
        protocol = str(data.get("protocol", "http"))
        try:
            port = int(data.get("port", 0))
        except (TypeError, ValueError):
            port = 0
        config = cls(
            mode=mode if mode in {"system", "direct", "manual"} else "system",
            protocol=protocol if protocol in {"http", "https", "socks"} else "http",
            host=str(data.get("host", "") or ""),
            port=port,
        )
        return config


@dataclass
class BrowserConfig:
    """Persisted configuration for the managed browser profile."""

    protections: Protections = field(default_factory=Protections)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    search_url: str = DEFAULT_SEARCH_URL
    last_url: str = ""
    #: Optional Firefox UI locale (``"en"``/``"uk"``); empty keeps the
    #: browser's own default.  A matching signed language pack must be present
    #: in the profile for non-English locales.
    language: str = ""
    #: HTTP ``Accept-Language`` sent to sites.  Kept separate from the UI
    #: language: a Ukrainian interface should not force every site to see a
    #: rare locale fingerprint.  ``"browser"`` leaves the browser's own
    #: language list untouched; the other values are explicit, safe choices.
    accept_language: str = "browser"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "protections": self.protections.to_dict(),
            "proxy": self.proxy.to_dict(),
            "search_url": self.search_url,
            "last_url": self.last_url,
            "language": self.language,
            "accept_language": self.accept_language,
        }

    def proxy_prefs(self) -> Dict[str, Any]:
        """Return Firefox prefs for the current proxy mode."""
        return _proxy_prefs(self)

    @classmethod
    def from_dict(cls, data: Any) -> "BrowserConfig":
        if not isinstance(data, dict):
            return cls()
        search = str(data.get("search_url", DEFAULT_SEARCH_URL) or DEFAULT_SEARCH_URL)
        if "{query}" not in search:
            search = DEFAULT_SEARCH_URL
        return cls(
            protections=Protections.from_dict(data.get("protections")),
            proxy=ProxyConfig.from_dict(data.get("proxy")),
            search_url=search,
            last_url=str(data.get("last_url", "") or ""),
            language=(
                str(data.get("language", "") or "")
                if str(data.get("language", "") or "") in {"", "en", "uk"}
                else ""
            ),
            accept_language=(
                str(data.get("accept_language", "browser") or "browser")
                if str(data.get("accept_language", "browser") or "browser")
                in ACCEPT_LANGUAGE_CHOICES
                else "browser"
            ),
        )


def default_profile_dir(data_dir: Optional[Path | str] = None) -> Path:
    """Return the profile directory used by the Omnify browser."""
    if data_dir is None:
        env = os.environ.get("OMNIFY_DATA_DIR")
        base = Path(env).expanduser() if env else Path.home() / ".local" / "share" / "omnify"
    else:
        base = Path(data_dir)
    return base / "browser" / "profile"


class BrowserProfile:
    """Create and update the dedicated browser profile."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root) if root is not None else default_profile_dir()

    @property
    def user_js(self) -> Path:
        return self.root / "user.js"

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def window_state_path(self) -> Path:
        return self.root / "xulstore.json"

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def ensure_window_state(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Path:
        """Write a maximized main-window geometry into the profile.

        Firefox stores the last window geometry in ``xulstore.json``.  A
        profile created while the desktop was smaller — or with an old window
        position — can therefore open with grey desktop strips on either side.
        Preparing the profile before launch avoids that first-run surprise.
        The window manager still owns the real geometry, so this is a hint and
        never a hard requirement.
        """
        self.ensure()
        width = int(width) if width and int(width) > 0 else 1600
        height = int(height) if height and int(height) > 0 else 1000
        data = {
            "chrome://browser/content/browser.xhtml": {
                "main-window": {
                    "screenX": "0",
                    "screenY": "0",
                    "width": str(width),
                    "height": str(height),
                    "sizemode": "maximized",
                }
            }
        }
        tmp = self.window_state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, self.window_state_path)
        return self.window_state_path

    def load_config(self) -> BrowserConfig:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return BrowserConfig()
        return BrowserConfig.from_dict(raw)

    def save_config(self, config: BrowserConfig) -> Path:
        self.ensure()
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.config_path)
        return self.config_path

    def apply(self, config: Optional[BrowserConfig] = None) -> Path:
        """Write ``user.js`` and the Omnify metadata, returning the user.js path."""
        config = config or self.load_config()
        self.save_config(config)
        prefs = prefs_for(config)
        text = user_js_text(prefs)
        self.ensure()
        tmp = self.user_js.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.user_js)
        # A profile-local WebExtension is normally loaded by Firefox when
        # signatures are not enforced (true for LibreWolf).  Failure here must
        # never prevent the browser itself from starting.
        try:
            from .browser_shields import install_extension, remove_extension

            if config.protections.shields:
                install_extension(self.root)
            else:
                remove_extension(self.root)
        except (OSError, ValueError):
            pass
        return self.user_js


def _pref_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def user_js_text(prefs: Dict[str, Any], generated_at: Optional[float] = None) -> str:
    """Render a deterministic Firefox ``user.js`` file."""
    stamp = time.strftime("%Y-%m-%d", time.gmtime(generated_at or time.time()))
    lines = [
        "// Omnify Browser — managed privacy profile.",
        "// Generated from the Browser module; changes here are overwritten.",
        f"// Generated: {stamp}",
        "",
    ]
    for key in sorted(prefs):
        lines.append(f"user_pref({json.dumps(str(key))}, {_pref_value(prefs[key])});")
    lines.append("")
    return "\n".join(lines)


def prefs_for(config: BrowserConfig) -> Dict[str, Any]:
    """Build the complete preference set for *config*.

    The result is a plain dict so it can be inspected and tested without a
    running browser.  Values use Firefox's native JSON-like types.
    """
    p = config.protections
    prefs: Dict[str, Any] = {
        # Anti-tracking / anti-advertising defaults.
        "browser.contentblocking.category": "strict" if p.tracking else "standard",
        "privacy.trackingprotection.enabled": bool(p.tracking),
        "privacy.trackingprotection.pbmode.enabled": bool(p.tracking),
        "privacy.trackingprotection.socialtracking.enabled": bool(p.tracking),
        "privacy.trackingprotection.cryptomining.enabled": bool(p.tracking),
        "privacy.trackingprotection.fingerprinting.enabled": bool(p.tracking),
        "privacy.trackingprotection.emailtracking.enabled": bool(p.tracking),
        "privacy.annotate_channels.strict_list.enabled": bool(p.tracking),
        "privacy.partition.network_state": True,
        "privacy.donottrackheader.enabled": True,
        "privacy.globalprivacycontrol.enabled": bool(p.global_privacy_control),
        "network.cookie.cookieBehavior": 5 if p.cookies else 0,
        "network.cookie.lifetimePolicy": 0,
        "network.cookie.thirdparty.sessionOnly": bool(p.cookies),
        "dom.security.https_only_mode": bool(p.https_only),
        "dom.security.https_only_mode_pbm": bool(p.https_only),
        "dom.security.https_only_mode_send_http_background_request": False,
        # Fingerprinting resistance.  Letterboxing is part of LibreWolf's
        # default posture; keeping it explicit makes Firefox use it too.
        "privacy.resistFingerprinting": bool(p.fingerprinting),
        # Letterboxing is excellent anti-fingerprinting, but its grey side
        # bands look like a layout bug on a maximized desktop window.  Keep
        # the rest of RFP enabled and make the browser occupy the full window.
        "privacy.resistFingerprinting.letterboxing": False,
        "privacy.resistFingerprinting.block_mozAddonManager": bool(p.fingerprinting),
        "privacy.resistFingerprinting.autoDeclineNoUserInputCanvasPrompts": bool(p.fingerprinting),
        "privacy.firstparty.isolate": True,
        "privacy.firstparty.isolate.restrict_opener_access": True,
        # WebRTC: never expose a local address without a proxy.
        "media.peerconnection.ice.default_address_only": bool(p.webrtc),
        "media.peerconnection.ice.proxy_only_if_behind_proxy": bool(p.webrtc),
        "media.peerconnection.ice.no_host": bool(p.webrtc),
        # Encrypted DNS (Quad9, no logging/filtering vendor lock-in).
        "network.trr.mode": 2 if p.doh else 5,
        "network.trr.uri": "https://dns.quad9.net/dns-query",
        "network.trr.custom_uri": "https://dns.quad9.net/dns-query",
        "network.trr.confirmationNS": "skip",
        "network.trr.bootstrapAddress": "9.9.9.9",
        # Quiet defaults: no telemetry, studies, Pocket or sponsored tiles.
        "toolkit.telemetry.enabled": not bool(p.telemetry_off),
        "toolkit.telemetry.unified": not bool(p.telemetry_off),
        "toolkit.telemetry.archive.enabled": not bool(p.telemetry_off),
        "datareporting.healthreport.uploadEnabled": not bool(p.telemetry_off),
        "datareporting.policy.dataSubmissionEnabled": not bool(p.telemetry_off),
        "app.shield.optoutstudies.enabled": not bool(p.telemetry_off),
        "app.normandy.enabled": not bool(p.telemetry_off),
        "browser.ping-centre.telemetry": not bool(p.telemetry_off),
        "browser.newtabpage.activity-stream.feeds.telemetry": not bool(p.telemetry_off),
        "browser.newtabpage.activity-stream.showSponsored": not bool(p.sponsored_off),
        "browser.newtabpage.activity-stream.showSponsoredTopSites": not bool(p.sponsored_off),
        "browser.newtabpage.activity-stream.feeds.section.topstories": not bool(p.sponsored_off),
        "browser.newtabpage.activity-stream.feeds.snippets": not bool(p.sponsored_off),
        "browser.urlbar.suggest.quicksuggest.sponsored": not bool(p.sponsored_off),
        "extensions.pocket.enabled": not bool(p.sponsored_off),
        "browser.discovery.enabled": False,
        "browser.search.suggest.enabled": False,
        "browser.urlbar.quicksuggest.enabled": False,
        "browser.urlbar.quicksuggest.dataCollection.enabled": False,
        # Firefox's local AI/chat surfaces are opt-in; Omnify keeps them off.
        "browser.ml.enable": False,
        "browser.ml.chat.enabled": False,
        "browser.ml.linkPreview.enabled": False,
        "browser.tabs.firefox-view": False,
        "browser.preferences.moreFromMozilla": False,
        # Referrer minimisation: cross-site requests reveal only the origin.
        "network.http.referer.XOriginPolicy": 2,
        "network.http.referer.XOriginTrimmingPolicy": 2,
        "network.http.referer.trimmingPolicy": 2,
        # Strong password-store defaults.  The master password itself can only
        # be set in about:logins; the UI links there and explains the trade-off.
        "signon.rememberSignons": bool(p.password_manager),
        "signon.autofillForms": bool(p.password_manager),
        "signon.generation.enabled": True,
        "signon.management.page.breach-alerts.enabled": True,
        "signon.formlessCapture.enabled": False,
        "signon.storeWhenAutocompleteOff": False,
        "security.insecurepassword.allow_http": False,
        "browser.formfill.enable": bool(p.password_manager),
        "browser.startup.page": 3 if p.session_restore else 1,
        "browser.startup.homepage": "about:blank",
        "browser.newtabpage.enabled": True,
        # Lightweight mode: lazy session restore and unload background tabs
        # under memory pressure without weakening the process sandbox
        # (Fission stays enabled).
        "browser.tabs.unloadOnLowMemory": bool(p.memory_saver),
        "browser.low_commit_space_threshold_mb": 512,
        "browser.sessionstore.restore_on_demand": bool(p.memory_saver),
        "browser.sessionstore.restore_tabs_lazily": bool(p.memory_saver),
        "dom.ipc.processCount": 4,
        "fission.autostart": True,
        "browser.tabs.remote.autostart": True,
        # Let profile-local, unsigned Omnify Shields load in LibreWolf.
        "xpinstall.signatures.required": False,
        "extensions.langpacks.signatures.required": False,
        "extensions.autoDisableScopes": 0,
        "extensions.postDownloadThirdPartyPrompt": False,
        # A conventional browser surface: tabs, devtools, downloads.
        "devtools.enabled": True,
        "browser.tabs.warnOnClose": False,
        "browser.tabs.warnOnCloseOtherTabs": False,
        "browser.warnOnQuit": False,
        "browser.sessionstore.warnOnQuit": False,
        "browser.download.useDownloadDir": False,
        "browser.download.always_ask_before_handling_new_types": True,
        "browser.helperApps.neverAsk.saveToDisk": "",
        # Search: no Google dependency by default.
        "browser.search.defaultenginename": "DuckDuckGo",
        "browser.urlbar.placeholderName": "DuckDuckGo",
        # The UI language and the HTTP Accept-Language header are deliberately
        # independent.  A Ukrainian interface should not force every site to
        # see a rare locale fingerprint.  Firefox's built-in RFP prompt asks
        # whether to spoof Accept-Language; Omnify answers that policy in its
        # own UI instead, so "1" means "keep the explicit language list" and
        # avoids an unexpected browser dialog on startup.
        "privacy.spoof_english": 1,
        **(
            {"intl.locale.requested": config.language}
            if config.language
            else {}
        ),
        **(
            {"intl.accept_languages": config.accept_language}
            if config.accept_language != "browser"
            else {}
        ),
        "browser.urlbar.suggest.searches": False,
        "browser.urlbar.suggest.topsites": False,
        "browser.urlbar.suggest.engines": False,
        "browser.region.network.url": "",
        "browser.region.update.enabled": False,
        "browser.newtabpage.activity-stream.default.sites": "",
    }
    # Clear-on-close is a stronger promise than the default: only data the
    # user did not ask to keep (history/cookies/cache) is swept.
    clear = bool(p.clear_on_close)
    prefs.update(
        {
            "privacy.sanitize.sanitizeOnShutdown": clear,
            "privacy.clearOnShutdown.history": clear,
            "privacy.clearOnShutdown.cookies": clear,
            "privacy.clearOnShutdown.cache": clear,
            "privacy.clearOnShutdown.sessions": clear,
            "privacy.clearOnShutdown.offlineApps": clear,
            "privacy.clearOnShutdown.siteSettings": False,
            "privacy.clearOnShutdown.formdata": clear,
            "privacy.clearOnShutdown.downloads": False,
        }
    )
    prefs.update(config.proxy_prefs())
    return prefs


def _proxy_prefs(config: BrowserConfig) -> Dict[str, Any]:
    proxy = config.proxy
    if proxy.mode == "direct":
        return {"network.proxy.type": 0}
    if proxy.mode == "system":
        return {"network.proxy.type": 5}
    proxy.validate()
    host = proxy.host.strip()
    port = int(proxy.port)
    prefs: Dict[str, Any] = {
        "network.proxy.type": 1,
        "network.proxy.no_proxies_on": "localhost, 127.0.0.1, ::1",
    }
    if proxy.protocol == "socks":
        prefs.update(
            {
                "network.proxy.socks": host,
                "network.proxy.socks_port": port,
                "network.proxy.socks_version": 5,
                "network.proxy.socks_remote_dns": True,
                "network.proxy.share_proxy_settings": True,
            }
        )
    elif proxy.protocol == "https":
        prefs.update(
            {
                "network.proxy.ssl": host,
                "network.proxy.ssl_port": port,
                "network.proxy.http": host,
                "network.proxy.http_port": port,
                "network.proxy.share_proxy_settings": True,
            }
        )
    else:
        prefs.update(
            {
                "network.proxy.http": host,
                "network.proxy.http_port": port,
                "network.proxy.ssl": host,
                "network.proxy.ssl_port": port,
                "network.proxy.share_proxy_settings": True,
            }
        )
    return prefs


def detect_browser(
    override: Optional[str] = None,
    home: Optional[Path | str] = None,
    candidates: Optional[Sequence[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]]]] = None,
    use_path: bool = True,
) -> Optional[BrowserInfo]:
    """Find a supported browser.

    *override* always wins when it points to an executable.  Canonical
    candidates are tried in order (LibreWolf, Zen, Firefox); ``use_path`` also
    checks names on ``$PATH``.  Tests pass fake candidates and ``use_path=False``.
    """
    base = Path(home).expanduser() if home is not None else Path.home()
    if override:
        path = Path(override).expanduser()
        if _is_executable(path):
            key = "librewolf" if "librewolf" in path.name.lower() else (
                "zen" if "zen" in path.name.lower() else "custom"
            )
            name = {"librewolf": "LibreWolf", "zen": "Zen Browser"}.get(key, path.name)
            return BrowserInfo(key=key, name=name, path=str(path))
    for key, name, paths, names in candidates or BROWSER_CANDIDATES:
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = base / path
            if _is_executable(path):
                return BrowserInfo(key=key, name=name, path=str(path))
        if use_path:
            for binary in names:
                found = shutil.which(binary)
                if found and _is_executable(Path(found)):
                    return BrowserInfo(key=key, name=name, path=found)
    return None


def detect_tor_browser(
    home: Optional[Path | str] = None,
    candidates: Optional[Sequence[str]] = None,
) -> Optional[TorBrowserInfo]:
    """Find a Tor Browser launcher, including Omnify's portable copy."""
    base = Path(home).expanduser() if home is not None else Path.home()
    for raw in candidates or TOR_BROWSER_CANDIDATES:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = base / path
        if _is_executable(path):
            return TorBrowserInfo(path=str(path))
    return None


def build_tor_command(
    tor_browser: TorBrowserInfo, urls: Iterable[str] = ()
) -> List[str]:
    """Build a Tor Browser launch command without touching its profile."""
    command = [tor_browser.path]
    for url in urls:
        if not isinstance(url, str) or "\x00" in url:
            raise ValueError("Invalid URL")
        command.append(url)
    return command


def launch_tor_browser(
    tor_browser: Optional[TorBrowserInfo],
    urls: Iterable[str] = (),
    dry_run: bool = False,
) -> List[str]:
    """Start Tor Browser (Tor routing + its own fingerprint hardening)."""
    if tor_browser is None:
        raise RuntimeError("Tor Browser is not installed")
    command = build_tor_command(tor_browser, urls=urls)
    if dry_run:
        return command
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    schedule_browser_maximize(("tor browser", "tor-browser"))
    return command


def browser_version(browser: Optional[BrowserInfo]) -> Optional[str]:
    """Read a Firefox-compatible browser version, if the binary reports one."""
    if browser is None:
        return None
    try:
        result = subprocess.run(
            [browser.path, "--version"],
            env={**os.environ, **browser.environment},
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)", result.stdout or result.stderr)
    return match.group(1) if match else None


def install_language_pack(
    profile: BrowserProfile,
    browser: BrowserInfo,
    code: str = "uk",
    timeout: int = 30,
) -> Path:
    """Download and install the official Firefox language pack for *code*.

    The language pack is stored in the managed profile as a normal signed XPI;
    LibreWolf loads it on the next start.  No third-party server is involved.
    """
    version = browser_version(browser)
    if not version:
        raise RuntimeError("Could not determine the browser version")
    url = FIREFOX_LANGPACK_URL.format(version=version, code=code)
    profile.ensure()
    target_dir = profile.root / "extensions"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"langpack-{code}@firefox.mozilla.org.xpi"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read()
    if len(payload) < 10_000:
        raise RuntimeError("The downloaded language pack is too small")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    return target


def screen_size_hint() -> Optional[Tuple[int, int]]:
    """Return the primary monitor size when GDK can provide it.

    This stays optional and headless-safe: a missing display or a minimal GTK
    installation simply means the browser keeps its own window geometry.
    """
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gtk

        # In the standalone launcher there is no Gtk.Application yet, so the
        # display has to be opened explicitly before querying the monitor.
        display = Gdk.Display.get_default()
        if display is None:
            try:
                Gtk.init()
            except Exception:
                pass
            display = Gdk.Display.get_default()
        if display is None:
            return None
        monitors = display.get_monitors()
        if monitors is None or monitors.get_n_items() < 1:
            return None
        geometry = monitors.get_item(0).get_geometry()
        if geometry.width <= 0 or geometry.height <= 0:
            return None
        return int(geometry.width), int(geometry.height)
    except Exception:
        return None


def build_command(
    browser: BrowserInfo,
    profile: Path | str,
    urls: Iterable[str] = (),
    private: bool = False,
    new_instance: bool = False,
) -> List[str]:
    """Construct a browser command; never invokes a shell."""
    if not browser.path:
        raise ValueError("No browser executable")
    profile_path = Path(profile).expanduser().resolve()
    command = [browser.path, "--profile", str(profile_path)]
    if new_instance:
        command.append("--new-instance")
    if private:
        command.append("--private-window")
    for url in urls:
        if not isinstance(url, str) or "\x00" in url:
            raise ValueError("Invalid URL")
        command.append(url)
    return command


def _kwin_script_path(classes: Sequence[str]) -> Path:
    """Write a one-shot KWin script that maximizes matching windows."""
    class_list = ", ".join(
        json.dumps(str(name).lower()) for name in classes if str(name).strip()
    )
    script = f"""
function run() {{
    var wanted = [{class_list}];
    var list = workspace.windowList ? workspace.windowList() : workspace.clientList();
    for (var i = 0; i < list.length; ++i) {{
        var w = list[i];
        var cls = String(w.resourceClass || "").toLowerCase();
        var caption = String(w.caption || "").toLowerCase();
        var match = false;
        for (var j = 0; j < wanted.length; ++j) {{
            if (cls.indexOf(wanted[j]) >= 0 || caption.indexOf(wanted[j]) >= 0) {{
                match = true;
            }}
        }}
        if (!match) {{
            continue;
        }}
        if (typeof w.setMaximize === "function") {{
            w.setMaximize(true, true);
        }} else if (typeof w.setMaximized === "function") {{
            w.setMaximized(true);
        }}
    }}
}}
run();
"""
    handle, name = tempfile.mkstemp(prefix="omnify-maximize-", suffix=".js")
    os.close(handle)
    path = Path(name)
    path.write_text(script, encoding="utf-8")
    return path


def _run_kwin_maximize(classes: Sequence[str]) -> None:
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if qdbus is None:
        return
    path = _kwin_script_path(classes)
    try:
        load = subprocess.run(
            [
                qdbus,
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if load.returncode != 0:
            return
        subprocess.run(
            [
                qdbus,
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.start",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def schedule_browser_maximize(
    classes: Sequence[str] = ("librewolf",),
    delays: Sequence[float] = (3.0, 7.0, 12.0, 18.0),
) -> None:
    """Maximize a newly started browser window on KDE Wayland.

    Firefox stores window geometry in its session store, which can override a
    perfectly valid ``xulstore.json`` on the first launch.  KWin is the final
    authority on Wayland, so a short, best-effort script is the only reliable
    way to remove the grey side margins.  Other desktops are left untouched.
    """
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "kde" not in desktop.lower():
        return
    wanted = tuple(str(item).lower() for item in classes if str(item).strip())
    if not wanted:
        return

    def worker() -> None:
        for delay in delays:
            time.sleep(max(0.0, float(delay)))
            _run_kwin_maximize(wanted)

    threading.Thread(
        target=worker,
        name="omnify-maximize",
        # Keep the launcher alive while the browser starts: a daemon thread
        # would disappear as soon as ``omnify-browser`` returns, before the
        # window exists.  The short-lived thread exits by itself.
        daemon=False,
    ).start()


def launch_browser(
    browser: Optional[BrowserInfo],
    profile: BrowserProfile,
    urls: Iterable[str] = (),
    private: bool = False,
    new_instance: bool = False,
    dry_run: bool = False,
    screen_size: Optional[Tuple[int, int]] = None,
) -> List[str]:
    """Launch *browser* with the managed profile.

    ``dry_run=True`` returns the command without spawning a process, which is
    what the tests and the launcher's ``--print-command`` mode use.
    ``screen_size`` is an optional ``(width, height)`` hint used to write a
    maximized initial window state; the window manager still makes the final
    decision.
    """
    if browser is None:
        raise RuntimeError("No supported browser found")
    profile.ensure()
    if screen_size is not None:
        profile.ensure_window_state(*screen_size)
    command = build_command(
        browser, profile.root, urls=urls, private=private, new_instance=new_instance
    )
    if dry_run:
        return command
    env = os.environ.copy()
    env.update(browser.environment)
    subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Firefox's session store can restore a 1600×1015 window on a 1920×1080
    # screen.  KWin is the final authority on Wayland, so ask it to maximize the
    # managed browser after the window has appeared.
    schedule_browser_maximize((browser.key,))
    return command


def url_from_input(text: str, search_url: str = DEFAULT_SEARCH_URL) -> str:
    """Turn a URL or search phrase into a safe URL.

    Explicit ``javascript:``/``data:`` URLs are rejected; a bare hostname gets
    ``https://``; anything else is sent to the configured search engine.
    """
    value = (text or "").strip()
    if not value:
        return ""
    template = search_url if "{query}" in search_url else DEFAULT_SEARCH_URL
    lowered = value.lower()
    if lowered.startswith(("javascript:", "data:", "vbscript:")):
        raise ValueError("Unsafe URL scheme")
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "file", "about", "ftp", "mailto"}:
        return value
    if parsed.scheme and " " not in value:
        # Unknown but explicit scheme: treat as search rather than guessing.
        return template.format(query=quote_plus(value))
    if " " not in value and ("." in value or lowered.startswith(("localhost", "127.", "[::1]"))):
        return "https://" + value
    return template.format(query=quote_plus(value))


def quick_link_url(name: str) -> str:
    """Return the internal Firefox page for a quick action."""
    pages = {
        "home": "about:home",
        "bookmarks": "about:bookmarks",
        "history": "about:history",
        "downloads": "about:downloads",
        "settings": "about:preferences",
        "extensions": "about:addons",
        "passwords": "about:logins",
        "private": "about:privatebrowsing",
    }
    return pages.get(name.lower(), "about:home")


def standalone_launcher_text(python_executable: Optional[str] = None) -> str:
    """Return the portable ``omnify-browser`` shell launcher."""
    python = python_executable or "/usr/bin/python3"
    return (
        "#!/bin/sh\n"
        "# Omnify Browser — standalone LibreWolf/Firefox launcher.\n"
        "# Generated by the Omnify Browser module; edit the module instead.\n"
        f'exec {json.dumps(python)} -m omnify.browser_launcher "$@"\n'
    )


def desktop_entry_text(executable: Path | str) -> str:
    """Return the .desktop entry for the standalone browser."""
    raw = str(executable)
    quoted = f'"{raw}"' if any(ch.isspace() for ch in raw) else raw
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Omnify Browser\n"
        "GenericName=Private Web Browser\n"
        "Comment=LibreWolf/Firefox with Brave-style protections, managed by Omnify\n"
        f"Exec={quoted} %U\n"
        f"TryExec={raw}\n"
        "Icon=omnify\n"
        "Terminal=false\n"
        "Categories=Network;WebBrowser;Utility;\n"
        "MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;"
        "x-scheme-handler/https;x-scheme-handler/about;\n"
        "StartupNotify=true\n"
        "StartupWMClass=LibreWolf\n"
    )


@dataclass(frozen=True)
class LauncherPaths:
    launcher: Path
    desktop: Path


def install_standalone_launcher(
    bin_dir: Optional[Path | str] = None,
    desktop_dir: Optional[Path | str] = None,
    python_executable: Optional[str] = None,
    refresh: bool = False,
) -> LauncherPaths:
    """Install ``omnify-browser`` and its application-menu entry.

    This is a no-root, user-local install.  It is idempotent and does not
    download anything.
    """
    home = Path.home()
    bin_path = Path(bin_dir).expanduser() if bin_dir else home / ".local" / "bin"
    desktop_path = (
        Path(desktop_dir).expanduser()
        if desktop_dir
        else home / ".local" / "share" / "applications"
    )
    bin_path.mkdir(parents=True, exist_ok=True)
    desktop_path.mkdir(parents=True, exist_ok=True)
    launcher = bin_path / LAUNCHER_NAME
    desktop = desktop_path / DESKTOP_NAME
    launcher.write_text(standalone_launcher_text(python_executable), encoding="utf-8")
    launcher.chmod(0o755)
    desktop.write_text(desktop_entry_text(launcher), encoding="utf-8")
    if refresh:
        try:
            subprocess.run(
                ["update-desktop-database", str(desktop_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return LauncherPaths(launcher=launcher, desktop=desktop)


def protection_summary(config: BrowserConfig) -> List[Tuple[str, bool]]:
    """A compact checklist for the UI's status card."""
    p = config.protections
    return [
        ("Ad & tracker blocking", p.tracking),
        ("Block third-party cookies", p.cookies),
        ("HTTPS-only mode", p.https_only),
        ("Fingerprint resistance", p.fingerprinting),
        ("WebRTC address guard", p.webrtc),
        ("Encrypted DNS (DoH)", p.doh),
        ("Global Privacy Control", p.global_privacy_control),
        ("No telemetry / sponsored tiles", p.telemetry_off and p.sponsored_off),
        ("Memory saver", p.memory_saver),
        ("Omnify Shields counter", p.shields),
        ("Forget data on close", p.clear_on_close),
    ]
