"""Optional LSP bridge for receiver-type hint enrichment."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def lsp_backend_statuses() -> list[dict[str, Any]]:
    backends = [
        ("pyright", "pyright"),
        ("typescript_language_server", "typescript-language-server"),
        ("gopls", "gopls"),
        ("jdtls", "jdtls"),
    ]
    statuses: list[dict[str, Any]] = []
    for backend, executable in backends:
        location = shutil.which(executable)
        statuses.append(
            {
                "backend": backend,
                "available": location is not None,
                "executable": location,
            }
        )
    return statuses


def _lsp_enabled() -> bool:
    raw = os.getenv("BOMBE_ENABLE_LSP_HINTS", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _normalize_path(relative_path: str) -> str:
    return relative_path.strip().lstrip("/").replace("\\", "/")


def _read_sidecar(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _merge_hint(
    target: dict[tuple[int, str], set[str]],
    receiver: str,
    owner_type: str,
    line_start: int,
    line_end: int,
) -> None:
    normalized_receiver = receiver.strip()
    normalized_owner = owner_type.strip()
    if not normalized_receiver or not normalized_owner:
        return
    start = max(1, line_start)
    end = max(start, line_end)
    for line in range(start, min(end, start + 512) + 1):
        target.setdefault((line, normalized_receiver), set()).add(normalized_owner)


def _parse_lsp_payload(payload: dict[str, Any]) -> dict[tuple[int, str], set[str]]:
    hints: dict[tuple[int, str], set[str]] = {}
    entries = payload.get("receiver_hints", [])
    if not isinstance(entries, list):
        return hints
    for item in entries:
        if not isinstance(item, dict):
            continue
        receiver = str(item.get("receiver", "")).strip()
        owner_type = str(item.get("owner_type", "")).strip()
        line_raw = item.get("line", item.get("line_start", 1))
        line_end_raw = item.get("line_end", line_raw)
        try:
            line_start = int(line_raw)
            line_end = int(line_end_raw)
        except (TypeError, ValueError):
            continue
        _merge_hint(hints, receiver, owner_type, line_start, line_end)
    return hints


def load_lsp_receiver_hints(
    repo_root: Path,
    relative_path: str,
) -> dict[tuple[int, str], set[str]]:
    if not _lsp_enabled():
        return {}
    normalized = _normalize_path(relative_path)
    sidecar = repo_root / ".bombe" / "lsp" / f"{normalized}.hints.json"
    payload = _read_sidecar(sidecar)
    if payload is None:
        return {}
    return _parse_lsp_payload(payload)
