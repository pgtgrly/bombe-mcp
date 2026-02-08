"""Configuration handling for Bombe server startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    db_path: Path
    log_level: str
    init_only: bool
    runtime_profile: str


def resolve_repo_path(repo: Path) -> Path:
    repo_root = repo.expanduser().resolve()
    if not repo_root.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo_root}")
    if not repo_root.is_dir():
        raise NotADirectoryError(f"Repository path is not a directory: {repo_root}")
    return repo_root


def resolve_db_path(repo_root: Path, db_path: Path | None) -> Path:
    if db_path is None:
        return repo_root / ".bombe" / "bombe.db"
    return db_path.expanduser().resolve()


def build_settings(
    repo: Path,
    db_path: Path | None,
    log_level: str,
    init_only: bool,
    runtime_profile: str,
) -> Settings:
    repo_root = resolve_repo_path(repo)
    resolved_db_path = resolve_db_path(repo_root, db_path)
    return Settings(
        repo_root=repo_root,
        db_path=resolved_db_path,
        log_level=log_level,
        init_only=init_only,
        runtime_profile=runtime_profile,
    )
