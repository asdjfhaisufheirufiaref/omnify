"""Drop-in plugin discovery, trust and lifecycle management.

Omnify follows the "modded Minecraft" model rather than a remote store:
a plugin is a folder with ``plugin.json`` and a Python entry point.  Put it
under ``$XDG_DATA_HOME/omnify/plugins/<id>/`` (the Settings → Plugins card
has an "Open folder" button), press Rescan, and enable it.

Because Python plugins run with the user's full permissions, externally
installed plugins are **not** executed until the user explicitly trusts them.
Built-in plugins are trusted by the application itself, but still start
disabled unless their manifest says otherwise.

The module is GTK-free so discovery, manifests, command registration and
settings persistence can be tested headlessly.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .commands import Command, CommandRegistry, normalize_keys
from .logfile import log

PLUGIN_API_VERSION = 1
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


@dataclass
class PluginManifest:
    """Validated metadata from a plugin folder."""

    id: str
    name: str
    version: str
    description: str
    author: str
    entrypoint: str
    api_version: int
    permissions: Tuple[str, ...] = ()
    default_enabled: bool = False
    root: Path = Path(".")
    builtin: bool = False

    @property
    def entry_path(self) -> Path:
        return self.root / self.entrypoint


@dataclass
class PluginRecord:
    """Runtime state for one discovered plugin."""

    manifest: PluginManifest
    enabled: bool = False
    trusted: bool = False
    loaded: bool = False
    error: str = ""
    module: Any = None
    command_ids: List[str] = field(default_factory=list)
    panel_count: int = 0

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def source_label(self) -> str:
        return "Built-in" if self.manifest.builtin else "External"


def _manifest_from_file(path: Path, *, builtin: bool) -> PluginManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("plugin.json must contain an object")
    plugin_id = str(data.get("id") or "").strip()
    if not _ID_RE.match(plugin_id):
        raise ValueError("plugin id must contain only letters, digits, '.', '_' or '-'")
    api_version = int(data.get("api_version", PLUGIN_API_VERSION))
    if api_version != PLUGIN_API_VERSION:
        raise ValueError(
            f"plugin API version {api_version} is not supported "
            f"(expected {PLUGIN_API_VERSION})"
        )
    entrypoint = str(data.get("entrypoint") or "plugin.py").strip()
    root = path.parent.resolve()
    target = (root / entrypoint).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("entrypoint must stay inside the plugin folder")
    if target.suffix != ".py":
        raise ValueError("entrypoint must be a .py file")
    if not target.is_file():
        raise ValueError(f"entrypoint not found: {entrypoint}")
    permissions = data.get("permissions") or []
    if not isinstance(permissions, list):
        permissions = []
    return PluginManifest(
        id=plugin_id,
        name=str(data.get("name") or plugin_id),
        version=str(data.get("version") or "0.0.0"),
        description=str(data.get("description") or ""),
        author=str(data.get("author") or ""),
        entrypoint=entrypoint,
        api_version=api_version,
        permissions=tuple(str(item) for item in permissions if str(item).strip()),
        default_enabled=bool(data.get("default_enabled", False)),
        root=root,
        builtin=builtin,
    )


class PluginAPI:
    """The stable surface handed to a plugin's ``setup(api)`` function."""

    def __init__(self, manager: "PluginManager", manifest: PluginManifest) -> None:
        self._manager = manager
        self._manifest = manifest

    # -- identity ------------------------------------------------------
    @property
    def id(self) -> str:
        return self._manifest.id

    @property
    def name(self) -> str:
        return self._manifest.name

    @property
    def version(self) -> str:
        return self._manifest.version

    @property
    def permissions(self) -> Tuple[str, ...]:
        return self._manifest.permissions

    # -- commands ------------------------------------------------------
    def register_command(
        self,
        command_id: str,
        title: str,
        callback: Callable[[], Any],
        *,
        category: Optional[str] = None,
        description: str = "",
        default_keys: Sequence[str] = (),
        scope: str = "app",
        when: Optional[Callable[[], bool]] = None,
        global_default: bool = False,
    ) -> str:
        """Register a command; the returned id is namespaced by plugin id."""
        clean = str(command_id or "").strip()
        if not clean:
            raise ValueError("command id must not be empty")
        full_id = clean if clean.startswith(f"{self.id}.") else f"{self.id}.{clean}"
        if not _ID_RE.match(full_id):
            raise ValueError(f"invalid command id: {full_id!r}")

        def guarded():
            try:
                return callback()
            except Exception as exc:
                log(f"plugin {self.id}: command {full_id} failed: {exc!r}")
                self.notify(f"Plugin {self.name}: {exc}")
                return False

        command = Command(
            full_id,
            title,
            guarded,
            category=category or self.name,
            description=description,
            default_keys=tuple(normalize_keys(default_keys)),
            scope=scope,
            when=when,
            global_default=global_default,
        )
        self._manager._register_plugin_command(self.id, command)
        return full_id

    def unregister_command(self, command_id: str) -> None:
        self._manager._unregister_plugin_command(self.id, command_id)

    # -- settings ------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._manager._plugin_settings(self.id).get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._manager._set_plugin_setting(self.id, key, value)

    def settings_dict(self) -> Dict[str, Any]:
        return dict(self._manager._plugin_settings(self.id))

    # -- app services --------------------------------------------------
    def notify(self, message: str) -> None:
        callback = self._manager._notify
        if callback is not None:
            try:
                callback(str(message))
            except Exception:
                pass

    def get_module(self, module_id: str):
        callback = self._manager._get_module
        return callback(module_id) if callback is not None else None

    def open_module(self, module_id: str) -> None:
        callback = self._manager._open_module
        if callback is not None:
            callback(module_id)

    def show_window(self) -> None:
        callback = self._manager._show_window
        if callback is not None:
            callback()

    def run_command(self, command_id: str) -> bool:
        callback = self._manager._run_command
        return bool(callback(command_id)) if callback is not None else False

    def log(self, message: str) -> None:
        log(f"plugin {self.id}: {message}")

    # -- settings panels ----------------------------------------------
    def register_settings_panel(self, title: str, builder: Callable[[], Any]) -> None:
        self._manager._register_plugin_panel(self.id, title, builder)


