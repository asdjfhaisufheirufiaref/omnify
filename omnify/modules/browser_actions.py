"""Browser selection actions and their local WebExtension.

LibreWolf/Firefox cannot launch external programs from a context menu without
native messaging.  Omnify uses a smaller, auditable approach instead:

* a companion WebExtension adds two items for selected text — **Move
  selection to a new note** and **Solve in Calculator**;
* the extension posts the selection to a loopback-only HTTP endpoint;
* :class:`SelectionBridge` validates a per-profile random token and forwards
  the action to the running Omnify process.

The server binds ``127.0.0.1`` only, accepts POST requests only, caps the body
size and never logs selected text.  No page history or browsing data leaves
the machine.  When Omnify is not running, the extension shows a browser
notification instead of silently dropping the action.

This module is headless, standard-library-only and covered by unit tests.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

EXTENSION_ID = "omnify-actions@omnify"
EXTENSION_NAME = "Omnify Actions"
EXTENSION_VERSION = "1.0.0"
EXTENSION_DIR = "omnify-actions@omnify"
CONFIG_NAME = "omnify-actions.json"

#: A stable default port makes the extension survive a browser restart while
#: Omnify is not running.  The bridge moves to an ephemeral port if this one
#: is already occupied.
DEFAULT_PORT = 47823
MIN_PORT = 1024
MAX_TEXT = 20000
MAX_BODY = 128 * 1024
TOKEN_BYTES = 32

ActionHandler = Callable[[str, str, str, str], bool]


# ---------------------------------------------------------------------------
# Profile configuration
# ---------------------------------------------------------------------------
def config_path(profile: Path | str) -> Path:
    return Path(profile) / CONFIG_NAME


def _valid_port(value: Any) -> Optional[int]:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if MIN_PORT <= port <= 65535:
        return port
    return None


def _write_private_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write a small JSON file readable only by the owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def ensure_config(profile: Path | str) -> Dict[str, Any]:
    """Load or create the bridge port and token for *profile*.

    The token is regenerated only when missing or obviously truncated, so a
    browser that stays open across Omnify restarts keeps a working extension.
    """
    path = config_path(profile)
    data: Dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}

    token = str(data.get("token") or "")
    if len(token) < TOKEN_BYTES * 2 or not re.fullmatch(r"[0-9a-f]+", token):
        token = secrets.token_hex(TOKEN_BYTES)
    port = _valid_port(data.get("port")) or DEFAULT_PORT
    config = {"port": port, "token": token}
    if data.get("port") != port or data.get("token") != token:
        _write_private_json(path, config)
    elif not path.exists():
        _write_private_json(path, config)
    return config


def endpoint_for(config: Dict[str, Any]) -> str:
    return f"http://127.0.0.1:{int(config['port'])}/selection"


# ---------------------------------------------------------------------------
# Extension files
# ---------------------------------------------------------------------------
def extension_dir(profile: Path | str) -> Path:
    """Return the selection-actions extension directory inside a profile."""
    return Path(profile) / "extensions" / EXTENSION_DIR


def _manifest() -> Dict[str, Any]:
    return {
        "manifest_version": 2,
        "name": "__MSG_extensionName__",
        "version": EXTENSION_VERSION,
        "description": "__MSG_extensionDescription__",
        "default_locale": "en",
        "browser_specific_settings": {
            "gecko": {
                "id": EXTENSION_ID,
                "strict_min_version": "109.0",
            }
        },
        "permissions": [
            "contextMenus",
            "notifications",
            "http://127.0.0.1/*",
        ],
        "background": {"scripts": ["background.js"], "persistent": True},
        "icons": {
            "16": "icon-16.png",
            "32": "icon-32.png",
            "48": "icon-48.png",
            "96": "icon-96.png",
        },
    }


def background_js(endpoint: str, token: str) -> str:
    """Render the extension background script for one bridge endpoint."""
    return (
        '"use strict";\n'
        f"const OMNIFY_ENDPOINT = {json.dumps(str(endpoint))};\n"
        f"const OMNIFY_TOKEN = {json.dumps(str(token))};\n"
        r'''
const MENU_NOTE = "omnify-note";
const MENU_CALC = "omnify-calc";

function message(key, fallback) {
  try {
    return browser.i18n.getMessage(key) || fallback;
  } catch (_error) {
    return fallback;
  }
}

function notify(id, title, body) {
  try {
    browser.notifications.create(id, {
      type: "basic",
      iconUrl: browser.runtime.getURL("icon-48.png"),
      title: title,
      message: body
    });
  } catch (_error) {
    // Notifications are optional; the context menu action still works.
  }
}

browser.contextMenus.removeAll().then(function () {
  browser.contextMenus.create({
    id: MENU_NOTE,
    title: message("menuNote", "Move selection to a new note"),
    contexts: ["selection"]
  });
  browser.contextMenus.create({
    id: MENU_CALC,
    title: message("menuCalc", "Solve in Calculator"),
    contexts: ["selection"]
  });
}).catch(function (error) {
  console.error("Omnify Actions: could not create menus", error);
});

browser.contextMenus.onClicked.addListener(function (info, tab) {
  const action = info.menuItemId === MENU_NOTE ? "note"
    : info.menuItemId === MENU_CALC ? "calculator" : "";
  if (!action) {
    return;
  }
  const text = String(info.selectionText || "").trim();
  if (!text) {
    return;
  }
  fetch(OMNIFY_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Omnify-Token": OMNIFY_TOKEN
    },
    body: JSON.stringify({
      action: action,
      text: text.slice(0, 20000),
      title: tab && tab.title ? String(tab.title) : "",
      url: tab && tab.url ? String(tab.url) : ""
    })
  }).then(function (response) {
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    if (action === "note") {
      notify(
        "omnify-note-saved",
        message("savedTitle", "Omnify"),
        message("savedMessage", "The selection was moved to a new note.")
      );
    }
  }).catch(function (error) {
    console.error("Omnify Actions: request failed", error);
    notify(
      "omnify-action-error",
      message("errorTitle", "Omnify is not available"),
      message(
        "errorMessage",
        "Start Omnify and try the selection action again."
      )
    );
  });
});
'''
    )


def _icon_png(size: int = 32) -> bytes:
    """A small blue rounded tile with a white ring (no external assets)."""
    size = max(16, min(128, int(size)))
    blue = (43, 108, 176, 255)
    light = (66, 153, 225, 255)
    white = (255, 255, 255, 255)
    rows = []
    for py in range(size):
        row = bytearray([0])
        y = (py + 0.5) / size
        for px in range(size):
            x = (px + 0.5) / size
            radius = 0.22
            inside = (
                radius <= x <= 1.0 - radius
                or radius <= y <= 1.0 - radius
            )
            if not inside:
                cx = radius if x < radius else 1.0 - radius
                cy = radius if y < radius else 1.0 - radius
                inside = (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2
            colour = (0, 0, 0, 0)
            if inside:
                colour = blue if y > 0.55 else light
                distance = ((x - 0.5) ** 2 + (y - 0.5) ** 2) ** 0.5
                if 0.20 <= distance <= 0.32:
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


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _locale(en: str, uk: str) -> Dict[str, Any]:
    return {
        "en": {"message": en},
        "uk": {"message": uk},
    }


def install_extension(
    profile: Path | str,
    config: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write the unpacked context-menu extension into *profile*."""
    root = Path(profile)
    bridge = dict(config or ensure_config(root))
    target = extension_dir(root)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(_manifest(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "background.js").write_text(
        background_js(endpoint_for(bridge), bridge["token"]),
        encoding="utf-8",
    )
    locales = {
        "extensionName": _locale("Omnify Actions", "Дії Omnify"),
        "extensionDescription": _locale(
            "Move selected text to Omnify notes or solve it in Calculator.",
            "Переміщуйте виділений текст у нотатки Omnify або розв'язуйте "
            "його в Калькуляторі.",
        ),
        "menuNote": _locale(
            "Move selection to a new note",
            "Перемістити виділення в нову нотатку",
        ),
        "menuCalc": _locale(
            "Solve in Calculator",
            "Розв'язати в Калькуляторі",
        ),
        "savedTitle": _locale("Omnify", "Omnify"),
        "savedMessage": _locale(
            "The selection was moved to a new note.",
            "Виділення переміщено в нову нотатку.",
        ),
        "errorTitle": _locale(
            "Omnify is not available",
            "Omnify недоступний",
        ),
        "errorMessage": _locale(
            "Start Omnify and try the selection action again.",
            "Запустіть Omnify і повторіть дію з виділенням.",
        ),
    }
    for locale in ("en", "uk"):
        folder = target / "_locales" / locale
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "messages.json").write_text(
            json.dumps(
                {
                    key: values[locale]
                    for key, values in locales.items()
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    for size in (16, 32, 48, 96):
        (target / f"icon-{size}.png").write_bytes(_icon_png(size))
    return target


def remove_extension(profile: Path | str) -> None:
    """Remove the selection-actions extension from a profile."""
    target = extension_dir(profile)
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
    except OSError:
        pass


def extension_status(profile: Path | str) -> Tuple[bool, int]:
    """Return ``(manifest_present, default_port)`` for the Settings card."""
    target = extension_dir(profile)
    return (target / "manifest.json").is_file(), DEFAULT_PORT


# ---------------------------------------------------------------------------
# Note payload
# ---------------------------------------------------------------------------
def selection_note(
    text: str,
    title: str = "",
    url: str = "",
) -> Tuple[str, str]:
    """Build a note title and Markdown body for a browser selection.

    The page URL is included only when it is a real HTTP(S) address, and it is
    written as plain text so a hostile title cannot inject Markdown markup.
    """
    selection = str(text or "").strip()
    if not selection:
        raise ValueError("The selection is empty")
    page_title = " ".join(str(title or "").split())[:80]
    first_line = next(
        (line.strip() for line in selection.splitlines() if line.strip()),
        "",
    )
    note_title = page_title or first_line[:80] or "Browser selection"
    contents = selection
    source = str(url or "").strip()
    try:
        parsed = urlparse(source)
    except ValueError:
        parsed = None
    if (
        parsed is not None
        and parsed.scheme in {"http", "https"}
        and parsed.netloc
        and not any(char.isspace() for char in source)
    ):
        contents += f"\n\n---\nSource: {source}"
    return note_title, contents


def normalise_expression(text: str) -> str:
    """Turn a selected phrase into a single calculator expression line."""
    value = str(text or "").strip()
    if not value:
        return ""
    line = next((item.strip() for item in value.splitlines() if item.strip()), "")
    # A copied equation such as "2 + 2 = 4" should evaluate the left-hand
    # side.  Splitting once keeps a later "=" from silently deleting the
    # operator between two expressions.
    line = line.split("=", 1)[0]
    return (
        line.replace("−", "-")
        .replace("–", "-")
        .replace("×", "*")
        .replace("÷", "/")
        .replace("·", "*")
        .strip()
        .rstrip(".;,")
    )


# ---------------------------------------------------------------------------
# Loopback bridge
# ---------------------------------------------------------------------------
class _ActionServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, bridge) -> None:
        self.bridge = bridge
        super().__init__(address, handler)


class _ActionRequestHandler(BaseHTTPRequestHandler):
    server_version = "OmnifyActions/1.0"
    sys_version = ""

    def log_message(self, _format: str, *_args) -> None:
        """Never log selections or request bodies."""

    def _headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Omnify-Token",
        )
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")

    def _json(self, status: int, payload: Optional[Dict[str, Any]] = None) -> None:
        body = json.dumps(payload or {}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 - http.server API
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._json(405, {"error": "POST only"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if urlparse(self.path).path != "/selection":
            self._json(404, {"error": "Unknown endpoint"})
            return
        bridge = self.server.bridge
        supplied = self.headers.get("X-Omnify-Token", "")
        if not secrets.compare_digest(supplied, bridge.token):
            self._json(401, {"error": "Invalid token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": "Request body is too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json(400, {"error": "Invalid JSON"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "Invalid payload"})
            return
        action = str(payload.get("action", "") or "")
        text = str(payload.get("text", "") or "").strip()
        title = str(payload.get("title", "") or "")[:200]
        url = str(payload.get("url", "") or "")[:4000]
        if action not in {"note", "calculator"}:
            self._json(400, {"error": "Unknown action"})
            return
        if not text or len(text) > MAX_TEXT:
            self._json(400, {"error": "Selection is empty or too long"})
            return
        try:
            accepted = bool(bridge.dispatch(action, text, title, url))
        except Exception:
            accepted = False
        if not accepted:
            self._json(503, {"error": "Omnify could not handle the action"})
            return
        self._json(202, {"ok": True})


class SelectionBridge:
    """A loopback HTTP endpoint for the browser selection extension."""

    def __init__(
        self,
        profile: Path | str,
        handler: ActionHandler,
    ) -> None:
        self._profile = Path(profile)
        self._handler = handler
        self._lock = threading.Lock()
        self._server: Optional[_ActionServer] = None
        self._thread: Optional[threading.Thread] = None
        self._token = ""
        self._port = 0

    @property
    def token(self) -> str:
        return self._token

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._port}/selection"

    def start(self) -> Tuple[str, str]:
        """Start listening and return ``(endpoint, token)``.

        The configured port is preferred so a browser extension survives an
        Omnify restart.  If it is busy, an ephemeral port is used and written
        back to the profile config.
        """
        with self._lock:
            if self._server is not None:
                return self.endpoint, self._token
            config = ensure_config(self._profile)
            token = str(config["token"])
            preferred = int(config["port"])
            server = None
            for port in (preferred, 0):
                try:
                    server = _ActionServer(
                        ("127.0.0.1", port),
                        _ActionRequestHandler,
                        self,
                    )
                    break
                except OSError:
                    server = None
            if server is None:
                raise RuntimeError("Could not start the Omnify actions bridge")
            self._port = int(server.server_address[1])
            self._token = token
            self._server = server
            if self._port != preferred:
                _write_private_json(
                    config_path(self._profile),
                    {"port": self._port, "token": token},
                )
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                name="omnify-actions",
                daemon=True,
            )
            thread.start()
            self._thread = thread
            return self.endpoint, self._token

    def dispatch(self, action: str, text: str, title: str, url: str) -> bool:
        """Forward one validated action to the host application."""
        return bool(self._handler(action, text, title, url))

    def stop(self) -> None:
        """Stop the server; safe to call more than once."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)


__all__ = [
    "DEFAULT_PORT",
    "EXTENSION_ID",
    "MAX_TEXT",
    "SelectionBridge",
    "background_js",
    "endpoint_for",
    "ensure_config",
    "extension_dir",
    "extension_status",
    "install_extension",
    "normalise_expression",
    "remove_extension",
    "selection_note",
]
