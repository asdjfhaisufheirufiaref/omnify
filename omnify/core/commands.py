"""Application-wide commands and editable keyboard shortcuts.

Every action that can be triggered outside its own widget is registered as a
:class:`Command`.  A stable command id (``notes.save``, ``timer.start`` …)
decouples the action from the menu entry, keyboard shortcut, command palette,
system tray or plugin that invokes it.

The module is deliberately GTK-free: key combinations are plain strings
(``"ctrl+shift+p"``) and the shell translates GTK key events into the same
representation.  This keeps shortcut parsing, persistence and conflict
detection unit-testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .logfile import log

#: Preferred modifier order for stable, comparable combo strings.
MODIFIER_ORDER: Tuple[str, ...] = ("ctrl", "shift", "alt", "super")

_MODIFIER_ALIASES: Dict[str, str] = {
    "control": "ctrl",
    "ctl": "ctrl",
    "cmd": "super",
    "command": "super",
    "meta": "super",
    "win": "super",
    "windows": "super",
}

#: Human-friendly aliases accepted from settings and GTK keyval names.
_KEY_ALIASES: Dict[str, str] = {
    "esc": "escape",
    "del": "delete",
    "ins": "insert",
    "enter": "return",
    "spacebar": "space",
    "pgup": "page_up",
    "pgdn": "page_down",
    "pageup": "page_up",
    "pagedown": "page_down",
    "leftbracket": "bracket_left",
    "rightbracket": "bracket_right",
    "comma": "comma",
    "period": "period",
    "slash": "slash",
    "minus": "minus",
    "equal": "equal",
    "plus": "plus",
    "grave": "grave",
    "backslash": "backslash",
    "semicolon": "semicolon",
    "apostrophe": "apostrophe",
    "kp_enter": "return",
    "kp_add": "plus",
    "kp_subtract": "minus",
}

#: Display names for keys whose GTK name is not obvious.
_DISPLAY_KEYS: Dict[str, str] = {
    "return": "Enter",
    "escape": "Esc",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "tab": "Tab",
    "space": "Space",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "minus": "−",
    "equal": "=",
    "plus": "+",
    "grave": "`",
    "backslash": "\\",
    "semicolon": ";",
    "apostrophe": "'",
    "bracket_left": "[",
    "bracket_right": "]",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "home": "Home",
    "end": "End",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
}


def normalize_key_name(key: str) -> str:
    """Normalize one GTK key name to the spelling used in settings."""
    value = str(key or "").strip().lower()
    if not value:
        return ""
    value = value.replace("key_", "").replace("-", "_")
    return _KEY_ALIASES.get(value, value)


def normalize_combo(combo: str) -> str:
    """Return a canonical ``ctrl+shift+p`` string, or ``""`` if empty.

    Modifier order is always ctrl, shift, alt, super.  The function is
    intentionally forgiving: ``"Shift+Control+P"`` and ``"ctrl+shift+p"``
    produce the same result.
    """
    if not isinstance(combo, str):
        return ""
    raw = combo.strip().lower().replace(" ", "")
    if not raw:
        return ""
    # A literal plus key arrives as ``ctrl++``; splitting naively would
    # throw away the key itself.
    if raw.endswith("++"):
        raw = raw[:-1] + "plus"
    parts = [part for part in raw.split("+") if part]
    if not parts:
        return ""
    modifiers = set()
    key = ""
    for part in parts:
        normalized = _MODIFIER_ALIASES.get(part, part)
        if normalized in MODIFIER_ORDER:
            modifiers.add(normalized)
        else:
            key = normalize_key_name(part)
    if not key:
        return ""
    ordered = [modifier for modifier in MODIFIER_ORDER if modifier in modifiers]
    return "+".join(ordered + [key])


def normalize_keys(keys: Iterable[str]) -> Tuple[str, ...]:
    """Normalize, deduplicate and retain the order of *keys*."""
    result: List[str] = []
    for raw in keys or ():
        combo = normalize_combo(raw)
        if combo and combo not in result:
            result.append(combo)
    return tuple(result)


def format_combo(combo: str) -> str:
    """Return a human-readable shortcut such as ``Ctrl+Shift+P``."""
    normalized = normalize_combo(combo)
    if not normalized:
        return ""
    labels = {
        "ctrl": "Ctrl",
        "shift": "Shift",
        "alt": "Alt",
        "super": "Super",
    }
    parts = normalized.split("+")
    out: List[str] = []
    for part in parts[:-1]:
        out.append(labels.get(part, part.title()))
    key = parts[-1]
    if key in _DISPLAY_KEYS:
        out.append(_DISPLAY_KEYS[key])
    elif len(key) == 1:
        out.append(key.upper())
    elif key.startswith("f") and key[1:].isdigit():
        out.append(key.upper())
    else:
        out.append(key.replace("_", " ").title())
    return "+".join(out)


def format_keys(keys: Iterable[str]) -> str:
    """Format several bindings as ``Ctrl+S · Ctrl+Enter``."""
    return " · ".join(filter(None, (format_combo(key) for key in keys)))


@dataclass
class Command:
    """One invokable action.

    ``scope`` is either ``"app"`` (always active) or a module id.  Commands
    whose scope is a module only fire while that module is visible, unless
    the user marks them as global in Settings.

    ``when`` is an optional predicate used for context-sensitive commands
    (for example, "only while the Notes editor has focus").  A command is
    not executed when the predicate returns ``False``.
    """

    id: str
    title: str
    callback: Callable[[], Any]
    category: str = "General"
    description: str = ""
    default_keys: Tuple[str, ...] = ()
    scope: str = "app"
    when: Optional[Callable[[], bool]] = None
    global_default: bool = False

    def __post_init__(self) -> None:
        if not self.id or any(char.isspace() for char in self.id):
            raise ValueError("A command needs a stable, whitespace-free id")
        self.title = str(self.title or self.id)
        self.category = str(self.category or "General")
        self.default_keys = normalize_keys(self.default_keys)

    def available(self) -> bool:
        """Return whether the command's context predicate currently allows it."""
        if self.when is None:
            return True
        try:
            return bool(self.when())
        except Exception as exc:
            log(f"command {self.id}: when() failed: {exc!r}")
            return False


