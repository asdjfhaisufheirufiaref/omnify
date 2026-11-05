"""Omnify Shields: a small, auditable WebExtension for LibreWolf.

LibreWolf ships uBlock Origin, which is the primary blocker.  Firefox does not
expose uBlock's per-tab counters to an external application, so Omnify also
installs this deliberately small companion extension:

* it blocks a curated list of advertising/tracking hosts;
* it shows the number of requests it blocked for the current tab as a badge;
* the popup lets the user disable it for one site when a site breaks;
* it never sends data anywhere and uses only local extension storage.

This module is headless and standard-library-only.  :func:`install_extension`
writes a normal Firefox extension directory into the managed profile.  The
extension is intentionally not a replacement for uBlock Origin: the two layers
complement each other and the popup says so.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

EXTENSION_ID = "omnify-shields@omnify"
EXTENSION_NAME = "Omnify Shields"
EXTENSION_VERSION = "1.0.0"

#: A compact, conservative host list.  uBlock Origin remains the broad filter
#: engine; this list is intentionally stable and easy to audit.
TRACKER_HOSTS: Tuple[str, ...] = (
    "doubleclick.net",
    "googleadservices.com",
    "googlesyndication.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "stats.g.doubleclick.net",
    "amazon-adsystem.com",
    "assoc-amazon.com",
    "media-amazon.com",
    "ads.yahoo.com",
    "advertising.com",
    "adnxs.com",
    "adsrvr.org",
    "criteo.com",
    "criteo.net",
    "taboola.com",
    "outbrain.com",
    "scorecardresearch.com",
    "quantserve.com",
    "quantcount.com",
    "moatads.com",
    "facebook.net",
    "connect.facebook.net",
    "pixel.facebook.com",
    "analytics.twitter.com",
    "static.ads-twitter.com",
    "ads.linkedin.com",
    "analytics.linkedin.com",
    "snap.licdn.com",
    "bat.bing.com",
    "clarity.ms",
    "hotjar.com",
    "static.hotjar.com",
    "mixpanel.com",
    "segment.io",
    "segment.com",
    "amplitude.com",
    "fullstory.com",
    "mouseflow.com",
    "crazyegg.com",
    "optimizely.com",
    "newrelic.com",
    "nr-data.net",
    "sentry.io",
    "bugsnag.com",
    "chartbeat.com",
    "parsely.com",
    "sail-horizon.com",
    "krxd.net",
    "rlcdn.com",
    "tapad.com",
    "tynt.com",
    "zemanta.com",
    "bidswitch.net",
    "rubiconproject.com",
    "pubmatic.com",
    "openx.net",
    "smartadserver.com",
    "casalemedia.com",
    "contextweb.com",
    "indexww.com",
    "sharethrough.com",
    "teads.tv",
    "spotxchange.com",
    "yieldmo.com",
    "3lift.com",
    "gumgum.com",
    "adcolony.com",
    "applovin.com",
    "inmobi.com",
    "unityads.unity3d.com",
    "vungle.com",
    "chartboost.com",
    "mopub.com",
    "ironsrc.com",
    "startapp.com",
    "adform.net",
    "smartclip.net",
    "yieldlab.net",
    "1rx.io",
    "agkn.com",
    "bluekai.com",
    "exelator.com",
    "demdex.net",
    "omtrdc.net",
    "everesttech.net",
    "adobedtm.com",
    "heapanalytics.com",
    "kissmetrics.com",
    "statcounter.com",
    "clicky.com",
    "getclicky.com",
    "matomo.cloud",
    "piwik.pro",
    "plausible.io",
    "umami.is",
    "cloudflareinsights.com",
    "static.cloudflareinsights.com",
    "gravatar.com",
    "disqus.com",
    "disquscdn.com",
    "addthis.com",
    "sharethis.com",
    "sumo.com",
    "onesignal.com",
    "pushengage.com",
    "pushcrew.com",
    "izooto.com",
    "cleverpush.com",
    "webpushr.com",
    "sailthru.com",
    "list-manage.com",
    "mailchimp.com",
    "hubspot.com",
    "hs-scripts.com",
    "hsforms.net",
    "marketo.com",
    "mktoresp.com",
    "pardot.com",
    "salesforce.com",
    "intercom.io",
    "intercomcdn.com",
    "drift.com",
    "driftt.com",
    "livechatinc.com",
    "zendesk.com",
    "zopim.com",
    "tawk.to",
    "olark.com",
    "crisp.chat",
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """Build one PNG chunk (length + type + data + CRC32)."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _inside_rounded_rect(
    x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float
) -> bool:
    if x < left or x > right or y < top or y > bottom:
        return False
    if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
        return True
    cx = left + radius if x < left + radius else right - radius
    cy = top + radius if y < top + radius else bottom - radius
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def shield_icon_png(size: int = 32) -> bytes:
    """Return a tiny green shield with a white check as a PNG.

    Drawing the icon in code keeps the project asset-free and means the same
    icon works on every size Firefox requests.  The result is deterministic.
    """
    size = max(16, min(128, int(size)))
    green = (64, 181, 111, 255)
    dark = (31, 122, 74, 255)
    white = (255, 255, 255, 255)
    rows: List[bytes] = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            x = (px + 0.5) / size
            y = (py + 0.5) / size
            inside = _inside_rounded_rect(x, y, 0.12, 0.08, 0.88, 0.78, 0.20)
            if y > 0.54:
                # Taper the bottom half into a shield point.
                half = 0.38 * (1.0 - min(1.0, (y - 0.54) / 0.40))
                inside = 0.50 - half <= x <= 0.50 + half and y <= 0.94
            colour = (0, 0, 0, 0)
            if inside:
                colour = dark if y > 0.56 else green
                # White check mark (two thick line segments).
                if 0.28 <= y <= 0.58:
                    if abs((y - 0.46) - 0.55 * (x - 0.33)) < 0.065 and 0.28 <= x <= 0.46:
                        colour = white
                    if abs((y - 0.46) + 0.62 * (x - 0.70)) < 0.065 and 0.44 <= x <= 0.76:
                        colour = white
            row.extend(colour)
        rows.append(bytes(row))
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def blocklist_js(hosts: Iterable[str] = TRACKER_HOSTS) -> str:
    """Render the blocklist as a JavaScript array literal."""
    values = sorted({host.strip().lower() for host in hosts if host.strip()})
    return "const OMNIFY_BLOCKED_HOSTS = " + json.dumps(values, ensure_ascii=False) + ";\n"