class PluginManager:
    """Discover, trust, load and unload drop-in plugins."""

    STATE_KEY = "plugins"
    VERSION = 1

    def __init__(
        self,
        storage,
        commands: CommandRegistry,
        *,
        notify: Optional[Callable[[str], None]] = None,
        get_module: Optional[Callable[[str], Any]] = None,
        open_module: Optional[Callable[[str], None]] = None,
        show_window: Optional[Callable[[], None]] = None,
        run_command: Optional[Callable[[str], bool]] = None,
        builtin_dir: Optional[Path] = None,
    ) -> None:
        self._storage = storage
        self._commands = commands
        self._notify = notify
        self._get_module = get_module
        self._open_module = open_module
        self._show_window = show_window
        self._run_command = run_command
        self._builtin_dir = (
            Path(builtin_dir)
            if builtin_dir is not None
            else Path(__file__).resolve().parent.parent / "plugins"
        )
        self._external_dir = storage.path / "plugins" if storage is not None else Path(".")
        self._external_dir.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, PluginRecord] = {}
        self._errors: List[Tuple[str, str]] = []
        self._panels: Dict[str, List[Tuple[str, Callable[[], Any]]]] = {}
        data = storage.load(self.STATE_KEY, {}) if storage is not None else {}
        if not isinstance(data, dict):
            data = {}
        self._enabled: Dict[str, bool] = {
            str(key): bool(value)
            for key, value in (data.get("enabled") or {}).items()
            if isinstance(key, str)
        }
        self._trusted: Dict[str, bool] = {
            str(key): bool(value)
            for key, value in (data.get("trusted") or {}).items()
            if isinstance(key, str)
        }
        raw_settings = data.get("settings") or {}
        self._settings: Dict[str, Dict[str, Any]] = {
            str(key): dict(value)
            for key, value in raw_settings.items()
            if isinstance(key, str) and isinstance(value, dict)
        } if isinstance(raw_settings, dict) else {}

    # -- discovery -----------------------------------------------------
    @property
    def plugins_dir(self) -> Path:
        return self._external_dir

    @property
    def builtin_dir(self) -> Path:
        return self._builtin_dir

    def errors(self) -> List[Tuple[str, str]]:
        return list(self._errors)

    def scan(self) -> List[PluginRecord]:
        """Discover valid plugins, merging with already loaded records."""
        self._errors = []
        seen: Dict[str, Path] = {}
        found: Dict[str, PluginRecord] = {}
        for root, builtin in (
            (self._builtin_dir, True),
            (self._external_dir, False),
        ):
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if not folder.is_dir():
                    continue
                manifest_path = folder / "plugin.json"
                if not manifest_path.is_file():
                    if not builtin:
                        self._errors.append((folder.name, "plugin.json is missing"))
                    continue
                try:
                    manifest = _manifest_from_file(manifest_path, builtin=builtin)
                except Exception as exc:
                    self._errors.append((folder.name, str(exc)))
                    continue
                if manifest.id in seen:
                    self._errors.append(
                        (
                            folder.name,
                            f"duplicate plugin id {manifest.id!r} "
                            f"(already provided by {seen[manifest.id]})",
                        )
                    )
                    continue
                seen[manifest.id] = folder
                previous = self._records.get(manifest.id)
                if previous is not None and previous.manifest.root == manifest.root:
                    previous.manifest = manifest
                    found[manifest.id] = previous
                    continue
                record = PluginRecord(manifest=manifest)
                if builtin:
                    record.trusted = True
                    record.enabled = bool(
                        self._enabled.get(manifest.id, manifest.default_enabled)
                    )
                else:
                    record.trusted = bool(self._trusted.get(manifest.id, False))
                    record.enabled = bool(self._enabled.get(manifest.id, False))
                found[manifest.id] = record
        # Unload plugins that disappeared or moved to a different folder;
        # otherwise their commands and panels would outlive the folder.
        for plugin_id, previous in list(self._records.items()):
            replacement = found.get(plugin_id)
            if previous.loaded and replacement is not previous:
                self._unload_record(previous)
        self._records = found
        return list(self._records.values())

    def records(self) -> List[PluginRecord]:
        return sorted(self._records.values(), key=lambda record: record.manifest.name.casefold())

    def get(self, plugin_id: str) -> Optional[PluginRecord]:
        return self._records.get(plugin_id)

    # -- loading -------------------------------------------------------
    def load_all(self) -> None:
        self.scan()
        for record in list(self._records.values()):
            if record.enabled and (record.trusted or record.manifest.builtin):
                self._load_record(record)

    def shutdown(self) -> None:
        """Unload every loaded plugin without changing persisted state."""
        for record in list(self._records.values()):
            if record.loaded:
                self._unload_record(record)

    def enable(self, plugin_id: str, *, trust: bool = False) -> bool:
        record = self._records.get(plugin_id)
        if record is None:
            return False
        if not record.manifest.builtin and not record.trusted:
            if not trust:
                return False
            record.trusted = True
            self._trusted[plugin_id] = True
        record.enabled = True
        self._enabled[plugin_id] = True
        self._save_state()
        return self._load_record(record)

    def disable(self, plugin_id: str) -> bool:
        record = self._records.get(plugin_id)
        if record is None:
            return False
        self._unload_record(record)
        record.enabled = False
        self._enabled[plugin_id] = False
        self._save_state()
        return True

    def reload(self, plugin_id: str) -> bool:
        record = self._records.get(plugin_id)
        if record is None:
            return False
        self._unload_record(record)
        if not record.enabled:
            return True
        return self._load_record(record)

    def set_trusted(self, plugin_id: str, trusted: bool) -> None:
        self._trusted[plugin_id] = bool(trusted)
        record = self._records.get(plugin_id)
        if record is not None:
            record.trusted = bool(trusted)
        self._save_state()

    def _load_record(self, record: PluginRecord) -> bool:
        if record.loaded:
            return True
        if not record.manifest.builtin and not record.trusted:
            record.error = "Plugin is not trusted"
            return False
        try:
            module_name = f"omnify_plugin_{re.sub(r'[^a-zA-Z0-9_]', '_', record.id)}"
            spec = importlib.util.spec_from_file_location(
                module_name, record.manifest.entry_path
            )
            if spec is None or spec.loader is None:
                raise ImportError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            # Allow multi-file plugins to import sibling modules.
            root_str = str(record.manifest.root)
            added_path = root_str not in sys.path
            if added_path:
                sys.path.insert(0, root_str)
            try:
                spec.loader.exec_module(module)
                api = PluginAPI(self, record.manifest)
                setup = getattr(module, "setup", None) or getattr(module, "register", None)
                if not callable(setup):
                    raise AttributeError("plugin must define setup(api) or register(api)")
                setup(api)
            finally:
                if added_path:
                    try:
                        sys.path.remove(root_str)
                    except ValueError:
                        pass
            record.module = module
            record.loaded = True
            record.error = ""
            log(f"plugin loaded: {record.id} {record.manifest.version}")
            return True
        except Exception as exc:
            record.error = str(exc)
            record.loaded = False
            record.module = None
            self._cleanup_plugin(record.id)
            log(
                f"plugin {record.id} failed to load: {exc!r}\n"
                f"{traceback.format_exc()}"
            )
            if self._notify is not None:
                try:
                    self._notify(f"Plugin {record.manifest.name} failed: {exc}")
                except Exception:
                    pass
            return False

    def _unload_record(self, record: PluginRecord) -> None:
        if not record.loaded:
            self._cleanup_plugin(record.id)
            return
        module = record.module
        api = PluginAPI(self, record.manifest)
        teardown = getattr(module, "teardown", None) if module is not None else None
        if callable(teardown):
            try:
                teardown(api)
            except Exception as exc:
                log(f"plugin {record.id}: teardown failed: {exc!r}")
        module_name = f"omnify_plugin_{re.sub(r'[^a-zA-Z0-9_]', '_', record.id)}"
        sys.modules.pop(module_name, None)
        record.module = None
        record.loaded = False
        self._cleanup_plugin(record.id)

    def _cleanup_plugin(self, plugin_id: str) -> None:
        record = self._records.get(plugin_id)
        if record is not None:
            for command_id in list(record.command_ids):
                self._commands.unregister(command_id)
            record.command_ids = []
            record.panel_count = 0
        self._panels.pop(plugin_id, None)

    # -- plugin-facing plumbing ---------------------------------------
    def _register_plugin_command(self, plugin_id: str, command: Command) -> None:
        self._commands.register(command)
        record = self._records.get(plugin_id)
        if record is not None:
            record.command_ids.append(command.id)

    def _unregister_plugin_command(self, plugin_id: str, command_id: str) -> None:
        self._commands.unregister(command_id)
        record = self._records.get(plugin_id)
        if record is not None and command_id in record.command_ids:
            record.command_ids.remove(command_id)

    def _plugin_settings(self, plugin_id: str) -> Dict[str, Any]:
        return self._settings.setdefault(plugin_id, {})

    def _set_plugin_setting(self, plugin_id: str, key: str, value: Any) -> None:
        self._plugin_settings(plugin_id)[str(key)] = value
        self._save_state()

    def _register_plugin_panel(
        self, plugin_id: str, title: str, builder: Callable[[], Any]
    ) -> None:
        self._panels.setdefault(plugin_id, []).append((str(title), builder))
        record = self._records.get(plugin_id)
        if record is not None:
            record.panel_count = len(self._panels[plugin_id])

    def settings_panels(self) -> List[Tuple[PluginManifest, str, Callable[[], Any]]]:
        result: List[Tuple[PluginManifest, str, Callable[[], Any]]] = []
        for plugin_id, panels in self._panels.items():
            record = self._records.get(plugin_id)
            if record is None or not record.loaded:
                continue
            for title, builder in panels:
                result.append((record.manifest, title, builder))
        return result

    def _save_state(self) -> None:
        if self._storage is None:
            return
        self._storage.save(
            self.STATE_KEY,
            {
                "version": self.VERSION,
                "enabled": self._enabled,
                "trusted": self._trusted,
                "settings": self._settings,
            },
        )
