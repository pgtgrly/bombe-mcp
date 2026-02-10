"""Workspace configuration utilities for multi-root indexing and queries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bombe.models import WorkspaceConfig, WorkspaceRoot


WORKSPACE_SCHEMA_VERSION = 1


def default_workspace_file(repo_root: Path) -> Path:
    return repo_root / ".bombe" / "workspace.json"


def _root_identifier(path: Path) -> str:
    name = path.name or "root"
    digest = hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"


def _normalize_root_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _root_db_path(path: Path) -> Path:
    return path / ".bombe" / "bombe.db"


def _fallback_workspace(repo_root: Path) -> WorkspaceConfig:
    normalized = _normalize_root_path(repo_root)
    root = WorkspaceRoot(
        id=_root_identifier(normalized),
        path=normalized.as_posix(),
        db_path=_root_db_path(normalized).as_posix(),
        enabled=True,
    )
    return WorkspaceConfig(name=normalized.name or "workspace", version=WORKSPACE_SCHEMA_VERSION, roots=[root])


def build_workspace_config(
    repo_root: Path,
    roots: list[Path],
    name: str | None = None,
) -> WorkspaceConfig:
    seen: set[str] = set()
    normalized_roots: list[WorkspaceRoot] = []
    effective_roots = roots or [repo_root]
    for raw_root in effective_roots:
        normalized = _normalize_root_path(raw_root)
        root_path = normalized.as_posix()
        if root_path in seen:
            continue
        seen.add(root_path)
        normalized_roots.append(
            WorkspaceRoot(
                id=_root_identifier(normalized),
                path=root_path,
                db_path=_root_db_path(normalized).as_posix(),
                enabled=True,
            )
        )
    workspace_name = (name or _normalize_root_path(repo_root).name or "workspace").strip()
    return WorkspaceConfig(
        name=workspace_name,
        version=WORKSPACE_SCHEMA_VERSION,
        roots=normalized_roots,
    )


def save_workspace_config(
    repo_root: Path,
    config: WorkspaceConfig,
    workspace_file: Path | None = None,
) -> Path:
    target = (workspace_file or default_workspace_file(repo_root)).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": config.name,
        "version": int(config.version),
        "roots": [asdict(root) for root in config.roots],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _parse_root(item: Any) -> WorkspaceRoot | None:
    if not isinstance(item, dict):
        return None
    path_raw = item.get("path")
    db_path_raw = item.get("db_path")
    if not isinstance(path_raw, str) or not path_raw.strip():
        return None
    path = _normalize_root_path(Path(path_raw.strip()))
    db_path = (
        Path(str(db_path_raw)).expanduser().resolve()
        if isinstance(db_path_raw, str) and db_path_raw.strip()
        else _root_db_path(path)
    )
    root_id_raw = item.get("id")
    root_id = str(root_id_raw).strip() if isinstance(root_id_raw, str) and root_id_raw.strip() else _root_identifier(path)
    enabled = bool(item.get("enabled", True))
    return WorkspaceRoot(
        id=root_id,
        path=path.as_posix(),
        db_path=db_path.as_posix(),
        enabled=enabled,
    )


def load_workspace_config(
    repo_root: Path,
    workspace_file: Path | None = None,
) -> WorkspaceConfig:
    source = (workspace_file or default_workspace_file(repo_root)).expanduser().resolve()
    if not source.exists():
        return _fallback_workspace(repo_root)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return _fallback_workspace(repo_root)
    if not isinstance(payload, dict):
        return _fallback_workspace(repo_root)

    roots_raw = payload.get("roots", [])
    roots: list[WorkspaceRoot] = []
    seen: set[str] = set()
    for item in roots_raw if isinstance(roots_raw, list) else []:
        parsed = _parse_root(item)
        if parsed is None:
            continue
        key = parsed.path
        if key in seen:
            continue
        seen.add(key)
        roots.append(parsed)
    if not roots:
        return _fallback_workspace(repo_root)

    name_raw = payload.get("name")
    version_raw = payload.get("version")
    name = str(name_raw).strip() if isinstance(name_raw, str) and name_raw.strip() else "workspace"
    version = int(version_raw) if isinstance(version_raw, int) else WORKSPACE_SCHEMA_VERSION
    return WorkspaceConfig(name=name, version=version, roots=roots)


def enabled_workspace_roots(config: WorkspaceConfig) -> list[WorkspaceRoot]:
    return [root for root in config.roots if bool(root.enabled)]
