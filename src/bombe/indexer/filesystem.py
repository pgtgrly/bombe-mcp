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

DEFAULT_SENSITIVE_EXCLUDE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*secret*",
    "*secrets*",
    "*credential*",
    "id_rsa",
    "id_dsa",
)


def load_gitignore_rules(repo_root: Path) -> list[IgnoreRule]:
    return _load_ignore_file(repo_root / ".gitignore")


def load_bombeignore_rules(repo_root: Path) -> list[IgnoreRule]:
    return _load_ignore_file(repo_root / ".bombeignore")


def _load_ignore_file(ignore_path: Path) -> list[IgnoreRule]:
    if not ignore_path.exists():
        return []
    rules: list[IgnoreRule] = []
    for line in ignore_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        directory_only = stripped.endswith("/")
        pattern = stripped[:-1] if directory_only else stripped
        if pattern.startswith("./"):
            pattern = pattern[2:]
        rules.append(IgnoreRule(pattern=pattern, directory_only=directory_only))
    return rules


def _normalize_pattern(pattern: str) -> IgnoreRule:
    stripped = pattern.strip()
    directory_only = stripped.endswith("/")
    normalized = stripped[:-1] if directory_only else stripped
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return IgnoreRule(pattern=normalized, directory_only=directory_only)


def _matches_pattern(rel_path: str, pattern: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return fnmatch(normalized, pattern) or fnmatch(Path(normalized).name, pattern)


def is_ignored(rel_path: str, is_dir: bool, rules: list[IgnoreRule]) -> bool:
    normalized = rel_path.replace("\\", "/")
    for rule in rules:
        if rule.directory_only and not is_dir:
            continue
        if _matches_pattern(normalized, rule.pattern):
            return True
        if normalized.startswith(f"{rule.pattern}/"):
            return True
    return False


def _matches_any_include(rel_path: str, include_patterns: list[str]) -> bool:
    if not include_patterns:
        return True
    for pattern in include_patterns:
        if _matches_pattern(rel_path, pattern):
            return True
    return False


def iter_repo_files(
    repo_root: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> Iterator[Path]:
    rules = [*load_gitignore_rules(repo_root), *load_bombeignore_rules(repo_root)]
    exclude_sensitive_env = os.getenv("BOMBE_EXCLUDE_SENSITIVE", "1").strip().lower()
    exclude_sensitive = exclude_sensitive_env not in {"0", "false", "no", "off"}
    if exclude_sensitive:
        for pattern in DEFAULT_SENSITIVE_EXCLUDE_PATTERNS:
            rules.append(_normalize_pattern(pattern))
    include = [pattern for pattern in (include_patterns or []) if pattern.strip()]
    for pattern in (exclude_patterns or []):
        stripped = pattern.strip()
        if not stripped:
            continue
        rules.append(_normalize_pattern(stripped))
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
            if not _matches_any_include(rel_file, include):
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