class CommandRegistry:
    """A stable registry of commands contributed by the app, modules and plugins."""

    def __init__(self) -> None:
        self._commands: Dict[str, Command] = {}

    def register(self, command: Command) -> Command:
        if command.id in self._commands:
            raise ValueError(f"Duplicate command id: {command.id}")
        self._commands[command.id] = command
        return command

    def register_many(self, commands: Iterable[Command]) -> None:
        for command in commands:
            self.register(command)

    def unregister(self, command_id: str) -> bool:
        return self._commands.pop(command_id, None) is not None

    def get(self, command_id: str) -> Optional[Command]:
        return self._commands.get(command_id)

    def all(self) -> List[Command]:
        """Commands grouped by category and title for menus/palettes."""
        return sorted(
            self._commands.values(),
            key=lambda command: (command.category.casefold(), command.title.casefold()),
        )

    def by_category(self) -> Dict[str, List[Command]]:
        grouped: Dict[str, List[Command]] = {}
        for command in self.all():
            grouped.setdefault(command.category, []).append(command)
        return grouped

    def execute(self, command_id: str) -> bool:
        """Run a command; return ``False`` for unknown/disabled/error results.

        A callback returning exactly ``False`` is treated as "not handled";
        widgets such as the Notes image-paste handler use this to let GTK
        perform the normal editor behaviour.
        """
        command = self._commands.get(command_id)
        if command is None:
            log(f"command: unknown id {command_id!r}")
            return False
        if not command.available():
            return False
        try:
            result = command.callback()
        except Exception as exc:
            log(f"command {command_id} failed: {exc!r}")
            return False
        return result is not False


@dataclass(frozen=True)
class ShortcutBinding:
    """The current key configuration for one command."""

    command: Command
    keys: Tuple[str, ...]
    global_enabled: bool = False


