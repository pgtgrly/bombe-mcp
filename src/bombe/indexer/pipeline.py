"""Indexing pipeline orchestration."""

from __future__ import annotations

import time
from pathlib import Path

from bombe.indexer.filesystem import compute_content_hash, detect_language, iter_repo_files
from bombe.models import FileChange, FileRecord, IndexStats
from bombe.store.database import Database


def full_index(repo_root: Path, db: Database, workers: int = 4) -> IndexStats:
    del workers
    started = time.perf_counter()
    files_seen = 0
    file_records: list[FileRecord] = []

    for file_path in iter_repo_files(repo_root):
        files_seen += 1
        language = detect_language(file_path)
        if language is None:
            continue
        rel_path = file_path.relative_to(repo_root).as_posix()
        file_records.append(
            FileRecord(
                path=rel_path,
                language=language,
                content_hash=compute_content_hash(file_path),
                size_bytes=file_path.stat().st_size,
            )
        )

    db.upsert_files(file_records)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return IndexStats(
        files_seen=files_seen,
        files_indexed=len(file_records),
        symbols_indexed=0,
        edges_indexed=0,
        elapsed_ms=elapsed_ms,
    )


def incremental_index(repo_root: Path, db: Database, changes: list[FileChange]) -> IndexStats:
    started = time.perf_counter()
    files_seen = len(changes)
    files_indexed = 0

    for change in changes:
        status = change.status.upper()
        if status == "D":
            db.delete_file_graph(change.path)
            continue
        if status == "R" and change.old_path:
            db.rename_file(change.old_path, change.path)
            continue
        if status not in {"A", "M"}:
            continue

        full_path = repo_root / change.path
        if not full_path.exists() or not full_path.is_file():
            continue
        language = detect_language(full_path)
        if language is None:
            continue
        db.upsert_files(
            [
                FileRecord(
                    path=change.path,
                    language=language,
                    content_hash=compute_content_hash(full_path),
                    size_bytes=full_path.stat().st_size,
                )
            ]
        )
        files_indexed += 1

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return IndexStats(
        files_seen=files_seen,
        files_indexed=files_indexed,
        symbols_indexed=0,
        edges_indexed=0,
        elapsed_ms=elapsed_ms,
    )
