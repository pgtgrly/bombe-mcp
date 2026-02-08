"""Optional semantic hint integrations for call resolution."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def backend_statuses() -> list[dict[str, Any]]:
    backends = [
        ("pyright", "pyright"),
        ("typescript_language_server", "typescript-language-server"),
        ("gopls", "gopls"),
        ("jdtls", "jdtls"),
    ]
    payload: list[dict[str, Any]] = []
    for name, executable in backends:
        location = shutil.which(executable)
        payload.append(
            {
                "backend": name,
                "available": location is not None,
                "executable": location,
            }
        )
    return payload


def _normalize_relative_path(relative_path: str) -> str:
    return relative_path.strip().lstrip("/").replace("\\", "/")


def _sidecar_path(repo_root: Path, relative_path: str) -> Path:
    normalized = _normalize_relative_path(relative_path)
    return repo_root / ".bombe" / "semantic" / f"{normalized}.hints.json"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _merge_hint_entry(
    target: dict[tuple[int, str], set[str]],
    receiver: str,
    owner_type: str,
    start_line: int,
    end_line: int,
) -> None:
    normalized_receiver = receiver.strip()
    normalized_owner = owner_type.strip()
    if not normalized_receiver or not normalized_owner:
        return
    start = max(1, start_line)
    end = max(start, end_line)
    for line in range(start, min(end, start + 512) + 1):
        target.setdefault((line, normalized_receiver), set()).add(normalized_owner)


def _parse_hint_payload(payload: dict[str, Any]) -> dict[tuple[int, str], set[str]]:
    hints: dict[tuple[int, str], set[str]] = {}
    entries = payload.get("receiver_hints", [])
    if not isinstance(entries, list):
        return hints
    for item in entries:
        if not isinstance(item, dict):
            continue
        receiver = str(item.get("receiver", "")).strip()
        owner_type = str(item.get("owner_type", "")).strip()
        if not receiver or not owner_type:
            continue
        line = item.get("line")
        line_start = item.get("line_start", line)
        line_end = item.get("line_end", line)
        try:
            start = int(line_start if line_start is not None else 1)
            end = int(line_end if line_end is not None else start)
        except (TypeError, ValueError):
            continue
        _merge_hint_entry(hints, receiver, owner_type, start, end)
    return hints


def _merge_hint_maps(
    target: dict[tuple[int, str], set[str]],
    source: dict[tuple[int, str], set[str]],
) -> dict[tuple[int, str], set[str]]:
    for key, values in source.items():
        target.setdefault(key, set()).update(values)
    return target


def load_receiver_type_hints(
    repo_root: Path,
    relative_path: str,
) -> dict[tuple[int, str], set[str]]:
    hints: dict[tuple[int, str], set[str]] = {}
    normalized_relative_path = _normalize_relative_path(relative_path)

    sidecar = _sidecar_path(repo_root, normalized_relative_path)
    sidecar_payload = _load_json(sidecar)
    if sidecar_payload is not None:
        _merge_hint_maps(hints, _parse_hint_payload(sidecar_payload))

    global_hints_file = os.getenv("BOMBE_SEMANTIC_HINTS_FILE", "").strip()
    if global_hints_file:
        payload = _load_json(Path(global_hints_file).expanduser().resolve())
        if payload is not None:
            files = payload.get("files", {})
            if isinstance(files, dict):
                lookup_candidates = {
                    normalized_relative_path,
                    relative_path,
                    relative_path.replace("\\", "/").lstrip("/"),
                }
                for candidate in lookup_candidates:
                    file_payload = files.get(candidate)
                    if isinstance(file_payload, dict):
                        _merge_hint_maps(hints, _parse_hint_payload(file_payload))
    return hints
