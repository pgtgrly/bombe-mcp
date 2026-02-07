"""Indexing pipeline orchestration."""

from __future__ import annotations

import time
import zlib
from pathlib import Path

from bombe.indexer.callgraph import build_call_edges
from bombe.indexer.filesystem import compute_content_hash, detect_language, iter_repo_files
from bombe.indexer.imports import resolve_imports
from bombe.indexer.pagerank import recompute_pagerank
from bombe.indexer.parser import parse_file
from bombe.indexer.symbols import extract_symbols
from bombe.models import EdgeRecord, FileChange, FileRecord, ImportRecord, IndexStats
from bombe.models import ParsedUnit, SymbolRecord
from bombe.store.database import Database


def _symbol_hash(qualified_name: str) -> int:
    return int(zlib.crc32(qualified_name.encode("utf-8")) & 0x7FFFFFFF)


def _scan_repo_files(repo_root: Path) -> tuple[int, list[FileRecord]]:
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
    return files_seen, file_records


def _parse_relative(repo_root: Path, file_record: FileRecord) -> ParsedUnit:
    absolute = repo_root / file_record.path
    parsed = parse_file(absolute, file_record.language)
    return ParsedUnit(
        path=Path(file_record.path),
        language=parsed.language,
        source=parsed.source,
        tree=parsed.tree,
    )


def _load_symbols(db: Database) -> tuple[list[SymbolRecord], dict[int, int]]:
    rows = db.query(
        """
        SELECT id, name, qualified_name, kind, file_path, start_line, end_line,
               signature, return_type, visibility, is_async, is_static,
               parent_symbol_id, docstring, pagerank_score
        FROM symbols;
        """
    )
    symbols = [
        SymbolRecord(
            name=row["name"],
            qualified_name=row["qualified_name"],
            kind=row["kind"],
            file_path=row["file_path"],
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            signature=row["signature"],
            return_type=row["return_type"],
            visibility=row["visibility"],
            is_async=bool(row["is_async"]),
            is_static=bool(row["is_static"]),
            parent_symbol_id=row["parent_symbol_id"],
            docstring=row["docstring"],
            pagerank_score=float(row["pagerank_score"] or 0.0),
        )
        for row in rows
    ]
    hash_to_id = {_symbol_hash(row["qualified_name"]): int(row["id"]) for row in rows}
    return symbols, hash_to_id


def _current_files(db: Database) -> list[FileRecord]:
    rows = db.query("SELECT path, language, content_hash, size_bytes FROM files;")
    return [
        FileRecord(
            path=row["path"],
            language=row["language"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
        )
        for row in rows
    ]


def _rebuild_dependencies(repo_root: Path, db: Database) -> tuple[int, int]:
    files = _current_files(db)
    files_map = {record.path: record for record in files}
    parsed_cache: dict[str, ParsedUnit] = {}
    imports_by_file: dict[str, list[ImportRecord]] = {}
    symbols_by_file: dict[str, list[SymbolRecord]] = {}

    symbol_count = 0
    for file_record in files:
        absolute = repo_root / file_record.path
        if not absolute.exists():
            db.delete_file_graph(file_record.path)
            continue
        parsed = _parse_relative(repo_root, file_record)
        parsed_cache[file_record.path] = parsed
        symbols, import_records = extract_symbols(parsed)
        symbol_count += len(symbols)
        symbols_by_file[file_record.path] = symbols
        imports_by_file[file_record.path] = import_records
        db.replace_file_symbols(file_record.path, symbols)

    all_symbols, hash_to_id = _load_symbols(db)
    edge_count = 0
    for file_record in files:
        parsed = parsed_cache.get(file_record.path)
        if parsed is None:
            continue
        import_records = imports_by_file.get(file_record.path, [])
        import_edges, external = resolve_imports(
            repo_root.as_posix(), file_record, import_records, files_map
        )
        db.replace_external_deps(file_record.path, external)

        call_edges = build_call_edges(
            parsed=parsed,
            file_symbols=symbols_by_file.get(file_record.path, []),
            candidate_symbols=all_symbols,
        )
        mapped_call_edges = []
        for edge in call_edges:
            source_id = hash_to_id.get(edge.source_id)
            target_id = hash_to_id.get(edge.target_id)
            if source_id is None or target_id is None:
                continue
            mapped_call_edges.append(
                EdgeRecord(
                    source_id=source_id,
                    target_id=target_id,
                    source_type=edge.source_type,
                    target_type=edge.target_type,
                    relationship=edge.relationship,
                    file_path=edge.file_path,
                    line_number=edge.line_number,
                    confidence=edge.confidence,
                )
            )
        combined_edges = [*import_edges, *mapped_call_edges]
        edge_count += len(combined_edges)
        db.replace_file_edges(file_record.path, combined_edges)

    recompute_pagerank(db)
    return symbol_count, edge_count


def full_index(repo_root: Path, db: Database, workers: int = 4) -> IndexStats:
    del workers
    started = time.perf_counter()
    files_seen, file_records = _scan_repo_files(repo_root)
    db.upsert_files(file_records)
    symbols_indexed, edges_indexed = _rebuild_dependencies(repo_root, db)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return IndexStats(
        files_seen=files_seen,
        files_indexed=len(file_records),
        symbols_indexed=symbols_indexed,
        edges_indexed=edges_indexed,
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

    symbols_indexed, edges_indexed = _rebuild_dependencies(repo_root, db)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return IndexStats(
        files_seen=files_seen,
        files_indexed=files_indexed,
        symbols_indexed=symbols_indexed,
        edges_indexed=edges_indexed,
        elapsed_ms=elapsed_ms,
    )
