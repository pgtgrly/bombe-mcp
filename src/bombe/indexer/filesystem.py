"""Filesystem scanning helpers for indexing passes."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    directory_only: bool


LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
}


def load_gitignore_rules(repo_root: Path) -> list[IgnoreRule]:
    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.exists():
        return []

    rules: list[IgnoreRule] = []
    for line in gitignore_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        directory_only = stripped.endswith("/")
        pattern = stripped[:-1] if directory_only else stripped
        if pattern.startswith("./"):
            pattern = pattern[2:]
        rules.append(IgnoreRule(pattern=pattern, directory_only=directory_only))
    return rules


def is_ignored(rel_path: str, is_dir: bool, rules: list[IgnoreRule]) -> bool:
    normalized = rel_path.replace("\\", "/")
    for rule in rules:
        if rule.directory_only and not is_dir:
            continue
        if fnmatch(normalized, rule.pattern):
            return True
        if fnmatch(Path(normalized).name, rule.pattern):
            return True
        if normalized.startswith(f"{rule.pattern}/"):
            return True
    return False


def iter_repo_files(repo_root: Path) -> Iterator[Path]:
    rules = load_gitignore_rules(repo_root)
    implicit_ignored_dirs = {".git", ".bombe"}
    for root, dirs, files in os.walk(repo_root):
        root_path = Path(root)
        rel_root = root_path.relative_to(repo_root)
        if rel_root != Path("."):
            rel_root_str = rel_root.as_posix()
            if is_ignored(rel_root_str, is_dir=True, rules=rules):
                dirs[:] = []
                continue
        dirs[:] = [
            d
            for d in dirs
            if d not in implicit_ignored_dirs
            if not is_ignored(
                (root_path / d).relative_to(repo_root).as_posix(),
                is_dir=True,
                rules=rules,
            )
        ]
        for file_name in files:
            full_path = root_path / file_name
            rel_file = full_path.relative_to(repo_root).as_posix()
            if is_ignored(rel_file, is_dir=False, rules=rules):
                continue
            yield full_path


def detect_language(path: Path) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(path.suffix.lower())


def compute_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