def background_js() -> str:
    """The extension's background script (MV2, Firefox)."""
    return (
        '"use strict";\n'
        + blocklist_js()
        + r'''
const blockedByTab = new Map();
const sitePaused = new Map();

function normalizeHost(host) {
  return String(host || "").toLowerCase().replace(/\.$/, "");
}

function isBlockedHost(host) {
  const value = normalizeHost(host);
  return OMNIFY_BLOCKED_HOSTS.some((entry) => value === entry || value.endsWith("." + entry));
}

function originKey(url) {
  try {
    const parsed = new URL(url);
    return parsed.origin;
  } catch (_error) {
    return "";
  }
}

async function isPaused(tabId, url) {
  const key = originKey(url);
  if (!key) return false;
  if (sitePaused.has(key)) return sitePaused.get(key);
  const stored = await browser.storage.local.get("pausedSites");
  const paused = Array.isArray(stored.pausedSites) ? stored.pausedSites : [];
  const value = paused.includes(key);
  sitePaused.set(key, value);
  return value;
}

async function setPaused(url, paused) {
  const key = originKey(url);
  if (!key) return;
  const stored = await browser.storage.local.get("pausedSites");
  const set = new Set(Array.isArray(stored.pausedSites) ? stored.pausedSites : []);
  if (paused) set.add(key); else set.delete(key);
  sitePaused.set(key, paused);
  await browser.storage.local.set({ pausedSites: Array.from(set) });
}

function updateBadge(tabId, count, paused) {
  if (tabId == null || tabId < 0) return;
  let text = "";
  if (paused) text = "off";
  else if (count > 0) text = count > 999 ? "999+" : String(count);
  browser.browserAction.setBadgeText({ tabId, text }).catch(() => {});
  browser.browserAction.setBadgeBackgroundColor({
    tabId,
    color: paused ? "#777777" : "#3fb56f",
  }).catch(() => {});
}

browser.webRequest.onBeforeRequest.addListener(
  async (details) => {
    if (details.tabId < 0 || details.type === "main_frame") return {};
    if (await isPaused(details.tabId, details.url)) return {};
    let hostname = "";
    try { hostname = new URL(details.url).hostname; } catch (_error) { return {}; }
    if (!isBlockedHost(hostname)) return {};
    const count = (blockedByTab.get(details.tabId) || 0) + 1;
    blockedByTab.set(details.tabId, count);
    updateBadge(details.tabId, count, false);
    return { cancel: true };
  },
  { urls: ["<all_urls>"], types: ["script", "image", "xmlhttprequest", "sub_frame", "stylesheet", "font", "media", "websocket", "other"] },
  ["blocking"]
);

browser.tabs.onRemoved.addListener((tabId) => {
  blockedByTab.delete(tabId);
});

browser.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") return;
  const url = tab && tab.url ? tab.url : "";
  const paused = await isPaused(tabId, url);
  updateBadge(tabId, blockedByTab.get(tabId) || 0, paused);
});

browser.tabs.onActivated.addListener(async (info) => {
  const tab = await browser.tabs.get(info.tabId).catch(() => null);
  if (!tab) return;
  const paused = await isPaused(info.tabId, tab.url || "");
  updateBadge(info.tabId, blockedByTab.get(info.tabId) || 0, paused);
});

browser.runtime.onMessage.addListener(async (message, sender) => {
  const tabs = await browser.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0] ? tabs[0] : null;
  if (!tab) return { count: 0, paused: false, url: "" };
  const paused = await isPaused(tab.id, tab.url || "");
  if (message && message.what === "toggle") {
    await setPaused(tab.url || "", !paused);
    updateBadge(tab.id, blockedByTab.get(tab.id) || 0, !paused);
  }
  return {
    count: blockedByTab.get(tab.id) || 0,
    paused: message && message.what === "toggle" ? !paused : paused,
    url: tab.url || "",
  };
});
'''
    )


