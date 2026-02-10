"""Plugin loading and lifecycle hook execution."""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


@dataclass(frozen=True)
class _PluginRegistration:
    name: str
    instance: Any
    timeout_ms: int


def _default_config_path(repo_root: Path) -> Path:
    return repo_root / ".bombe" / "plugins.json"


def _load_module_from_path(path: Path) -> ModuleType:
    module_name = f"bombe_plugin_{path.stem}_{abs(hash(path.as_posix()))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load plugin spec: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_module(entry: dict[str, Any]) -> tuple[str, ModuleType]:
    module_name = entry.get("module")
    path_value = entry.get("path")
    if isinstance(module_name, str) and module_name.strip():
        normalized_module = module_name.strip()
        return normalized_module, importlib.import_module(normalized_module)
    if isinstance(path_value, str) and path_value.strip():
        path = Path(path_value).expanduser().resolve()
        return path.stem, _load_module_from_path(path)
    raise RuntimeError("plugin entry requires either 'module' or 'path'")


class PluginManager:
    def __init__(self, registrations: list[_PluginRegistration]) -> None:
        self._registrations = registrations
        self._stats = {
            "plugins_loaded": len(registrations),
            "hook_calls": 0,
            "hook_errors": 0,
            "hook_timeouts": 0,
            "plugin_names": [registration.name for registration in registrations],
        }

    @classmethod
    def from_repo(cls, repo_root: Path, config_path: Path | None = None) -> "PluginManager":
        source = (config_path or _default_config_path(repo_root)).expanduser().resolve()
        if not source.exists():
            return cls([])
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            return cls([])
        if not isinstance(payload, dict):
            return cls([])
        plugins_raw = payload.get("plugins", [])
        if not isinstance(plugins_raw, list):
            return cls([])
        registrations: list[_PluginRegistration] = []
        for item in plugins_raw:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("enabled", True)):
                continue
            timeout_ms = max(1, int(item.get("timeout_ms", 1000)))
            try:
                name, module = _load_module(item)
            except Exception as exc:
                logging.getLogger(__name__).warning("Plugin load failed: %s", str(exc))
                continue
            factory = getattr(module, "build_plugin", None)
            if callable(factory):
                try:
                    instance = factory()
                except Exception as exc:
                    logging.getLogger(__name__).warning("Plugin build failed: %s", str(exc))
                    continue
            else:
                instance = module
            registrations.append(
                _PluginRegistration(
                    name=name,
                    instance=instance,
                    timeout_ms=timeout_ms,
                )
            )
        return cls(registrations)

    def _run_hook(
        self,
        registration: _PluginRegistration,
        hook_name: str,
        *args: Any,
    ) -> Any:
        hook = getattr(registration.instance, hook_name, None)
        if not callable(hook):
            return None
        started = time.perf_counter()
        self._stats["hook_calls"] += 1
        try:
            result = hook(*args)
        except Exception as exc:
            self._stats["hook_errors"] += 1
            logging.getLogger(__name__).warning(
                "Plugin hook failed: plugin=%s hook=%s error=%s",
                registration.name,
                hook_name,
                str(exc),
            )
            return None
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > registration.timeout_ms:
            self._stats["hook_timeouts"] += 1
            logging.getLogger(__name__).warning(
                "Plugin hook exceeded timeout: plugin=%s hook=%s elapsed_ms=%.3f timeout_ms=%d",
                registration.name,
                hook_name,
                elapsed_ms,
                registration.timeout_ms,
            )
        return result

    def before_index(self, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = dict(payload)
        for registration in self._registrations:
            result = self._run_hook(registration, "before_index", mode, dict(current))
            if isinstance(result, dict):
                current = result
        return current

    def after_index(
        self,
        mode: str,
        payload: dict[str, Any],
        error: str | None = None,
    ) -> None:
        for registration in self._registrations:
            self._run_hook(registration, "after_index", mode, dict(payload), error)

    def before_query(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = dict(payload)
        for registration in self._registrations:
            result = self._run_hook(registration, "before_query", tool_name, dict(current))
            if isinstance(result, dict):
                current = result
        return current

    def after_query(
        self,
        tool_name: str,
        payload: dict[str, Any],
        response: dict[str, Any] | str | None,
        error: str | None = None,
    ) -> None:
        for registration in self._registrations:
            self._run_hook(registration, "after_query", tool_name, dict(payload), response, error)

    def stats(self) -> dict[str, Any]:
        return dict(self._stats)
