"""Obsidian-style note templates with safe, dependency-free variables.

Templates are ordinary Markdown with ``{{variable}}`` placeholders.  The
engine is deliberately pure Python so the same renderer can be used by the
Notes module, quick capture, plugins and the command line:

* ``{{date}}``, ``{{time}}``, ``{{datetime}}``
* ``{{date:YYYY-MM-DD}}`` and other Moment-style date patterns
* ``{{yesterday}}``, ``{{tomorrow}}``, ``{{weekday}}``
* ``{{title}}``, ``{{folder}}``, ``{{note_id}}``
* ``{{uuid}}``, ``{{random}}``, ``{{clipboard}}``, ``{{selection}}``
* ``{{prompt:Question}}`` — asks the caller's prompt callback
* ``{{cursor}}`` — marks where the text cursor should land

Unknown variables are left intact so a typo remains visible instead of
silently disappearing.
"""

from __future__ import annotations

import datetime as _dt
import random as _random
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

_VARIABLE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


@dataclass(frozen=True)
class TemplateRecord:
    """A user-created template persisted in storage."""

    id: str
    name: str
    body: str


@dataclass(frozen=True)
class RenderedTemplate:
    """Rendered text plus the optional cursor position."""

    text: str
    cursor: Optional[int] = None


def _moment_format(pattern: str, moment: _dt.datetime) -> str:
    """Convert common Obsidian/Moment date tokens to ``strftime``."""
    tokens = (
        ("YYYY", "%Y"),
        ("YY", "%y"),
        ("MMMM", "%B"),
        ("MMM", "%b"),
        ("MM", "%m"),
        ("DD", "%d"),
        ("dddd", "%A"),
        ("ddd", "%a"),
        ("HH", "%H"),
        ("hh", "%I"),
        ("mm", "%M"),
        ("ss", "%S"),
    )
    result = pattern
    for token, replacement in tokens:
        result = result.replace(token, replacement)

    # Protect percent sequences created above (``%A`` contains ``A``)
    # before translating the single-letter Moment tokens.
    protected: Dict[str, str] = {}

    def protect(match: re.Match[str]) -> str:
        key = f"\u0000{len(protected)}\u0000"
        protected[key] = match.group(0)
        return key

    result = re.sub(r"%[A-Za-z%]", protect, result)
    result = result.replace("A", "\u0000PCTP\u0000")
    result = result.replace("Z", "%z").replace("X", "%s")
    for key, value in protected.items():
        result = result.replace(key, value)
    result = result.replace("\u0000PCTP\u0000", "%p")
    return moment.strftime(result)


class TemplateEngine:
    """Render ``{{variables}}`` in a template string."""

    def __init__(
        self,
        *,
        now: Optional[Callable[[], _dt.datetime]] = None,
        clipboard: Optional[Callable[[], str]] = None,
        selection: Optional[Callable[[], str]] = None,
        prompt: Optional[Callable[[str], str]] = None,
        random_digits: Optional[Callable[[], str]] = None,
    ) -> None:
        self._now = now or _dt.datetime.now
        self._clipboard = clipboard
        self._selection = selection
        self._prompt = prompt
        self._random_digits = random_digits or (
            lambda: f"{_random.randint(0, 999999):06d}"
        )

    def render(
        self,
        template: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> RenderedTemplate:
        moment = self._now()
        values: Dict[str, Any] = {
            "date": moment.strftime("%Y-%m-%d"),
            "time": moment.strftime("%H:%M"),
            "datetime": moment.strftime("%Y-%m-%d %H:%M"),
            "yesterday": (moment - _dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            "tomorrow": (moment + _dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            "weekday": moment.strftime("%A"),
            "uuid": uuid.uuid4().hex,
            "random": self._random_digits(),
            "clipboard": self._clipboard() if self._clipboard else "",
            "selection": self._selection() if self._selection else "",
        }
        if context:
            values.update(context)

        pieces: List[str] = []
        cursor: Optional[int] = None
        position = 0
        for match in _VARIABLE_RE.finditer(template):
            pieces.append(template[position:match.start()])
            expression = match.group(1).strip()
            name, _separator, argument = expression.partition(":")
            name = name.strip().lower()
            argument = argument.strip()
            if name == "cursor":
                if cursor is None:
                    cursor = sum(len(piece) for piece in pieces)
            elif name == "prompt":
                if self._prompt is not None:
                    try:
                        pieces.append(str(self._prompt(argument or "Value") or ""))
                    except Exception:
                        pieces.append("")
            elif name in ("date", "time", "datetime") and argument:
                pieces.append(_moment_format(argument, moment))
            elif name in values:
                value = values[name]
                pieces.append(str(value if value is not None else ""))
            else:
                pieces.append(match.group(0))
            position = match.end()
        pieces.append(template[position:])
        return RenderedTemplate("".join(pieces), cursor)


class TemplateStore:
    """Persist user templates in the shared JSON storage."""

    KEY = "note_templates"
    VERSION = 1

    def __init__(self, storage) -> None:
        self._storage = storage
        data = storage.load(self.KEY, {}) if storage is not None else {}
        if not isinstance(data, dict):
            data = {}
        raw = data.get("templates", [])
        self._templates: List[TemplateRecord] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                raw_name = item.get("name")
                if not isinstance(raw_name, str):
                    continue
                name = raw_name.strip()
                body = str(item.get("body") or "")
                template_id = str(item.get("id") or uuid.uuid4().hex)
                if name:
                    self._templates.append(
                        TemplateRecord(template_id, name, body)
                    )

    def all(self) -> List[TemplateRecord]:
        return list(self._templates)

    def get(self, template_id: str) -> Optional[TemplateRecord]:
        for template in self._templates:
            if template.id == template_id:
                return template
        return None

    def save(
        self,
        name: str,
        body: str,
        template_id: Optional[str] = None,
    ) -> TemplateRecord:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Template name must not be empty")
        clean_body = str(body or "")
        if template_id:
            for index, template in enumerate(self._templates):
                if template.id == template_id:
                    updated = TemplateRecord(template.id, clean_name, clean_body)
                    self._templates[index] = updated
                    self._save()
                    return updated
        record = TemplateRecord(uuid.uuid4().hex, clean_name, clean_body)
        self._templates.append(record)
        self._save()
        return record

    def delete(self, template_id: str) -> bool:
        before = len(self._templates)
        self._templates = [
            template for template in self._templates if template.id != template_id
        ]
        if len(self._templates) != before:
            self._save()
            return True
        return False

    def _save(self) -> None:
        if self._storage is None:
            return
        self._storage.save(
            self.KEY,
            {
                "version": self.VERSION,
                "templates": [
                    {"id": template.id, "name": template.name, "body": template.body}
                    for template in self._templates
                ],
            },
        )