POPUP_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 12px; width: 270px;
  font: 13px/1.35 system-ui, sans-serif;
  background: #1e1f24; color: #e7e9ee;
}
h1 { margin: 0 0 8px; font-size: 15px; color: #6ecf8f; }
.count { font-size: 30px; font-weight: 700; margin: 4px 0 2px; }
.note { color: #9aa0ab; margin-bottom: 10px; }
button {
  width: 100%; padding: 8px 10px; border: 1px solid #3a3d46;
  border-radius: 8px; background: #2a2d34; color: #e7e9ee;
  cursor: pointer; font: inherit;
}
button:hover { background: #33363e; }
button.paused { border-color: #e6b455; color: #e6b455; }
a { color: #8fb6ff; display: inline-block; margin-top: 10px; font-size: 12px; }
"""


POPUP_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omnify Shields</title>
<link rel="stylesheet" href="popup.css">
</head>
<body>
<h1>Omnify Shields</h1>
<div id="count" class="count">0</div>
<div class="note">requests blocked on this tab</div>
<button id="toggle">Disable for this site</button>
<div id="status" class="note"></div>
<a href="about:addons" target="_blank">Manage protections</a>
<script src="popup.js"></script>
</body>
</html>
"""


POPUP_JS = r'''
"use strict";

const countElement = document.getElementById("count");
const toggleElement = document.getElementById("toggle");
const statusElement = document.getElementById("status");

function render(data) {
  countElement.textContent = String(data.count || 0);
  if (data.paused) {
    toggleElement.textContent = "Enable for this site";
    toggleElement.classList.add("paused");
    statusElement.textContent = "Shields are paused here.";
  } else {
    toggleElement.textContent = "Disable for this site";
    toggleElement.classList.remove("paused");
    statusElement.textContent = "uBlock Origin is also active.";
  }
}

browser.runtime.sendMessage({ what: "stats" }).then(render).catch(() => {
  countElement.textContent = "—";
  statusElement.textContent = "Reload this page to refresh the counter.";
});

toggleElement.addEventListener("click", () => {
  browser.runtime.sendMessage({ what: "toggle" }).then(render).catch(() => {});
});
'''


def manifest() -> Dict[str, object]:
    """Return the Firefox WebExtension manifest as a plain dict."""
    return {
        "manifest_version": 2,
        "name": EXTENSION_NAME,
        "version": EXTENSION_VERSION,
        "description": "A lightweight, local-only companion blocker for Omnify Browser.",
        "browser_specific_settings": {"gecko": {"id": EXTENSION_ID}},
        "permissions": [
            "webRequest",
            "webRequestBlocking",
            "tabs",
            "storage",
            "<all_urls>",
        ],
        "background": {"scripts": ["background.js"], "persistent": False},
        "browser_action": {
            "default_title": "Omnify Shields",
            "default_popup": "popup.html",
            "default_icon": {
                "16": "icon-16.png",
                "32": "icon-32.png",
                "48": "icon-48.png",
                "96": "icon-96.png",
            },
        },
        "icons": {
            "16": "icon-16.png",
            "32": "icon-32.png",
            "48": "icon-48.png",
            "96": "icon-96.png",
        },
    }


def extension_dir(profile: Path | str) -> Path:
    """Return the extension directory inside a Firefox profile."""
    return Path(profile) / "extensions" / EXTENSION_ID


def install_extension(profile: Path | str) -> Path:
    """Write the unpacked extension into *profile* and return its directory."""
    target = extension_dir(profile)
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "background.js").write_text(background_js(), encoding="utf-8")
    (target / "popup.html").write_text(POPUP_HTML, encoding="utf-8")
    (target / "popup.css").write_text(POPUP_CSS, encoding="utf-8")
    (target / "popup.js").write_text(POPUP_JS, encoding="utf-8")
    for size in (16, 32, 48, 96):
        (target / f"icon-{size}.png").write_bytes(shield_icon_png(size))
    return target


def remove_extension(profile: Path | str) -> None:
    """Remove the unpacked extension (used when the user disables Shields)."""
    target = extension_dir(profile)
    if not target.exists():
        return
    for child in sorted(target.rglob("*"), reverse=True):
        try:
            if child.is_dir():
                child.rmdir()
            else:
                child.unlink()
        except OSError:
            pass
    try:
        target.rmdir()
    except OSError:
        pass


def shield_status(profile: Path | str) -> Tuple[bool, int]:
    """Return ``(manifest_present, tracker_host_count)`` for UI status."""
    target = extension_dir(profile)
    manifest_path = target / "manifest.json"
    return manifest_path.is_file(), len(TRACKER_HOSTS)