class ShortcutManager:
    """Persisted, editable bindings layered on top of command defaults.

    Only user overrides are stored.  New commands therefore get their
    defaults automatically, while an explicit empty list still means
    "this command has no shortcut".
    """

    KEY = "shortcuts"

    def __init__(self, storage, registry: CommandRegistry) -> None:
        self._storage = storage
        self._registry = registry
        data = storage.load(self.KEY, {}) if storage is not None else {}
        if not isinstance(data, dict):
            data = {}
        raw = data.get("commands", {})
        self._overrides: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw, dict):
            for command_id, value in raw.items():
                if not isinstance(command_id, str) or not isinstance(value, dict):
                    continue
                keys = normalize_keys(value.get("keys") or ())
                self._overrides[command_id] = {
                    "keys": list(keys),
                    "global": bool(value.get("global", False)),
                }

    # -- reads ---------------------------------------------------------
    def binding(self, command_id: str) -> Optional[ShortcutBinding]:
        command = self._registry.get(command_id)
        if command is None:
            return None
        override = self._overrides.get(command_id)
        if override is None:
            return ShortcutBinding(
                command=command,
                keys=command.default_keys,
                global_enabled=bool(command.global_default),
            )
        return ShortcutBinding(
            command=command,
            keys=normalize_keys(override.get("keys") or ()),
            global_enabled=bool(override.get("global", command.global_default)),
        )

    def keys_for(self, command_id: str) -> Tuple[str, ...]:
        binding = self.binding(command_id)
        return binding.keys if binding is not None else ()

    def is_global(self, command_id: str) -> bool:
        binding = self.binding(command_id)
        return binding.global_enabled if binding is not None else False

    def bindings(self) -> List[ShortcutBinding]:
        result = []
        for command in self._registry.all():
            binding = self.binding(command.id)
            if binding is not None:
                result.append(binding)
        return result

    def bound_bindings(self) -> List[ShortcutBinding]:
        return [binding for binding in self.bindings() if binding.keys]

    def find_bindings(
        self,
        combo: str,
        scope: Optional[str] = None,
        *,
        global_only: bool = False,
        available_only: bool = True,
    ) -> List[ShortcutBinding]:
        """Return commands bound to *combo* in the given context.

        App-scoped commands are always candidates; module-scoped commands
        only when their module matches *scope*.  ``global_only`` is used by
        the background key monitor.
        """
        normalized = normalize_combo(combo)
        if not normalized:
            return []
        matches: List[ShortcutBinding] = []
        for binding in self.bindings():
            if normalized not in binding.keys:
                continue
            if global_only and not binding.global_enabled:
                continue
            command = binding.command
            if scope is not None and command.scope not in ("app", scope):
                continue
            if available_only and not command.available():
                continue
            matches.append(binding)
        # App-wide commands win over module-local ones when both somehow
        # share a combination (the UI normally prevents this).
        matches.sort(key=lambda item: (item.command.scope != "app", item.command.title))
        return matches

    # -- mutations -----------------------------------------------------
    def set_keys(self, command_id: str, keys: Iterable[str]) -> None:
        command = self._registry.get(command_id)
        if command is None:
            raise KeyError(command_id)
        normalized = normalize_keys(keys)
        self._set_override(
            command_id,
            keys=normalized,
            global_enabled=self.is_global(command_id),
        )

    def set_global(self, command_id: str, enabled: bool) -> None:
        command = self._registry.get(command_id)
        if command is None:
            raise KeyError(command_id)
        self._set_override(
            command_id,
            keys=self.keys_for(command_id),
            global_enabled=bool(enabled),
        )

    def reset(self, command_id: str) -> None:
        self._overrides.pop(command_id, None)
        self._save()

    def reset_all(self) -> None:
        self._overrides.clear()
        self._save()

    def _set_override(
        self,
        command_id: str,
        *,
        keys: Sequence[str],
        global_enabled: bool,
    ) -> None:
        command = self._registry.get(command_id)
        assert command is not None
        same_keys = tuple(keys) == tuple(command.default_keys)
        same_global = bool(global_enabled) == bool(command.global_default)
        if same_keys and same_global:
            self._overrides.pop(command_id, None)
        else:
            self._overrides[command_id] = {
                "keys": list(keys),
                "global": bool(global_enabled),
            }
        self._save()

    def _save(self) -> None:
        if self._storage is None:
            return
        self._storage.save(self.KEY, {"version": 1, "commands": self._overrides})

    # -- conflict detection --------------------------------------------
    @staticmethod
    def scopes_overlap(first: str, second: str) -> bool:
        return first == "app" or second == "app" or first == second

    def conflicts(
        self,
        combo: str,
        *,
        exclude_command_id: Optional[str] = None,
        scope: Optional[str] = None,
        global_enabled: bool = False,
    ) -> List[ShortcutBinding]:
        """Find commands already using *combo* in an overlapping context."""
        normalized = normalize_combo(combo)
        if not normalized:
            return []
        result: List[ShortcutBinding] = []
        for binding in self.bound_bindings():
            if binding.command.id == exclude_command_id:
                continue
            if normalized not in binding.keys:
                continue
            command_scope = binding.command.scope
            effective_scope = scope if scope is not None else command_scope
            scopes_collide = self.scopes_overlap(command_scope, effective_scope)
            # A global binding is active even when another module is shown,
            # so it collides with every other binding for the same key.
            if global_enabled or binding.global_enabled:
                scopes_collide = True
            if scopes_collide:
                result.append(binding)
        return result

    def conflicts_for_binding(
        self, binding: ShortcutBinding
    ) -> List[ShortcutBinding]:
        """All conflicts across every key of *binding*."""
        found: List[ShortcutBinding] = []
        for combo in binding.keys:
            for conflict in self.conflicts(
                combo,
                exclude_command_id=binding.command.id,
                scope=binding.command.scope,
                global_enabled=binding.global_enabled,
            ):
                if conflict not in found:
                    found.append(conflict)
        return found

    def has_conflicts(self) -> bool:
        return any(self.conflicts_for_binding(binding) for binding in self.bound_bindings())
