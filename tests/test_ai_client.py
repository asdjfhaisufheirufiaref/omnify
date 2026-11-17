"""Headless tests for the dependency-free AI client and its JSON helpers."""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.plugins.ai_assistant.client import (  # noqa: E402
    AIError,
    AIConfig,
    AIClient,
    is_local_endpoint,
    parse_json_object,
    provider_defaults,
    sanitize_base_url,
)


class _Response:
    def __init__(self, payload=None, raw: bytes = b"") -> None:
        self._raw = raw or json.dumps(payload or {}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return False

    def read(self) -> bytes:
        return self._raw


class AIConfigTests(unittest.TestCase):
    def test_local_defaults_need_no_key(self) -> None:
        config = AIConfig.from_settings({})
        self.assertEqual(config.provider, "local")
        self.assertTrue(config.local)
        self.assertFalse(config.requires_key)
        config.validate()
        self.assertEqual(
            config.chat_endpoint(),
            "http://localhost:11434/v1/chat/completions",
        )

    def test_cloud_provider_requires_key(self) -> None:
        config = AIConfig.from_settings(
            {"provider": "openai", "api_key": ""}, environ={}
        )
        self.assertFalse(config.local)
        with self.assertRaises(AIError):
            config.validate()

    def test_env_key_is_used_when_setting_is_empty(self) -> None:
        config = AIConfig.from_settings(
            {"provider": "openai", "api_key": ""},
            environ={"OPENAI_API_KEY": "env-secret"},
        )
        self.assertEqual(config.api_key, "env-secret")
        config.validate()

    def test_custom_provider_defaults_are_exposed(self) -> None:
        defaults = provider_defaults("openrouter")
        self.assertIn("openrouter", defaults["base_url"])
        self.assertEqual(defaults["model"], "openai/gpt-4o-mini")

    def test_base_url_scheme_is_validated(self) -> None:
        self.assertEqual(
            sanitize_base_url("https://example.test/v1/"),
            "https://example.test/v1",
        )
        with self.assertRaises(AIError):
            sanitize_base_url("file:///etc/passwd")
        with self.assertRaises(AIError):
            sanitize_base_url("not-a-url")

    def test_local_detection_covers_loopback_names(self) -> None:
        self.assertTrue(is_local_endpoint("http://localhost:1234/v1"))
        self.assertTrue(is_local_endpoint("http://127.0.0.1:11434/v1"))
        self.assertTrue(is_local_endpoint("http://[::1]:8080/v1"))
        self.assertTrue(is_local_endpoint("http://ollama.localhost/v1"))
        self.assertFalse(is_local_endpoint("https://api.openai.com/v1"))

    def test_chat_endpoint_is_not_doubled(self) -> None:
        config = AIConfig(
            provider="custom",
            base_url="https://example.test/v1/chat/completions",
            model="m",
            api_key="k",
        )
        self.assertEqual(
            config.chat_endpoint(),
            "https://example.test/v1/chat/completions",
        )

    def test_redacted_config_never_contains_the_key(self) -> None:
        config = AIConfig(
            provider="custom",
            base_url="https://example.test/v1",
            model="m",
            api_key="super-secret",
        )
        self.assertNotIn("super-secret", json.dumps(config.redacted()))


class AIClientTests(unittest.TestCase):
    def test_complete_parses_openai_response(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["auth"] = request.get_header("Authorization")
            return _Response(
                {"choices": [{"message": {"content": "Hello from the model"}}]}
            )

        client = AIClient(opener=opener)
        config = AIConfig(
            provider="custom",
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )
        answer = client.complete(
            config, [{"role": "user", "content": "Hi"}], timeout=12
        )
        self.assertEqual(answer, "Hello from the model")
        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["timeout"], 12)
        self.assertEqual(captured["body"]["model"], "test-model")
        self.assertEqual(captured["auth"], "Bearer secret")

    def test_complete_parses_ollama_style_response(self) -> None:
        client = AIClient(
            opener=lambda _request, timeout: _Response(
                {"message": {"role": "assistant", "content": "local answer"}}
            )
        )
        config = AIConfig(provider="local")
        self.assertEqual(
            client.complete(config, [{"role": "user", "content": "Hi"}]),
            "local answer",
        )

    def test_empty_answer_is_an_error(self) -> None:
        client = AIClient(
            opener=lambda _request, timeout: _Response({"choices": []})
        )
        with self.assertRaises(AIError):
            client.complete(AIConfig(), [{"role": "user", "content": "Hi"}])

    def test_http_error_is_reported(self) -> None:
        def opener(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                None,
            )

        client = AIClient(opener=opener)
        with self.assertRaises(AIError) as caught:
            client.complete(
                AIConfig(
                    provider="custom",
                    base_url="https://example.test/v1",
                    model="m",
                    api_key="bad",
                ),
                [{"role": "user", "content": "Hi"}],
            )
        self.assertIn("401", str(caught.exception))

    def test_invalid_json_is_reported(self) -> None:
        client = AIClient(
            opener=lambda _request, timeout: _Response(raw=b"not json")
        )
        with self.assertRaises(AIError):
            client.complete(AIConfig(), [{"role": "user", "content": "Hi"}])


class JSONParsingTests(unittest.TestCase):
    def test_plain_object(self) -> None:
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})

    def test_fenced_object_with_prose(self) -> None:
        answer = 'Here is the event:\n```json\n{"title": "Dentist"}\n```\nThanks'
        self.assertEqual(parse_json_object(answer), {"title": "Dentist"})

    def test_object_embedded_in_prose(self) -> None:
        answer = 'Sure: {"title": "Lunch", "time": "12:00"} done.'
        self.assertEqual(
            parse_json_object(answer),
            {"title": "Lunch", "time": "12:00"},
        )

    def test_invalid_answer_raises(self) -> None:
        with self.assertRaises(AIError):
            parse_json_object("no json here")


class BuiltinAIPluginTests(unittest.TestCase):
    def test_ai_plugin_is_discovered_disabled_and_loadable(self) -> None:
        import tempfile

        from omnify.core.commands import CommandRegistry
        from omnify.core.plugins import PluginManager
        from omnify.core.storage import Storage

        root = Path(__file__).resolve().parents[1] / "omnify" / "plugins"
        with tempfile.TemporaryDirectory() as directory:
            registry = CommandRegistry()
            manager = PluginManager(
                Storage(directory), registry, builtin_dir=root
            )
            manager.scan()
            record = manager.get("ai_assistant")
            self.assertIsNotNone(record)
            self.assertTrue(record.manifest.builtin)
            self.assertFalse(record.enabled)
            self.assertFalse(record.loaded)
            self.assertTrue(manager.enable("ai_assistant"))
            self.assertTrue(record.loaded)
            for command_id in (
                "ai_assistant.ask",
                "ai_assistant.ask_note",
                "ai_assistant.summarize_note",
                "ai_assistant.calendar_event",
                "ai_assistant.research",
            ):
                self.assertIsNotNone(
                    registry.get(command_id), f"missing {command_id}"
                )
            self.assertEqual(
                [title for _manifest, title, _builder in manager.settings_panels()],
                ["AI Assistant"],
            )
            manager.disable("ai_assistant")
            self.assertIsNone(registry.get("ai_assistant.ask"))


if __name__ == "__main__":
    unittest.main()
