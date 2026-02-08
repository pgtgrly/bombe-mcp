"""Git-diff based file change detection."""

from __future__ import annotations

import json
import hashlib
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from bombe.indexer.filesystem import compute_content_hash, iter_repo_files
from bombe.models import FileChange


def parse_diff_index_output(output: str) -> list[FileChange]:
    changes: list[FileChange] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            changes.append(
                FileChange(status="R", old_path=parts[1], path=parts[2])
            )
        elif status in {"A", "M", "D"} and len(parts) >= 2:
            changes.append(FileChange(status=status, path=parts[1]))
    return changes


def parse_status_porcelain_output(output: str) -> list[FileChange]:
    changes: list[FileChange] = []
    for line in output.splitlines():
        if not line.strip() or len(line) < 3:
            continue
        status_code = line[:2]
        path_payload = line[3:].strip()
        if not path_payload:
            continue
        if status_code.startswith("R") and " -> " in path_payload:
            old_path, new_path = path_payload.split(" -> ", 1)
            changes.append(FileChange(status="R", old_path=old_path, path=new_path))
            continue
        if status_code == "??":
            changes.append(FileChange(status="A", path=path_payload))
            continue
        dominant = status_code[1] if status_code[1] != " " else status_code[0]
        if dominant in {"A", "M", "D"}:
            changes.append(FileChange(status=dominant, path=path_payload))
    return changes


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/")
    return fnmatch(normalized, pattern) or fnmatch(Path(normalized).name, pattern)


def _keep_change(
    path: str,
    include_patterns: list[str] | None,
    exclude_patterns: list[str] | None,
) -> bool:
    include = [pattern for pattern in (include_patterns or []) if pattern.strip()]
    exclude = [pattern for pattern in (exclude_patterns or []) if pattern.strip()]
    if include:
        if not any(_matches_pattern(path, pattern) for pattern in include):
            return False
    if exclude:
        if any(_matches_pattern(path, pattern) for pattern in exclude):
            return False
    return True


def _read_snapshot(snapshot_path: Path) -> dict[str, str]:
    if not snapshot_path.exists():
        return {}
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    files = payload.get("files", {})
    if not isinstance(files, dict):
        return {}
    snapshot: dict[str, str] = {}
    for path, digest in files.items():
        if isinstance(path, str) and isinstance(digest, str):
            snapshot[path] = digest
    return snapshot


def _write_snapshot(snapshot_path: Path, files: dict[str, str]) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"files": files}
    snapshot_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _scan_filesystem_snapshot(
    repo_root: Path,
    include_patterns: list[str] | None,
    exclude_patterns: list[str] | None,
) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for file_path in iter_repo_files(
        repo_root,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    ):
        rel = file_path.relative_to(repo_root).as_posix()
        try:
            snapshot[rel] = compute_content_hash(file_path)
        except Exception:
            continue
    return snapshot


def _diff_snapshots(previous: dict[str, str], current: dict[str, str]) -> list[FileChange]:
    changes: list[FileChange] = []
    previous_paths = set(previous.keys())
    current_paths = set(current.keys())
    for path in sorted(current_paths - previous_paths):
        changes.append(FileChange(status="A", path=path))
    for path in sorted(previous_paths - current_paths):
        changes.append(FileChange(status="D", path=path))
    for path in sorted(previous_paths & current_paths):
        if previous[path] != current[path]:
            changes.append(FileChange(status="M", path=path))
    return changes


def _filesystem_changed_files(
    repo_root: Path,
    include_patterns: list[str] | None,
    exclude_patterns: list[str] | None,
) -> list[FileChange]:
    include = [pattern for pattern in (include_patterns or []) if pattern.strip()]
    exclude = [pattern for pattern in (exclude_patterns or []) if pattern.strip()]
    filter_key = json.dumps({"include": include, "exclude": exclude}, sort_keys=True)
    digest = hashlib.sha256(filter_key.encode("utf-8")).hexdigest()[:12]
    snapshot_path = repo_root / ".bombe" / f"watch-snapshot-{digest}.json"
    previous = _read_snapshot(snapshot_path)
    current = _scan_filesystem_snapshot(repo_root, include_patterns, exclude_patterns)
    _write_snapshot(snapshot_path, current)
    return _diff_snapshots(previous, current)


def get_changed_files(
    repo_root: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[FileChange]:
    try:
        completed = subprocess.run(
            ["git", "-C", repo_root.as_posix(), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        completed = None

    if completed is not None and completed.returncode == 0:
        parsed = parse_status_porcelain_output(completed.stdout)
        return [
            change
            for change in parsed
            if _keep_change(change.path, include_patterns, exclude_patterns)
        ]
    return _filesystem_changed_files(
        repo_root=repo_root,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
