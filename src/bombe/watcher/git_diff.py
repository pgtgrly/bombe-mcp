"""Git-diff based file change detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

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


def get_changed_files(repo_root: Path) -> list[FileChange]:
    try:
        completed = subprocess.run(
            ["git", "-C", repo_root.as_posix(), "diff-index", "--name-status", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []

    if completed.returncode != 0:
        return []
    return parse_diff_index_output(completed.stdout)
