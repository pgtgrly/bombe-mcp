"""Indexing pipeline orchestration."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
import os
import time
import uuid
from pathlib import Path
from typing import Any

from bombe.indexer.callgraph import build_call_edges
from bombe.indexer.filesystem import compute_content_hash, detect_language, iter_repo_files
from bombe.indexer.imports import resolve_imports
from bombe.indexer.pagerank import recompute_pagerank
from bombe.indexer.parser import parse_file
from bombe.indexer.semantic import load_receiver_type_hints
from bombe.indexer.symbols import extract_symbols
from bombe.models import FileChange, FileRecord, ImportRecord, IndexStats
from bombe.models import ParsedUnit, SymbolRecord
from bombe.store.database import Database


@dataclass(frozen=True)
class _ExtractionResult:
    file_path: str
    language: str
    source: str = ""
    python_tree: Any | None = None
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    error_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None


def _is_strict_runtime() -> bool:
    raw = os.getenv("BOMBE_REQUIRE_TREE_SITTER", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _diagnostic_category_and_hint(stage: str, error: Exception) -> tuple[str, str]:
    message = str(error)
    if isinstance(error, FileNotFoundError):
        return "file_not_found", "Ensure the file exists and rerun indexing."
    if isinstance(error, PermissionError):
        return "permission_denied", "Ensure Bombe can read this path and rerun indexing."
    if isinstance(error, OSError):
        return "io_error", "Check filesystem health and path accessibility."
    if isinstance(error, SyntaxError):
        return "syntax_error", "Fix source syntax errors and rerun indexing."
    if stage == "parse" and "Tree-sitter parser unavailable" in message:
        return (
            "parser_unavailable",
            "Install compatible tree-sitter dependencies for the required language backends.",
        )
    if stage == "extract":
        return "extractor_failure", "Check language extractor compatibility for this source shape."
    if stage == "import_resolve":
        return "import_resolution_failure", "Verify import paths and module mapping rules."
    if stage == "callgraph":
        return "callgraph_failure", "Inspect symbol extraction output and receiver type inference."
    if stage.startswith("store_"):
        return "database_write_failure", "Check SQLite schema compatibility and writable storage."
    if stage == "pagerank":
        return "pagerank_failure", "Inspect graph integrity before recomputing PageRank."
    return "unexpected_error", "Inspect the error detail and runtime logs for remediation."


def _record_indexing_diagnostic(
    db: Database,
    run_id: str,
    stage: str,
    file_path: str | None,
    language: str | None,
    error: Exception,
) -> None:
    category, hint = _diagnostic_category_and_hint(stage, error)
    message = str(error).strip() or repr(error)
    try:
        db.record_indexing_diagnostic(
            run_id=run_id,
            stage=stage,
            category=category,
            message=message,
            hint=hint,
            file_path=file_path,
            language=language,
            severity="error",
        )
    except Exception:
        return


def _exception_from_error_details(error_type: str | None, error_message: str | None) -> Exception:
    message = (error_message or "").strip()
    if error_type == "SyntaxError":
        return SyntaxError(message)
    if error_type == "FileNotFoundError":
        return FileNotFoundError(message)
    if error_type == "PermissionError":
        return PermissionError(message)
    if error_type == "OSError":
        return OSError(message)
    if error_type:
        return RuntimeError(f"{error_type}: {message}")
    return RuntimeError(message or "unknown indexing error")


def _extract_file_worker(job: tuple[str, str, str]) -> _ExtractionResult:
    repo_root_str, relative_path, language = job
    absolute_path = Path(repo_root_str) / relative_path
    try:
        parsed_raw = parse_file(absolute_path, language)
    except Exception as exc:
        return _ExtractionResult(
            file_path=relative_path,
            language=language,
            error_stage="parse",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    parsed = ParsedUnit(
        path=Path(relative_path),
        language=parsed_raw.language,
        source=parsed_raw.source,
        tree=parsed_raw.tree,
    )
    try:
        symbols, imports = extract_symbols(parsed)
    except Exception as exc:
        return _ExtractionResult(
            file_path=relative_path,
            language=language,
            source=parsed.source,
            python_tree=parsed.tree if language == "python" else None,
            error_stage="extract",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    return _ExtractionResult(
        file_path=relative_path,
        language=language,
        source=parsed.source,
        python_tree=parsed.tree if language == "python" else None,
        symbols=symbols,
        imports=imports,
    )


def _parallel_extract_file_payloads(
    repo_root: Path,
    files: list[FileRecord],
    workers: int,
) -> tuple[list[_ExtractionResult], dict[str, Any]]:
    jobs = [(repo_root.as_posix(), record.path, record.language) for record in files]
    if not jobs:
        return [], {
            "extractor_mode": "sequential",
            "worker_count": max(1, workers),
            "jobs_total": 0,
            "queue_depth_max": 0,
            "extract_elapsed_ms": 0,
            "files_per_second": 0.0,
        }

    normalized_workers = max(1, workers)
    if normalized_workers <= 1 or len(jobs) <= 1:
        started = time.perf_counter()
        results = [_extract_file_worker(job) for job in jobs]
        elapsed = max(time.perf_counter() - started, 1e-6)
        return sorted(results, key=lambda item: item.file_path), {
            "extractor_mode": "sequential",
            "worker_count": 1,
            "jobs_total": len(jobs),
            "queue_depth_max": 1,
            "extract_elapsed_ms": int(elapsed * 1000),
            "files_per_second": round(len(jobs) / elapsed, 2),
        }

    started = time.perf_counter()
    max_inflight = max(2, normalized_workers * 2)
    queue_depth_max = 0
    try:
        results: list[_ExtractionResult] = []
        with ProcessPoolExecutor(max_workers=normalized_workers) as executor:
            next_index = 0
            pending: dict[Any, tuple[str, str, str]] = {}
            while next_index < len(jobs) or pending:
                while next_index < len(jobs) and len(pending) < max_inflight:
                    job = jobs[next_index]
                    future = executor.submit(_extract_file_worker, job)
                    pending[future] = job
                    next_index += 1
                    queue_depth_max = max(queue_depth_max, len(pending))
                if not pending:
                    continue
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    _ = pending.pop(future, None)
                    results.append(future.result())
        elapsed = max(time.perf_counter() - started, 1e-6)
        return sorted(results, key=lambda item: item.file_path), {
            "extractor_mode": "process",
            "worker_count": normalized_workers,
            "jobs_total": len(jobs),
            "queue_depth_max": queue_depth_max,
            "extract_elapsed_ms": int(elapsed * 1000),
            "files_per_second": round(len(jobs) / elapsed, 2),
        }
    except Exception:
        fallback_started = time.perf_counter()
        fallback_results = [_extract_file_worker(job) for job in jobs]
        elapsed = max(time.perf_counter() - fallback_started, 1e-6)
        return sorted(fallback_results, key=lambda item: item.file_path), {
            "extractor_mode": "sequential_fallback",
            "worker_count": 1,
            "jobs_total": len(jobs),
            "queue_depth_max": 1,
            "extract_elapsed_ms": int(elapsed * 1000),
            "files_per_second": round(len(jobs) / elapsed, 2),
        }


def _scan_repo_files(
    repo_root: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> tuple[int, list[FileRecord]]:
    files_seen = 0
    file_records: list[FileRecord] = []
    for file_path in iter_repo_files(
        repo_root,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    ):
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


def _load_symbols(db: Database) -> tuple[list[SymbolRecord], dict[tuple[str, str], int]]:
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
    qualified_to_id = {
        (str(row["qualified_name"]), str(row["file_path"])): int(row["id"]) for row in rows
    }
    return symbols, qualified_to_id


def _current_files(db: Database) -> list[FileRecord]:
    rows = db.query("SELECT path, language, content_hash, size_bytes FROM files ORDER BY path;")
    return [
        FileRecord(
            path=row["path"],
            language=row["language"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
        )
        for row in rows
    ]


def _rebuild_dependencies(
    repo_root: Path,
    db: Database,
    run_id: str,
    workers: int,
) -> tuple[int, int, dict[str, Any]]:
    files = _current_files(db)
    files_map = {record.path: record for record in files}
    file_id_lookup = {
        record.path: index + 1 for index, record in enumerate(sorted(files, key=lambda item: item.path))
    }
    strict_runtime = _is_strict_runtime()
    parsed_cache: dict[str, ParsedUnit] = {}
    imports_by_file: dict[str, list[ImportRecord]] = {}
    symbols_by_file: dict[str, list[SymbolRecord]] = {}
    extraction_results, extraction_telemetry = _parallel_extract_file_payloads(
        repo_root=repo_root,
        files=files,
        workers=max(1, workers),
    )
    result_by_path = {result.file_path: result for result in extraction_results}

    symbol_count = 0
    for file_record in files:
        absolute = repo_root / file_record.path
        if not absolute.exists():
            db.delete_file_graph(file_record.path)
            continue

        result = result_by_path.get(file_record.path)
        if result is None:
            error = RuntimeError("missing extraction result")
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage="extract",
                file_path=file_record.path,
                language=file_record.language,
                error=error,
            )
            if strict_runtime:
                raise error
            continue

        if result.error_stage:
            error = _exception_from_error_details(result.error_type, result.error_message)
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage=result.error_stage,
                file_path=file_record.path,
                language=file_record.language,
                error=error,
            )
            if strict_runtime:
                raise error
            continue

        parsed_cache[file_record.path] = ParsedUnit(
            path=Path(file_record.path),
            language=file_record.language,
            source=result.source,
            tree=result.python_tree if file_record.language == "python" else None,
        )
        symbols_by_file[file_record.path] = result.symbols
        imports_by_file[file_record.path] = result.imports
        try:
            db.replace_file_symbols(file_record.path, result.symbols)
        except Exception as exc:
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage="store_symbols",
                file_path=file_record.path,
                language=file_record.language,
                error=exc,
            )
            if strict_runtime:
                raise
            parsed_cache.pop(file_record.path, None)
            imports_by_file.pop(file_record.path, None)
            symbols_by_file.pop(file_record.path, None)
            continue
        symbol_count += len(result.symbols)

    all_symbols, qualified_to_id = _load_symbols(db)
    edge_count = 0
    for file_record in files:
        parsed = parsed_cache.get(file_record.path)
        if parsed is None:
            continue
        import_records = imports_by_file.get(file_record.path, [])
        try:
            import_edges, external = resolve_imports(
                repo_root.as_posix(),
                file_record,
                import_records,
                files_map,
                file_id_lookup=file_id_lookup,
            )
        except Exception as exc:
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage="import_resolve",
                file_path=file_record.path,
                language=file_record.language,
                error=exc,
            )
            if strict_runtime:
                raise
            import_edges = []
            external = []
        try:
            db.replace_external_deps(file_record.path, external)
        except Exception as exc:
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage="store_external_deps",
                file_path=file_record.path,
                language=file_record.language,
                error=exc,
            )
            if strict_runtime:
                raise

        try:
            call_edges = build_call_edges(
                parsed=parsed,
                file_symbols=symbols_by_file.get(file_record.path, []),
                candidate_symbols=all_symbols,
                symbol_id_lookup=qualified_to_id,
                semantic_receiver_type_hints=load_receiver_type_hints(
                    repo_root=repo_root,
                    relative_path=file_record.path,
                ),
            )
        except Exception as exc:
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage="callgraph",
                file_path=file_record.path,
                language=file_record.language,
                error=exc,
            )
            if strict_runtime:
                raise
            call_edges = []
        combined_edges = [*import_edges, *call_edges]
        try:
            db.replace_file_edges(file_record.path, combined_edges)
        except Exception as exc:
            _record_indexing_diagnostic(
                db=db,
                run_id=run_id,
                stage="store_edges",
                file_path=file_record.path,
                language=file_record.language,
                error=exc,
            )
            if strict_runtime:
                raise
            continue
        edge_count += len(combined_edges)

    try:
        recompute_pagerank(db)
    except Exception as exc:
        _record_indexing_diagnostic(
            db=db,
            run_id=run_id,
            stage="pagerank",
            file_path=None,
            language=None,
            error=exc,
        )
        if strict_runtime:
            raise
    return symbol_count, edge_count, extraction_telemetry


def _progress_snapshots(
    mode: str,
    files_seen: int,
    files_indexed: int,
    symbols_indexed: int,
    edges_indexed: int,
    elapsed_ms: int,
) -> list[dict[str, Any]]:
    return [
        {
            "step": "scan",
            "completed": int(files_seen),
            "total": int(files_seen),
            "progress_pct": 20,
        },
        {
            "step": "extract_symbols",
            "completed": int(files_indexed),
            "total": int(files_indexed),
            "progress_pct": 55,
        },
        {
            "step": "build_edges",
            "completed": int(edges_indexed),
            "total": int(edges_indexed),
            "progress_pct": 85,
        },
        {
            "step": "complete",
            "completed": int(files_indexed),
            "total": int(files_indexed),
            "progress_pct": 100,
            "elapsed_ms": int(elapsed_ms),
            "mode": mode,
            "symbols_indexed": int(symbols_indexed),
            "edges_indexed": int(edges_indexed),
        },
    ]


def full_index(
    repo_root: Path,
    db: Database,
    workers: int = 4,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> IndexStats:
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
    files_seen, file_records = _scan_repo_files(
        repo_root,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    db.upsert_files(file_records)
    symbols_indexed, edges_indexed, extract_telemetry = _rebuild_dependencies(
        repo_root,
        db,
        run_id=run_id,
        workers=max(1, workers),
    )
    db.bump_cache_epoch()
    diagnostics_summary = db.summarize_indexing_diagnostics(run_id=run_id)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    files_per_second = round((int(len(file_records)) * 1000) / max(1, elapsed_ms), 2)
    indexing_telemetry = {
        "parallel_workers_requested": max(1, workers),
        "parallel_workers_effective": int(extract_telemetry.get("worker_count", max(1, workers))),
        "extractor_mode": str(extract_telemetry.get("extractor_mode", "sequential")),
        "extract_elapsed_ms": int(extract_telemetry.get("extract_elapsed_ms", 0)),
        "extract_files_per_second": float(extract_telemetry.get("files_per_second", 0.0)),
        "queue_depth_max": int(extract_telemetry.get("queue_depth_max", 0)),
        "files_per_second": files_per_second,
    }
    return IndexStats(
        files_seen=files_seen,
        files_indexed=len(file_records),
        symbols_indexed=symbols_indexed,
        edges_indexed=edges_indexed,
        elapsed_ms=elapsed_ms,
        run_id=run_id,
        diagnostics_summary=diagnostics_summary,
        indexing_telemetry=indexing_telemetry,
        progress_snapshots=_progress_snapshots(
            mode="full",
            files_seen=files_seen,
            files_indexed=len(file_records),
            symbols_indexed=symbols_indexed,
            edges_indexed=edges_indexed,
            elapsed_ms=elapsed_ms,
        ),
    )


def incremental_index(
    repo_root: Path,
    db: Database,
    changes: list[FileChange],
    workers: int = 4,
) -> IndexStats:
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
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

    symbols_indexed, edges_indexed, extract_telemetry = _rebuild_dependencies(
        repo_root,
        db,
        run_id=run_id,
        workers=max(1, workers),
    )
    db.bump_cache_epoch()
    diagnostics_summary = db.summarize_indexing_diagnostics(run_id=run_id)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    files_per_second = round((int(files_indexed) * 1000) / max(1, elapsed_ms), 2)
    indexing_telemetry = {
        "parallel_workers_requested": max(1, workers),
        "parallel_workers_effective": int(extract_telemetry.get("worker_count", max(1, workers))),
        "extractor_mode": str(extract_telemetry.get("extractor_mode", "sequential")),
        "extract_elapsed_ms": int(extract_telemetry.get("extract_elapsed_ms", 0)),
        "extract_files_per_second": float(extract_telemetry.get("files_per_second", 0.0)),
        "queue_depth_max": int(extract_telemetry.get("queue_depth_max", 0)),
        "files_per_second": files_per_second,
    }
    return IndexStats(
        files_seen=files_seen,
        files_indexed=files_indexed,
        symbols_indexed=symbols_indexed,
        edges_indexed=edges_indexed,
        elapsed_ms=elapsed_ms,
        run_id=run_id,
        diagnostics_summary=diagnostics_summary,
        indexing_telemetry=indexing_telemetry,
        progress_snapshots=_progress_snapshots(
            mode="incremental",
            files_seen=files_seen,
            files_indexed=files_indexed,
            symbols_indexed=symbols_indexed,
            edges_indexed=edges_indexed,
            elapsed_ms=elapsed_ms,
        ),
    )
