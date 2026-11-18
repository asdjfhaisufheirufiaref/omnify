"""Tests for browser selection actions and the loopback bridge."""

from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from omnify.modules.browser_actions import (
    DEFAULT_PORT,
    MAX_BODY,
    MAX_TEXT,
    SelectionBridge,
    background_js,
    config_path,
    endpoint_for,
    ensure_config,
    extension_dir,
    extension_status,
    install_extension,
    normalise_expression,
    remove_extension,
    selection_note,
)
from omnify.modules.browser_engine import BrowserConfig, BrowserProfile, Protections
from omnify.modules.browser import BrowserModule


class ConfigTests(unittest.TestCase):
    def test_config_is_created_once_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profile"
            config = ensure_config(root)
            self.assertEqual(config["port"], DEFAULT_PORT)
            self.assertEqual(len(config["token"]), 64)
            self.assertTrue(all(ch in "0123456789abcdef" for ch in config["token"]))

            again = ensure_config(root)
            self.assertEqual(again, config)

    def test_invalid_token_and_port_are_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profile"
            root.mkdir()
            config_path(root).write_text(
                json.dumps({"port": 7, "token": "short"}), encoding="utf-8"
            )
            config = ensure_config(root)
            self.assertEqual(config["port"], DEFAULT_PORT)
            self.assertEqual(len(config["token"]), 64)
            self.assertNotEqual(config["token"], "short")

    def test_config_file_is_owner_only(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX permissions only")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profile"
            ensure_config(root)
            mode = stat.S_IMODE(config_path(root).stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_endpoint_uses_loopback_and_port(self) -> None:
        self.assertEqual(
            endpoint_for({"port": 50123, "token": "x"}),
            "http://127.0.0.1:50123/selection",
        )


class ExtensionTests(unittest.TestCase):
    def test_extension_files_and_locales(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profile"
            target = install_extension(
                root, {"port": 50123, "token": "a" * 64}
            )
            self.assertEqual(target, extension_dir(root))
            manifest = json.loads((target / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["manifest_version"], 2)
            self.assertEqual(
                manifest["browser_specific_settings"]["gecko"]["id"],
                "omnify-actions@omnify",
            )
            self.assertIn("contextMenus", manifest["permissions"])
            self.assertIn("http://127.0.0.1/*", manifest["permissions"])
            self.assertNotIn("<all_urls>", json.dumps(manifest))

            script = (target / "background.js").read_text("utf-8")
            self.assertIn("http://127.0.0.1:50123/selection", script)
            self.assertIn("a" * 64, script)
            self.assertIn("browser.contextMenus.create", script)
            self.assertIn("browser.notifications.create", script)

            english = json.loads(
                (target / "_locales" / "en" / "messages.json").read_text("utf-8")
            )
            ukrainian = json.loads(
                (target / "_locales" / "uk" / "messages.json").read_text("utf-8")
            )
            self.assertEqual(
                english["menuNote"]["message"],
                "Move selection to a new note",
            )
            self.assertEqual(
                ukrainian["menuCalc"]["message"],
                "Розв'язати в Калькуляторі",
            )
            for size in (16, 32, 48, 96):
                icon = (target / f"icon-{size}.png").read_bytes()
                self.assertTrue(icon.startswith(b"\x89PNG\r\n\x1a\n"))

            present, port = extension_status(root)
            self.assertTrue(present)
            self.assertEqual(port, DEFAULT_PORT)

    def test_install_without_config_writes_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profile"
            install_extension(root)
            script = (extension_dir(root) / "background.js").read_text("utf-8")
            self.assertIn("/selection", script)
            self.assertTrue(config_path(root).is_file())

    def test_remove_extension_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "profile"
            install_extension(root)
            remove_extension(root)
            remove_extension(root)
            self.assertFalse(extension_dir(root).exists())
            self.assertFalse(extension_status(root)[0])

    def test_background_script_escapes_values(self) -> None:
        script = background_js('http://127.0.0.1:1/"quoted"', 'token"value')
        self.assertIn('\\"quoted\\"', script)
        self.assertIn('token\\"value', script)


class NotePayloadTests(unittest.TestCase):
    def test_empty_selection_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            selection_note("   \n  ")

    def test_page_title_becomes_the_note_title(self) -> None:
        title, contents = selection_note(
            "First line\nsecond line",
            "  Page   title  ",
            "https://example.com/article",
        )
        self.assertEqual(title, "Page title")
        self.assertTrue(contents.startswith("First line\nsecond line"))
        self.assertIn("Source: https://example.com/article", contents)

    def test_first_line_is_a_fallback_title(self) -> None:
        title, _contents = selection_note("\n  A useful quote  \nmore")
        self.assertEqual(title, "A useful quote")

    def test_long_titles_are_capped(self) -> None:
        title, _contents = selection_note("text", "x" * 200)
        self.assertEqual(len(title), 80)

    def test_non_web_sources_are_not_embedded(self) -> None:
        for source in ("javascript:alert(1)", "file:///etc/passwd", "", "not a url"):
            _title, contents = selection_note("safe text", "Page", source)
            self.assertNotIn("Source:", contents)

    def test_source_with_whitespace_is_rejected(self) -> None:
        _title, contents = selection_note(
            "safe text", "Page", "https://example.com/a b"
        )
        self.assertNotIn("Source:", contents)


class ExpressionTests(unittest.TestCase):
    def test_operators_are_normalised(self) -> None:
        self.assertEqual(normalise_expression("2 × 3 ÷ 4"), "2 * 3 / 4")
        self.assertEqual(normalise_expression("5 − 2 – 1"), "5 - 2 - 1")

    def test_equation_keeps_the_left_hand_side(self) -> None:
        self.assertEqual(normalise_expression("2 + 2 = 4"), "2 + 2")
        self.assertEqual(normalise_expression("2 + 2 = 4 = x"), "2 + 2")

    def test_first_non_empty_line_wins(self) -> None:
        self.assertEqual(normalise_expression("\n\n  42 + 1 \nrest"), "42 + 1")

    def test_trailing_punctuation_is_trimmed(self) -> None:
        self.assertEqual(normalise_expression("12 + 3."), "12 + 3")

    def test_empty_selection_is_empty(self) -> None:
        self.assertEqual(normalise_expression("   \n"), "")


class _BridgeHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profile = Path(self._tmp.name) / "profile"
        self.calls: list[tuple[str, str, str, str]] = []
        self.bridge = SelectionBridge(self.profile, self.handler)
        self.addCleanup(self.bridge.stop)

    def handler(self, action: str, text: str, title: str, url: str) -> bool:
        self.calls.append((action, text, title, url))
        return True

    def start(self) -> str:
        endpoint, _token = self.bridge.start()
        return endpoint

    def request(
        self,
        endpoint: str,
        *,
        data: bytes | None = None,
        token: str | None = None,
        method: str = "POST",
        path: str | None = None,
    ):
        url = endpoint if path is None else endpoint.rsplit("/", 1)[0] + path
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Omnify-Token"] = token
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            finally:
                exc.close()

    def post_json(self, endpoint: str, payload, token: str | None = None):
        return self.request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            token=token if token is not None else self.bridge.token,
        )


class BridgeHttpTests(_BridgeHarness):
    def test_successful_note_action(self) -> None:
        endpoint = self.start()
        status, body = self.post_json(
            endpoint,
            {
                "action": "note",
                "text": "Selected text",
                "title": "Example",
                "url": "https://example.com",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertEqual(
            self.calls,
            [("note", "Selected text", "Example", "https://example.com")],
        )

    def test_wrong_token_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.post_json(
            endpoint, {"action": "note", "text": "secret"}, token="wrong"
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.calls, [])

    def test_get_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.request(endpoint, method="GET")
        self.assertEqual(status, 405)

    def test_options_is_allowed(self) -> None:
        endpoint = self.start()
        status, _body = self.request(endpoint, method="OPTIONS")
        self.assertEqual(status, 204)

    def test_unknown_path_returns_404(self) -> None:
        endpoint = self.start()
        status, _body = self.request(
            endpoint,
            data=b"{}",
            token=self.bridge.token,
            path="/other",
        )
        self.assertEqual(status, 404)

    def test_unknown_action_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.post_json(
            endpoint, {"action": "delete", "text": "x"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_empty_selection_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.post_json(endpoint, {"action": "note", "text": " "})
        self.assertEqual(status, 400)

    def test_too_long_selection_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.post_json(
            endpoint, {"action": "note", "text": "x" * (MAX_TEXT + 1)}
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_oversized_body_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.request(
            endpoint,
            data=b"x" * (MAX_BODY + 1),
            token=self.bridge.token,
        )
        self.assertEqual(status, 413)

    def test_invalid_json_is_rejected(self) -> None:
        endpoint = self.start()
        status, _body = self.request(
            endpoint, data=b"{not json", token=self.bridge.token
        )
        self.assertEqual(status, 400)

    def test_handler_failure_returns_503(self) -> None:
        endpoint = self.start()
        self.bridge._handler = lambda *_args: False
        status, _body = self.post_json(
            endpoint, {"action": "note", "text": "x"}
        )
        self.assertEqual(status, 503)

    def test_handler_exception_returns_503(self) -> None:
        endpoint = self.start()

        def explode(*_args):
            raise RuntimeError("boom")

        self.bridge._handler = explode
        status, _body = self.post_json(
            endpoint, {"action": "note", "text": "x"}
        )
        self.assertEqual(status, 503)


class BridgeLifecycleTests(_BridgeHarness):
    def test_start_is_idempotent(self) -> None:
        endpoint = self.start()
        self.assertEqual(self.bridge.start()[0], endpoint)
        self.assertTrue(self.bridge.running)

    def test_stop_is_idempotent_and_restart_works(self) -> None:
        endpoint = self.start()
        port = self.bridge.port
        self.bridge.stop()
        self.assertFalse(self.bridge.running)
        self.bridge.stop()
        restarted, _token = self.bridge.start()
        self.assertEqual(restarted, endpoint)
        self.assertEqual(self.bridge.port, port)

    def test_busy_port_falls_back_and_updates_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            config = ensure_config(profile)
            blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            busy_port = blocker.getsockname()[1]
            try:
                config_path(profile).write_text(
                    json.dumps({"port": busy_port, "token": config["token"]}),
                    encoding="utf-8",
                )
                bridge = SelectionBridge(profile, lambda *_args: True)
                try:
                    endpoint, token = bridge.start()
                finally:
                    bridge.stop()
            finally:
                blocker.close()
            self.assertNotEqual(bridge.port, busy_port)
            self.assertEqual(token, config["token"])
            self.assertIn(str(bridge.port), endpoint)
            saved = json.loads(config_path(profile).read_text("utf-8"))
            self.assertEqual(saved["port"], bridge.port)
            self.assertEqual(saved["token"], config["token"])


class _FakeNotes:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.saved: list[tuple[str | None, str]] = []

    def capture_text(self, contents: str, title: str | None = None) -> str:
        if self.fail:
            raise RuntimeError("notes failed")
        self.saved.append((title, contents))
        return "note-1"


class _FakeCalculator:
    def __init__(self, works: bool = True) -> None:
        self.works = works
        self.expressions: list[str] = []

    def solve(self, expression: str) -> bool:
        self.expressions.append(expression)
        return self.works


class _FakeContext:
    def __init__(self, modules) -> None:
        self.modules = modules
        self.notifications: list[str] = []
        self.opened: list[str] = []
        self.shown = 0

    def get_module(self, module_id: str):
        return self.modules.get(module_id)

    def notify(self, message: str) -> None:
        self.notifications.append(message)

    def open_module(self, module_id: str) -> None:
        self.opened.append(module_id)

    def show_window(self) -> None:
        self.shown += 1


class SelectionActionTests(unittest.TestCase):
    def make_module(self, modules):
        module = BrowserModule.__new__(BrowserModule)
        module.ctx = _FakeContext(modules)
        return module

    def test_note_action_saves_without_stealing_focus(self) -> None:
        notes = _FakeNotes()
        module = self.make_module({"notes": notes})
        result = module._run_selection_action(
            "note",
            "Useful quote",
            "Example page",
            "https://example.com/article",
        )
        self.assertFalse(result)
        self.assertEqual(len(notes.saved), 1)
        title, contents = notes.saved[0]
        self.assertEqual(title, "Example page")
        self.assertIn("Useful quote", contents)
        self.assertIn("Source: https://example.com/article", contents)
        self.assertIn("Selection saved to a new note", module.ctx.notifications)
        self.assertEqual(module.ctx.opened, [])
        self.assertEqual(module.ctx.shown, 0)

    def test_missing_notes_module_is_reported(self) -> None:
        module = self.make_module({})
        module._run_selection_action("note", "text", "", "")
        self.assertIn("Notes is not available", module.ctx.notifications)

    def test_notes_failure_is_contained(self) -> None:
        module = self.make_module({"notes": _FakeNotes(fail=True)})
        module._run_selection_action("note", "text", "", "")
        self.assertIn(
            "Could not handle the browser selection",
            module.ctx.notifications,
        )

    def test_calculator_action_normalises_and_opens(self) -> None:
        calculator = _FakeCalculator()
        module = self.make_module({"calculator": calculator})
        module._run_selection_action(
            "calculator", "2 × 3 = 6", "", ""
        )
        self.assertEqual(calculator.expressions, ["2 * 3"])
        self.assertEqual(module.ctx.opened, ["calculator"])
        self.assertEqual(module.ctx.shown, 1)
        self.assertEqual(module.ctx.notifications, [])

    def test_calculator_error_is_reported(self) -> None:
        calculator = _FakeCalculator(works=False)
        module = self.make_module({"calculator": calculator})
        module._run_selection_action("calculator", "2 + )", "", "")
        self.assertIn(
            "Could not solve the selected text",
            module.ctx.notifications,
        )
        self.assertEqual(module.ctx.opened, [])

    def test_empty_expression_does_not_open_calculator(self) -> None:
        calculator = _FakeCalculator()
        module = self.make_module({"calculator": calculator})
        module._run_selection_action("calculator", "   ", "", "")
        self.assertEqual(calculator.expressions, [])
        self.assertEqual(module.ctx.opened, [])


class ProfileIntegrationTests(unittest.TestCase):
    def test_profile_apply_installs_and_removes_actions_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = BrowserProfile(Path(tmp) / "profile")
            profile.apply(BrowserConfig())
            self.assertTrue((extension_dir(profile.root) / "manifest.json").is_file())
            self.assertTrue(profile.load_config().protections.selection_actions)

            disabled = BrowserConfig(
                protections=Protections(selection_actions=False)
            )
            profile.apply(disabled)
            self.assertFalse(extension_dir(profile.root).exists())
            self.assertFalse(
                BrowserProfile(profile.root).load_config().protections.selection_actions
            )

    def test_config_roundtrip_keeps_selection_actions(self) -> None:
        config = BrowserConfig(
            protections=Protections(selection_actions=False)
        )
        restored = BrowserConfig.from_dict(config.to_dict())
        self.assertFalse(restored.protections.selection_actions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
