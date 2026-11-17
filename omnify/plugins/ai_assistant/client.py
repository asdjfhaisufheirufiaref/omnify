"""Small, dependency-free OpenAI-compatible chat client.

The AI plugin deliberately supports the widest common denominator:

* **Local** servers that expose an OpenAI-compatible ``/v1`` API (Ollama,
  LM Studio, llama.cpp, LocalAI, vLLM, …).
* **OpenAI** and **OpenRouter** over HTTPS.
* **Custom** OpenAI-compatible gateways with their own base URL and key.

Only the Python standard library is used, so the plugin keeps working even
when no ``requests``/``httpx`` package is installed.  Nothing in this module
imports GTK and no request is made until :meth:`AIClient.complete` is called.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class AIError(RuntimeError):
    """A user-facing AI failure (missing key, bad URL, HTTP error, …)."""


#: Provider presets shown in Settings.  ``local`` is the default because it
#: keeps private notes on the user's machine.
PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "local": {
        "label": "Local model (Ollama / LM Studio)",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "key_env": "",
        "help": "Run a local OpenAI-compatible server. No note leaves this computer.",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
        "help": "Cloud provider. Prompts and note text are sent to OpenAI.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "key_env": "OPENROUTER_API_KEY",
        "help": "Cloud gateway. Prompts and note text are sent to OpenRouter.",
    },
    "custom": {
        "label": "Custom OpenAI-compatible API",
        "base_url": "",
        "model": "",
        "key_env": "OMNIFY_AI_API_KEY",
        "help": "Any OpenAI-compatible endpoint you control or trust.",
    },
}

PROVIDER_ORDER: Tuple[str, ...] = ("local", "openai", "openrouter", "custom")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def provider_defaults(provider: str) -> Dict[str, str]:
    return dict(PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"]))


def sanitize_base_url(value: str) -> str:
    """Return a safe ``http(s)`` base URL without a trailing slash.

    ``file:``, ``javascript:`` and similar schemes are rejected so a custom
    endpoint cannot be used to read local files through ``urllib``.
    """
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise AIError("The AI base URL must start with http:// or https://")
    if not parsed.netloc:
        raise AIError("The AI base URL is missing a host")
    return candidate.rstrip("/")


def is_local_endpoint(base_url: str) -> bool:
    """Whether *base_url* stays on this machine (loopback only)."""
    try:
        parsed = urllib.parse.urlparse(str(base_url or "").strip())
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


@dataclass(frozen=True)
class AIConfig:
    """One immutable request configuration."""

    provider: str = "local"
    base_url: str = PROVIDER_PRESETS["local"]["base_url"]
    model: str = PROVIDER_PRESETS["local"]["model"]
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 1200
    timeout: float = 90.0

    @classmethod
    def from_settings(
        cls,
        settings: Mapping[str, Any],
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "AIConfig":
        """Build a config from plugin settings with env-var key fallback."""
        data = dict(settings or {})
        provider = str(data.get("provider") or "local").strip().lower()
        if provider not in PROVIDER_PRESETS:
            provider = "custom"
        defaults = provider_defaults(provider)
        base_url = str(data.get("base_url") or defaults["base_url"]).strip()
        model = str(data.get("model") or defaults["model"]).strip()
        api_key = str(data.get("api_key") or "").strip()
        env = os.environ if environ is None else environ
        if not api_key:
            for name in (
                defaults.get("key_env", ""),
                "OMNIFY_AI_API_KEY",
                "OPENAI_API_KEY" if provider == "openai" else "",
                "OPENROUTER_API_KEY" if provider == "openrouter" else "",
            ):
                if name and env.get(name):
                    api_key = str(env[name]).strip()
                    break
        try:
            temperature = float(data.get("temperature", 0.3))
        except (TypeError, ValueError):
            temperature = 0.3
        try:
            max_tokens = int(data.get("max_tokens", 1200))
        except (TypeError, ValueError):
            max_tokens = 1200
        try:
            timeout = float(data.get("timeout", 90.0))
        except (TypeError, ValueError):
            timeout = 90.0
        return cls(
            provider=provider,
            base_url=sanitize_base_url(base_url),
            model=model,
            api_key=api_key,
            temperature=max(0.0, min(2.0, temperature)),
            max_tokens=max(64, min(32768, max_tokens)),
            timeout=max(5.0, min(600.0, timeout)),
        )

    @property
    def local(self) -> bool:
        return self.provider == "local" and is_local_endpoint(self.base_url)

    @property
    def requires_key(self) -> bool:
        return not self.local

    def validate(self) -> None:
        if not self.base_url:
            raise AIError("Set an AI base URL in Settings → AI Assistant")
        if not self.model:
            raise AIError("Set an AI model name in Settings → AI Assistant")
        if self.requires_key and not self.api_key:
            raise AIError(
                "This AI provider needs an API key. Add one in "
                "Settings → AI Assistant or set OMNIFY_AI_API_KEY."
            )

    def chat_endpoint(self) -> str:
        base = sanitize_base_url(self.base_url)
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def redacted(self) -> Dict[str, Any]:
        """A log-safe representation (never includes the API key)."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


def _message_text(payload: Mapping[str, Any]) -> str:
    """Extract text from common OpenAI/Ollama response shapes."""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, Mapping):
            message = choice.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                            parts.append(item["text"])
                    if parts:
                        return "".join(parts)
            text = choice.get("text")
            if isinstance(text, str):
                return text
    message = payload.get("message")
    if isinstance(message, Mapping) and isinstance(message.get("content"), str):
        return message["content"]
    response = payload.get("response")
    if isinstance(response, str):
        return response
    return ""


class AIClient:
    """Call an OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(self, opener=None) -> None:
        # Injectable for tests; ``None`` means the stdlib default opener.
        self._opener = opener

    def complete(
        self,
        config: AIConfig,
        messages: Sequence[Mapping[str, str]],
        *,
        timeout: Optional[float] = None,
    ) -> str:
        config.validate()
        body: Dict[str, Any] = {
            "model": config.model,
            "messages": [dict(message) for message in messages],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": False,
        }
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Omnify-AI/1.0",
        }
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        request = urllib.request.Request(
            config.chat_endpoint(),
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            if self._opener is None:
                response = urllib.request.urlopen(
                    request, timeout=timeout or config.timeout
                )
            else:
                response = self._opener(request, timeout=timeout or config.timeout)
            with response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:1200]
            except Exception:
                pass
            finally:
                try:
                    exc.close()
                except Exception:
                    pass
            raise AIError(
                f"AI request failed ({exc.code}): {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AIError(f"Could not reach the AI server: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AIError("The AI server did not answer in time") from exc
        except OSError as exc:
            raise AIError(f"Could not reach the AI server: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise AIError("The AI server returned invalid JSON") from exc
        if isinstance(payload, Mapping) and payload.get("error"):
            error = payload["error"]
            if isinstance(error, Mapping):
                error = error.get("message") or error
            raise AIError(f"AI server error: {error}")
        text = _message_text(payload).strip()
        if not text:
            raise AIError("The AI server returned an empty answer")
        return text

    def test(self, config: AIConfig) -> str:
        return self.complete(
            config,
            [
                {"role": "system", "content": "Answer with exactly: OK"},
                {"role": "user", "content": "Connection test"},
            ],
            timeout=min(config.timeout, 30.0),
        )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from an LLM answer.

    Models often wrap JSON in Markdown fences or add a sentence before it;
    this keeps the calendar integration forgiving without evaluating code.
    """
    value = str(text or "").strip()
    fenced = _JSON_FENCE_RE.search(value)
    if fenced:
        value = fenced.group(1).strip()
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except ValueError:
        pass
    start = value.find("{")
    end = value.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(value[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except ValueError:
            pass
    raise AIError("The AI answer did not contain a JSON object")
