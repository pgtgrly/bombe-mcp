"""CLI entry point for the Bombe MCP server."""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from bombe.config import build_settings
from bombe.indexer.parser import tree_sitter_capability_report
from bombe.indexer.pipeline import full_index, incremental_index
from bombe.indexer.semantic import backend_statuses
from bombe.models import FileChange
from bombe.sync.orchestrator import run_sync_cycle
from bombe.sync.transport import FileControlPlaneTransport, HttpControlPlaneTransport
from bombe.watcher.git_diff import get_changed_files
from bombe.store.database import Database, SCHEMA_VERSION
from bombe.plugins import PluginManager
from bombe.tools.definitions import build_tool_registry, register_tools
from bombe.ui_api import build_inspector_bundle
from bombe.workspace import (
    build_workspace_config,
    default_workspace_file,
    enabled_workspace_roots,
    load_workspace_config,
    save_workspace_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bombe",
        description="Structure-aware code retrieval MCP server.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Path to the repository root.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Optional path to SQLite database file. Defaults to <repo>/.bombe/bombe.db.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log verbosity level.",
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="Initialize storage and exit without starting MCP transport.",
    )
    parser.add_argument(
        "--hybrid-sync",
        action="store_true",
        help="Enable sync push/pull cycle after index operations.",
    )
    parser.add_argument(
        "--control-plane-root",
        type=Path,
        default=None,
        help="Root directory for file-based control-plane transport.",
    )
    parser.add_argument(
        "--control-plane-url",
        type=str,
        default=None,
        help="Optional reference control-plane HTTP base URL.",
    )
    parser.add_argument(
        "--sync-timeout-ms",
        type=int,
        default=500,
        help="Per-request timeout for sync operations in milliseconds.",
    )
    parser.add_argument(
        "--runtime-profile",
        choices=["default", "strict"],
        default="default",
        help="Runtime policy profile. strict enforces hard-fail behavior for required parser backends.",
    )
    parser.add_argument(
        "--diagnostics-limit",
        type=int,
        default=50,
        help="Maximum diagnostics rows returned by status/doctor/diagnostics outputs.",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Optional glob include filter. Repeat for multiple patterns.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Optional glob exclude filter. Repeat for multiple patterns.",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("preflight", help="Run startup compatibility checks and exit.")

    serve_parser = subparsers.add_parser("serve", help="Start MCP server runtime.")
    serve_parser.add_argument(
        "--index-mode",
        choices=["none", "full", "incremental"],
        default="none",
        help="Optional indexing phase to run before serving.",
    )

    full_parser = subparsers.add_parser("index-full", help="Run a full repository index and exit.")
    full_parser.add_argument("--workers", type=int, default=4, help="Worker count hint.")

    inc_parser = subparsers.add_parser(
        "index-incremental",
        help="Run incremental index using git-detected changed files and exit.",
    )
    inc_parser.add_argument("--workers", type=int, default=4, help="Worker count hint.")

    watch_parser = subparsers.add_parser(
        "watch",
        help="Run incremental indexing loop with polling and optional sync.",
    )
    watch_parser.add_argument("--workers", type=int, default=4, help="Worker count hint.")
    watch_parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=1000,
        help="Polling interval for git changes in milliseconds.",
    )
    watch_parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Optional loop cycle cap for watch mode. 0 means run until interrupted.",
    )
    watch_parser.add_argument(
        "--watch-mode",
        choices=["auto", "poll", "fs"],
        default="auto",
        help="Watch transport: git diff polling, filesystem events, or automatic selection.",
    )
    watch_parser.add_argument(
        "--debounce-ms",
        type=int,
        default=250,
        help="Debounce window for filesystem event batching.",
    )
    watch_parser.add_argument(
        "--max-change-batch",
        type=int,
        default=500,
        help="Maximum number of changed files processed per watch cycle.",
    )

    workspace_init_parser = subparsers.add_parser(
        "workspace-init",
        help="Create or overwrite workspace root configuration.",
    )
    workspace_init_parser.add_argument(
        "--workspace-file",
        type=Path,
        default=None,
        help="Workspace config path. Defaults to <repo>/.bombe/workspace.json.",
    )
    workspace_init_parser.add_argument(
        "--name",
        type=str,
        default="workspace",
        help="Workspace name.",
    )
    workspace_init_parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Workspace root path. Repeat for multiple roots.",
    )

    workspace_status_parser = subparsers.add_parser(
        "workspace-status",
        help="Print aggregated status for all workspace roots.",
    )
    workspace_status_parser.add_argument(
        "--workspace-file",
        type=Path,
        default=None,
        help="Workspace config path. Defaults to <repo>/.bombe/workspace.json.",
    )
    workspace_status_parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include disabled roots in status output.",
    )

    workspace_index_parser = subparsers.add_parser(
        "workspace-index-full",
        help="Run full indexing for all enabled workspace roots.",
    )
    workspace_index_parser.add_argument(
        "--workspace-file",
        type=Path,
        default=None,
        help="Workspace config path. Defaults to <repo>/.bombe/workspace.json.",
    )
    workspace_index_parser.add_argument("--workers", type=int, default=4, help="Worker count hint.")
    workspace_index_parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include disabled roots in indexing output (still skipped for index execution).",
    )

    inspect_parser = subparsers.add_parser(
        "inspect-export",
        help="Export a read-only inspector bundle for the UI.",
    )
    inspect_parser.add_argument(
        "--output",
        type=Path,
        default=Path("ui") / "bundle.json",
        help="Output path for inspector JSON bundle.",
    )
    inspect_parser.add_argument("--node-limit", type=int, default=300, help="Maximum symbols to export.")
    inspect_parser.add_argument("--edge-limit", type=int, default=500, help="Maximum edges to export.")
    inspect_parser.add_argument(
        "--diagnostics-limit",
        type=int,
        default=50,
        help="Maximum diagnostics rows to export.",
    )

    subparsers.add_parser("status", help="Print local index status and exit.")
    diagnostics_parser = subparsers.add_parser(
        "diagnostics",
        help="Print parse/index diagnostics and exit.",
    )
    diagnostics_parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional diagnostics run id filter.",
    )
    diagnostics_parser.add_argument(
        "--stage",
        type=str,
        default=None,
        help="Optional diagnostics stage filter.",
    )
    diagnostics_parser.add_argument(
        "--severity",
        type=str,
        default=None,
        help="Optional diagnostics severity filter.",
    )
    doctor_parser = subparsers.add_parser("doctor", help="Run runtime and environment health checks.")
    doctor_parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe automatic repairs (schema sync, queue normalization, cache epoch bootstrap).",
    )
    parser.set_defaults(command="serve")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _stats_to_payload(stats: Any, mode: str, changed_files: list[FileChange] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "files_seen": int(stats.files_seen),
        "files_indexed": int(stats.files_indexed),
        "symbols_indexed": int(stats.symbols_indexed),
        "edges_indexed": int(stats.edges_indexed),
        "elapsed_ms": int(stats.elapsed_ms),
    }
    if changed_files is not None:
        payload["changed_files"] = [
            {"status": change.status, "path": change.path, "old_path": change.old_path}
            for change in changed_files
        ]
    run_id = getattr(stats, "run_id", None)
    if run_id:
        payload["run_id"] = str(run_id)
    diagnostics_summary = getattr(stats, "diagnostics_summary", None)
    if isinstance(diagnostics_summary, dict):
        payload["diagnostics"] = diagnostics_summary
    indexing_telemetry = getattr(stats, "indexing_telemetry", None)
    if isinstance(indexing_telemetry, dict):
        payload["indexing_telemetry"] = indexing_telemetry
    progress_snapshots = getattr(stats, "progress_snapshots", None)
    if isinstance(progress_snapshots, list):
        payload["progress"] = progress_snapshots
    return payload


def _pattern_list(raw_patterns: Any) -> list[str]:
    if not isinstance(raw_patterns, list):
        return []
    normalized: list[str] = []
    for pattern in raw_patterns:
        text = str(pattern).strip()
        if text:
            normalized.append(text)
    return normalized


def _matches_change_pattern(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/")
    return fnmatch(normalized, pattern) or fnmatch(Path(normalized).name, pattern)


def _filter_changes(
    changes: list[FileChange],
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> list[FileChange]:
    filtered: list[FileChange] = []
    for change in changes:
        path = change.path
        if include_patterns and not any(
            _matches_change_pattern(path, pattern) for pattern in include_patterns
        ):
            continue
        if exclude_patterns and any(
            _matches_change_pattern(path, pattern) for pattern in exclude_patterns
        ):
            continue
        filtered.append(change)
    return filtered


def _run_full_index(
    repo_root: Path,
    db: Database,
    workers: int,
    args: argparse.Namespace | None = None,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    include_patterns = _pattern_list(getattr(args, "include", [])) if args is not None else []
    exclude_patterns = _pattern_list(getattr(args, "exclude", [])) if args is not None else []
    effective_workers = max(1, workers)
    if plugin_manager is not None:
        plugin_payload = plugin_manager.before_index(
            "full",
            {
                "repo_root": repo_root.as_posix(),
                "workers": effective_workers,
                "include_patterns": include_patterns,
                "exclude_patterns": exclude_patterns,
            },
        )
        if isinstance(plugin_payload, dict):
            try:
                effective_workers = max(1, int(plugin_payload.get("workers", effective_workers)))
            except (TypeError, ValueError):
                effective_workers = max(1, workers)
    stats = full_index(
        repo_root,
        db,
        workers=effective_workers,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    payload = _stats_to_payload(stats, mode="full")
    if plugin_manager is not None:
        plugin_manager.after_index("full", payload, error=None)
    logging.getLogger(__name__).info(
        "Full index complete: files_indexed=%d symbols=%d edges=%d elapsed_ms=%d",
        payload["files_indexed"],
        payload["symbols_indexed"],
        payload["edges_indexed"],
        payload["elapsed_ms"],
    )
    return payload


def _run_incremental_index(
    repo_root: Path,
    db: Database,
    workers: int,
    changes: list[FileChange] | None = None,
    args: argparse.Namespace | None = None,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    include_patterns = _pattern_list(getattr(args, "include", [])) if args is not None else []
    exclude_patterns = _pattern_list(getattr(args, "exclude", [])) if args is not None else []
    if changes is None:
        resolved_changes = get_changed_files(
            repo_root,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )
    else:
        resolved_changes = _filter_changes(changes, include_patterns, exclude_patterns)
    if resolved_changes:
        logging.getLogger(__name__).info(
            "Detected file changes before incremental index: %s",
            json.dumps(
                [
                    {"status": change.status, "path": change.path, "old_path": change.old_path}
                    for change in resolved_changes
                ],
                sort_keys=True,
            ),
        )
    effective_workers = max(1, workers)
    if plugin_manager is not None:
        plugin_payload = plugin_manager.before_index(
            "incremental",
            {
                "repo_root": repo_root.as_posix(),
                "workers": effective_workers,
                "changes": [
                    {"status": change.status, "path": change.path, "old_path": change.old_path}
                    for change in resolved_changes
                ],
                "include_patterns": include_patterns,
                "exclude_patterns": exclude_patterns,
            },
        )
        if isinstance(plugin_payload, dict):
            try:
                effective_workers = max(1, int(plugin_payload.get("workers", effective_workers)))
            except (TypeError, ValueError):
                effective_workers = max(1, workers)
    stats = incremental_index(repo_root, db, resolved_changes, workers=effective_workers)
    payload = _stats_to_payload(stats, mode="incremental", changed_files=resolved_changes)
    if plugin_manager is not None:
        plugin_manager.after_index("incremental", payload, error=None)
    logging.getLogger(__name__).info(
        "Incremental index complete: changed=%d files_indexed=%d symbols=%d edges=%d elapsed_ms=%d",
        len(resolved_changes),
        payload["files_indexed"],
        payload["symbols_indexed"],
        payload["edges_indexed"],
        payload["elapsed_ms"],
    )
    return payload


def _all_file_changes(db: Database) -> list[FileChange]:
    rows = db.query("SELECT path FROM files ORDER BY path;")
    return [FileChange(status="M", path=str(row["path"])) for row in rows]


def _run_hybrid_sync(
    repo_root: Path,
    db: Database,
    args: argparse.Namespace,
    changes: list[FileChange],
) -> dict[str, Any] | None:
    if not bool(getattr(args, "hybrid_sync", False)):
        return None
    timeout_ms = max(1, int(getattr(args, "sync_timeout_ms", 500)))
    control_plane_root = getattr(args, "control_plane_root", None)
    control_plane_url = getattr(args, "control_plane_url", None)
    if isinstance(control_plane_url, str) and control_plane_url.strip():
        transport = HttpControlPlaneTransport(control_plane_url.strip())
        control_plane_descriptor = control_plane_url.strip()
    else:
        if control_plane_root is None:
            control_plane_root = repo_root / ".bombe" / "control-plane"
        transport = FileControlPlaneTransport(Path(control_plane_root))
        control_plane_descriptor = Path(control_plane_root).expanduser().resolve().as_posix()
    report = run_sync_cycle(
        repo_root=repo_root,
        db=db,
        transport=transport,
        changes=changes,
        timeout_seconds=timeout_ms / 1000.0,
    )
    payload = {
        "repo_id": report.repo_id,
        "snapshot_id": report.snapshot_id,
        "parent_snapshot": report.parent_snapshot,
        "queue_id": report.queue_id,
        "push": report.push,
        "pull": report.pull,
        "pinned_artifact_id": report.pinned_artifact_id,
        "control_plane": control_plane_descriptor,
    }
    logging.getLogger(__name__).info(
        "Hybrid sync complete: queue_id=%d push=%s pull=%s pinned=%s",
        report.queue_id,
        report.push["reason"],
        report.pull["reason"],
        report.pinned_artifact_id,
    )
    return payload


def _resolve_workspace_file(repo_root: Path, workspace_file: Path | None) -> Path:
    if workspace_file is None:
        return default_workspace_file(repo_root)
    if workspace_file.is_absolute():
        return workspace_file.expanduser().resolve()
    return (repo_root / workspace_file).expanduser().resolve()


def _workspace_status_payload(
    repo_root: Path,
    workspace_file: Path,
    diagnostics_limit: int,
    include_disabled: bool = False,
) -> dict[str, Any]:
    config = load_workspace_config(repo_root, workspace_file=workspace_file)
    root_entries = config.roots if include_disabled else enabled_workspace_roots(config)
    results: list[dict[str, Any]] = []
    totals = {"roots": 0, "files": 0, "symbols": 0, "edges": 0, "diagnostics_errors": 0}
    for root in root_entries:
        root_path = Path(root.path)
        db_path = Path(root.db_path)
        entry: dict[str, Any] = {
            "root_id": root.id,
            "root_path": root.path,
            "db_path": root.db_path,
            "enabled": bool(root.enabled),
        }
        if not root_path.exists() or not root_path.is_dir():
            entry["status"] = "missing_root"
            results.append(entry)
            continue
        try:
            db = Database(db_path)
            db.init_schema()
            status_payload = _status_payload(db, root_path, diagnostics_limit=diagnostics_limit)
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            results.append(entry)
            continue
        entry["status"] = "ok"
        entry["counts"] = status_payload.get("counts", {})
        entry["indexing_diagnostics_summary"] = status_payload.get("indexing_diagnostics_summary", {})
        results.append(entry)
        counts = status_payload.get("counts", {})
        totals["roots"] += 1
        totals["files"] += int(counts.get("files", 0))
        totals["symbols"] += int(counts.get("symbols", 0))
        totals["edges"] += int(counts.get("edges", 0))
        totals["diagnostics_errors"] += int(counts.get("indexing_diagnostics_errors", 0))
    return {
        "workspace_file": workspace_file.as_posix(),
        "workspace_name": config.name,
        "workspace_version": int(config.version),
        "root_count": len(root_entries),
        "totals": totals,
        "roots": results,
    }


def _run_workspace_full_index(
    repo_root: Path,
    workspace_file: Path,
    workers: int,
    args: argparse.Namespace,
    include_disabled: bool = False,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    config = load_workspace_config(repo_root, workspace_file=workspace_file)
    root_entries = config.roots if include_disabled else enabled_workspace_roots(config)
    runs: list[dict[str, Any]] = []
    totals = {
        "roots_indexed": 0,
        "roots_skipped": 0,
        "files_indexed": 0,
        "symbols_indexed": 0,
        "edges_indexed": 0,
        "elapsed_ms": 0,
    }
    for root in root_entries:
        root_path = Path(root.path)
        entry: dict[str, Any] = {
            "root_id": root.id,
            "root_path": root.path,
            "db_path": root.db_path,
            "enabled": bool(root.enabled),
        }
        if not bool(root.enabled):
            entry["status"] = "skipped_disabled"
            totals["roots_skipped"] += 1
            runs.append(entry)
            continue
        if not root_path.exists() or not root_path.is_dir():
            entry["status"] = "missing_root"
            totals["roots_skipped"] += 1
            runs.append(entry)
            continue
        started = time.perf_counter()
        try:
            root_db = Database(Path(root.db_path))
            root_db.init_schema()
            index_payload = _run_full_index(
                repo_root=root_path,
                db=root_db,
                workers=workers,
                args=args,
                plugin_manager=plugin_manager,
            )
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            totals["roots_skipped"] += 1
            runs.append(entry)
            continue
        entry["status"] = "indexed"
        entry["index"] = index_payload
        entry["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        runs.append(entry)
        totals["roots_indexed"] += 1
        totals["files_indexed"] += int(index_payload.get("files_indexed", 0))
        totals["symbols_indexed"] += int(index_payload.get("symbols_indexed", 0))
        totals["edges_indexed"] += int(index_payload.get("edges_indexed", 0))
        totals["elapsed_ms"] += int(entry["elapsed_ms"])
    return {
        "workspace_file": workspace_file.as_posix(),
        "workspace_name": config.name,
        "workspace_version": int(config.version),
        "roots": runs,
        "totals": totals,
    }


def _tool_metrics_summary(db: Database, limit: int = 200) -> dict[str, Any]:
    rows = db.query(
        """
        SELECT latency_ms, success, mode
        FROM tool_metrics
        ORDER BY id DESC
        LIMIT ?;
        """,
        (max(1, limit),),
    )
    latencies = sorted(float(row["latency_ms"]) for row in rows)
    total_calls = len(rows)
    success_calls = sum(1 for row in rows if int(row["success"]) == 1)
    failure_calls = total_calls - success_calls
    by_mode: dict[str, int] = {}
    for row in rows:
        mode = str(row["mode"])
        by_mode[mode] = by_mode.get(mode, 0) + 1
    p50_latency_ms = 0.0
    p95_latency_ms = 0.0
    if latencies:
        p50_index = min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.5)))
        p95_index = min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.95)))
        p50_latency_ms = round(latencies[p50_index], 3)
        p95_latency_ms = round(latencies[p95_index], 3)
    return {
        "window_size": total_calls,
        "total_calls": total_calls,
        "success_calls": success_calls,
        "failure_calls": failure_calls,
        "success_rate": round(success_calls / max(1, total_calls), 4),
        "by_mode": by_mode,
        "p50_latency_ms": p50_latency_ms,
        "p95_latency_ms": p95_latency_ms,
    }


def _status_payload(
    db: Database,
    repo_root: Path,
    diagnostics_limit: int = 50,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    schema_row = db.query("SELECT value FROM repo_meta WHERE key = 'schema_version';")
    schema_version = int(schema_row[0]["value"]) if schema_row else 0
    file_rows = db.query("SELECT COUNT(*) AS count FROM files;")
    symbol_rows = db.query("SELECT COUNT(*) AS count FROM symbols;")
    edge_rows = db.query("SELECT COUNT(*) AS count FROM edges;")
    queued_rows = db.query(
        "SELECT COUNT(*) AS count FROM sync_queue WHERE status IN ('queued', 'retry');"
    )
    pin_rows = db.query("SELECT COUNT(*) AS count FROM artifact_pins;")
    quarantine_rows = db.query("SELECT COUNT(*) AS count FROM artifact_quarantine;")
    latest_files = db.query(
        """
        SELECT path, content_hash, last_indexed_at
        FROM files
        ORDER BY last_indexed_at DESC
        LIMIT 5;
        """
    )
    latest = [
        {
            "path": row["path"],
            "content_hash": row["content_hash"],
            "last_indexed_at": row["last_indexed_at"],
        }
        for row in latest_files
    ]
    diagnostics_limit = max(1, diagnostics_limit)
    diagnostics_summary = db.summarize_indexing_diagnostics()
    recent_diagnostics = db.list_indexing_diagnostics(limit=diagnostics_limit)
    tool_metrics_summary = _tool_metrics_summary(db)
    error_count = int(diagnostics_summary.get("by_severity", {}).get("error", 0))
    return {
        "repo_root": repo_root.as_posix(),
        "db_path": db.db_path.as_posix(),
        "schema_version": schema_version,
        "counts": {
            "files": int(file_rows[0]["count"]) if file_rows else 0,
            "symbols": int(symbol_rows[0]["count"]) if symbol_rows else 0,
            "edges": int(edge_rows[0]["count"]) if edge_rows else 0,
            "sync_queue_pending": int(queued_rows[0]["count"]) if queued_rows else 0,
            "artifact_pins": int(pin_rows[0]["count"]) if pin_rows else 0,
            "artifact_quarantine": int(quarantine_rows[0]["count"]) if quarantine_rows else 0,
            "indexing_diagnostics_total": int(diagnostics_summary.get("total", 0)),
            "indexing_diagnostics_errors": error_count,
        },
        "latest_indexed_files": latest,
        "indexing_diagnostics_summary": diagnostics_summary,
        "recent_indexing_diagnostics": recent_diagnostics,
        "tool_metrics_summary": tool_metrics_summary,
        "plugin_manager": plugin_manager.stats() if plugin_manager is not None else {"plugins_loaded": 0},
        "recent_tool_metrics": db.query(
            """
            SELECT tool_name, latency_ms, success, mode, result_size, error_message, created_at
            FROM tool_metrics
            ORDER BY id DESC
            LIMIT 20;
            """
        ),
        "latest_pins": db.query(
            """
            SELECT repo_id, snapshot_id, artifact_id, pinned_at
            FROM artifact_pins
            ORDER BY pinned_at DESC
            LIMIT 5;
            """
        ),
        "circuit_breakers": db.query(
            """
            SELECT repo_id, state, failure_count, opened_at_utc
            FROM circuit_breakers
            ORDER BY repo_id ASC;
            """
        ),
    }


def _is_path_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    probe = directory / ".bombe-write-check"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _apply_runtime_profile(runtime_profile: str) -> None:
    if runtime_profile == "strict":
        os.environ["BOMBE_REQUIRE_TREE_SITTER"] = "1"
        return
    os.environ.pop("BOMBE_REQUIRE_TREE_SITTER", None)


def _preflight_payload(repo_root: Path, db_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    runtime_profile = str(getattr(args, "runtime_profile", "default"))
    checks: list[dict[str, Any]] = []
    error_codes: list[str] = []

    db_writable = _is_path_writable(db_path.parent)
    checks.append(
        {
            "code": "db_directory_unwritable",
            "name": "db_directory_writable",
            "status": "ok" if db_writable else "error",
            "detail": {"path": db_path.parent.as_posix()},
        }
    )
    if not db_writable:
        error_codes.append("db_directory_unwritable")

    control_plane_url = getattr(args, "control_plane_url", None)
    if isinstance(control_plane_url, str) and control_plane_url.strip():
        checks.append(
            {
                "code": "control_plane_http_configured",
                "name": "control_plane_http",
                "status": "ok",
                "detail": {"url": control_plane_url.strip()},
            }
        )
    else:
        control_plane_root = getattr(args, "control_plane_root", None)
        if control_plane_root is None:
            control_plane_root = repo_root / ".bombe" / "control-plane"
        control_plane_writable = _is_path_writable(Path(control_plane_root))
        checks.append(
            {
                "code": "control_plane_unwritable",
                "name": "control_plane_writable",
                "status": "ok" if control_plane_writable else "degraded",
                "detail": {"path": Path(control_plane_root).as_posix()},
            }
        )

    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except Exception:
        checks.append(
            {
                "code": "mcp_runtime_unavailable",
                "name": "mcp_runtime",
                "status": "degraded",
                "detail": {"available": False},
            }
        )
    else:
        _ = FastMCP
        checks.append(
            {
                "code": "mcp_runtime_unavailable",
                "name": "mcp_runtime",
                "status": "ok",
                "detail": {"available": True},
            }
        )

    capability = tree_sitter_capability_report()
    missing_languages = [
        str(item["language"])
        for item in capability.get("languages", [])
        if not bool(item.get("available"))
    ]
    strict_ready = not (runtime_profile == "strict" and missing_languages)
    if runtime_profile == "strict":
        status = "ok" if strict_ready and capability.get("all_required_available", False) else "error"
    else:
        status = "ok" if capability.get("all_required_available", False) else "degraded"
    checks.append(
        {
            "code": "tree_sitter_required_language_missing",
            "name": "tree_sitter_capabilities",
            "status": status,
            "detail": {
                "runtime_profile": runtime_profile,
                "all_required_available": bool(capability.get("all_required_available", False)),
                "missing_required_languages": missing_languages,
                "required_languages": capability.get("required_languages", []),
                "versions": capability.get("versions", {}),
            },
        }
    )
    if status == "error":
        error_codes.append("tree_sitter_required_language_missing")

    overall_status = "ok"
    if any(check["status"] == "error" for check in checks):
        overall_status = "error"
    elif any(check["status"] == "degraded" for check in checks):
        overall_status = "degraded"

    return {
        "status": overall_status,
        "runtime_profile": runtime_profile,
        "repo_root": repo_root.as_posix(),
        "db_path": db_path.as_posix(),
        "checks": checks,
        "error_codes": error_codes,
    }


def _doctor_payload(
    db: Database,
    repo_root: Path,
    args: argparse.Namespace,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    fixes_applied: list[dict[str, Any]] = []
    runtime_profile = str(getattr(args, "runtime_profile", "default"))
    if bool(getattr(args, "fix", False)):
        db.init_schema()
        normalized_rows = db.normalize_sync_queue_statuses()
        cache_epoch = db.get_cache_epoch()
        fixes_applied.append(
            {
                "name": "normalize_sync_queue_statuses",
                "rows_fixed": normalized_rows,
            }
        )
        fixes_applied.append(
            {
                "name": "cache_epoch_bootstrap",
                "cache_epoch": cache_epoch,
            }
        )

    schema_row = db.query("SELECT value FROM repo_meta WHERE key = 'schema_version';")
    schema_version = int(schema_row[0]["value"]) if schema_row else 0
    checks.append(
        {
            "name": "schema_version",
            "status": "ok" if schema_version == SCHEMA_VERSION else "degraded",
            "detail": {
                "expected": SCHEMA_VERSION,
                "actual": schema_version,
            },
        }
    )

    db_writable = _is_path_writable(db.db_path.parent)
    checks.append(
        {
            "name": "db_directory_writable",
            "status": "ok" if db_writable else "degraded",
            "detail": {"path": db.db_path.parent.as_posix()},
        }
    )

    control_plane_url = getattr(args, "control_plane_url", None)
    if isinstance(control_plane_url, str) and control_plane_url.strip():
        checks.append(
            {
                "name": "control_plane_http",
                "status": "ok",
                "detail": {"url": control_plane_url.strip()},
            }
        )
    else:
        control_plane_root = getattr(args, "control_plane_root", None)
        if control_plane_root is None:
            control_plane_root = repo_root / ".bombe" / "control-plane"
        control_plane_writable = _is_path_writable(Path(control_plane_root))
        checks.append(
            {
                "name": "control_plane_writable",
                "status": "ok" if control_plane_writable else "degraded",
                "detail": {"path": Path(control_plane_root).as_posix()},
            }
        )

    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except Exception:
        checks.append(
            {
                "name": "mcp_runtime",
                "status": "degraded",
                "detail": {"available": False},
            }
        )
    else:
        _ = FastMCP
        checks.append(
            {
                "name": "mcp_runtime",
                "status": "ok",
                "detail": {"available": True},
            }
        )

    tool_registry = build_tool_registry(db, repo_root.as_posix(), plugin_manager=plugin_manager)
    checks.append(
        {
            "name": "tool_registry",
            "status": "ok" if len(tool_registry) >= 16 else "degraded",
            "detail": {"tool_count": len(tool_registry)},
        }
    )
    checks.append(
        {
            "name": "plugins",
            "status": "ok",
            "detail": plugin_manager.stats() if plugin_manager is not None else {"plugins_loaded": 0},
        }
    )

    semantic_status = backend_statuses()
    checks.append(
        {
            "name": "semantic_backends",
            "status": "ok" if any(bool(item.get("available")) for item in semantic_status) else "degraded",
            "detail": {"backends": semantic_status},
        }
    )
    lsp_enabled = os.getenv("BOMBE_ENABLE_LSP_HINTS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    lsp_available = any(bool(item.get("lsp_bridge_available", False)) for item in semantic_status)
    checks.append(
        {
            "name": "lsp_bridge",
            "status": "ok" if (not lsp_enabled or lsp_available) else "degraded",
            "detail": {
                "enabled": lsp_enabled,
                "available": lsp_available,
                "providers": [
                    {
                        "backend": str(item.get("backend")),
                        "available": bool(item.get("lsp_bridge_available", False)),
                        "executable": item.get("executable"),
                    }
                    for item in semantic_status
                ],
            },
        }
    )

    fs_available = _filesystem_events_available()
    checks.append(
        {
            "name": "filesystem_watch_events",
            "status": "ok" if fs_available else "degraded",
            "detail": {"available": fs_available},
        }
    )

    trusted_keys = db.list_trusted_signing_keys(repo_root.as_posix(), active_only=True)
    checks.append(
        {
            "name": "trusted_sync_keys",
            "status": "ok" if len(trusted_keys) > 0 else "degraded",
            "detail": {"active_keys": len(trusted_keys)},
        }
    )

    tree_sitter_capabilities = tree_sitter_capability_report()
    missing_required_languages = [
        str(item["language"])
        for item in tree_sitter_capabilities.get("languages", [])
        if not bool(item.get("available"))
    ]
    profile_ready = not (runtime_profile == "strict" and missing_required_languages)
    checks.append(
        {
            "name": "runtime_profile_readiness",
            "status": "ok" if profile_ready else "degraded",
            "detail": {
                "runtime_profile": runtime_profile,
                "missing_required_languages": missing_required_languages,
                "required_languages": tree_sitter_capabilities.get("required_languages", []),
                "versions": tree_sitter_capabilities.get("versions", {}),
            },
        }
    )

    diagnostics_limit = max(1, int(getattr(args, "diagnostics_limit", 50)))
    diagnostics_summary = db.summarize_indexing_diagnostics()
    diagnostics_errors = int(diagnostics_summary.get("by_severity", {}).get("error", 0))
    checks.append(
        {
            "name": "indexing_diagnostics",
            "status": "ok" if diagnostics_errors == 0 else "degraded",
            "detail": {
                "total": int(diagnostics_summary.get("total", 0)),
                "errors": diagnostics_errors,
                "latest_run_id": diagnostics_summary.get("latest_run_id"),
            },
        }
    )

    overall_status = "ok"
    if any(check["status"] != "ok" for check in checks):
        overall_status = "degraded"
    recommendations = []
    if overall_status != "ok":
        recommendations = [
            "Run bombe status to inspect local index and sync counters.",
            "If MCP runtime is unavailable, install runtime dependency before serving STDIO MCP.",
            "Ensure DB/control-plane directories are writable for indexing and sync state.",
        ]
    return {
        "status": overall_status,
        "runtime_profile": runtime_profile,
        "repo_root": repo_root.as_posix(),
        "db_path": db.db_path.as_posix(),
        "fixes_applied": fixes_applied,
        "checks": checks,
        "indexing_diagnostics_summary": diagnostics_summary,
        "recent_indexing_diagnostics": db.list_indexing_diagnostics(limit=diagnostics_limit),
        "recommendations": recommendations,
    }


def _filesystem_events_available() -> bool:
    try:
        from watchdog.events import FileSystemEventHandler  # type: ignore
        from watchdog.observers import Observer  # type: ignore
    except Exception:
        return False
    _ = (FileSystemEventHandler, Observer)
    return True


def _collect_fs_changes(
    repo_root: Path,
    interval_ms: int,
    debounce_ms: int,
    max_events: int,
) -> tuple[list[FileChange], bool]:
    try:
        from watchdog.events import FileSystemEventHandler  # type: ignore
        from watchdog.observers import Observer  # type: ignore
    except Exception:
        return [], False

    changed_paths: set[str] = set()
    overflow = False

    class _Collector(FileSystemEventHandler):  # type: ignore[misc]
        def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
            nonlocal overflow
            if overflow:
                return
            if getattr(event, "is_directory", False):
                return
            src_path = str(getattr(event, "src_path", "") or "")
            if not src_path:
                return
            try:
                rel = Path(src_path).resolve().relative_to(repo_root.resolve()).as_posix()
            except Exception:
                return
            changed_paths.add(rel)
            if len(changed_paths) >= max_events:
                overflow = True
                return

            dest_path = str(getattr(event, "dest_path", "") or "")
            if dest_path:
                try:
                    rel_dest = Path(dest_path).resolve().relative_to(repo_root.resolve()).as_posix()
                    changed_paths.add(rel_dest)
                    if len(changed_paths) >= max_events:
                        overflow = True
                except Exception:
                    return

    observer = Observer()
    handler = _Collector()
    observer.schedule(handler, repo_root.as_posix(), recursive=True)
    observer.start()
    try:
        time.sleep(max(0.05, interval_ms / 1000.0))
        if debounce_ms > 0:
            time.sleep(debounce_ms / 1000.0)
    finally:
        observer.stop()
        observer.join(timeout=2.0)

    changes: list[FileChange] = []
    for rel in sorted(changed_paths):
        absolute = repo_root / rel
        status = "M" if absolute.exists() else "D"
        changes.append(FileChange(status=status, path=rel))
    return changes, overflow


def _run_watch(
    repo_root: Path,
    db: Database,
    args: argparse.Namespace,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    poll_interval_ms = max(100, int(getattr(args, "poll_interval_ms", 1000)))
    max_cycles = max(0, int(getattr(args, "max_cycles", 0)))
    debounce_ms = max(0, int(getattr(args, "debounce_ms", 250)))
    workers = int(getattr(args, "workers", 4))
    max_change_batch = max(1, int(getattr(args, "max_change_batch", 500)))
    requested_mode = str(getattr(args, "watch_mode", "auto"))
    include_patterns = _pattern_list(getattr(args, "include", []))
    exclude_patterns = _pattern_list(getattr(args, "exclude", []))
    fs_available = _filesystem_events_available()
    if requested_mode == "poll":
        effective_mode = "poll"
    elif requested_mode == "fs":
        if not fs_available:
            raise RuntimeError(
                "Filesystem watch mode requested but watchdog filesystem events are unavailable."
            )
        effective_mode = "fs"
    else:
        effective_mode = "fs" if fs_available else "poll"

    cycles = 0
    runs = 0
    changed_files_total = 0
    files_indexed_total = 0
    truncated_cycles = 0
    overflow_cycles = 0
    last_index_payload: dict[str, Any] | None = None
    last_sync_payload: dict[str, Any] | None = None

    while True:
        cycles += 1
        if effective_mode == "fs":
            changes, overflow = _collect_fs_changes(
                repo_root=repo_root,
                interval_ms=poll_interval_ms,
                debounce_ms=debounce_ms,
                max_events=max_change_batch * 4,
            )
            if overflow:
                overflow_cycles += 1
        else:
            changes = get_changed_files(
                repo_root,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
            )
            overflow = False
        changes = _filter_changes(changes, include_patterns, exclude_patterns)
        if len(changes) > max_change_batch:
            truncated_cycles += 1
            dropped = len(changes) - max_change_batch
            logging.getLogger(__name__).warning(
                "Watch change burst exceeded batch limit; truncating %d files for this cycle.",
                dropped,
            )
            changes = changes[:max_change_batch]
        changed_files_total += len(changes)
        if changes:
            last_index_payload = _run_incremental_index(
                repo_root=repo_root,
                db=db,
                workers=workers,
                changes=changes,
                args=args,
                plugin_manager=plugin_manager,
            )
            runs += 1
            files_indexed_total += int(last_index_payload.get("files_indexed", 0))
            sync_payload = _run_hybrid_sync(
                repo_root=repo_root,
                db=db,
                args=args,
                changes=changes,
            )
            if sync_payload is not None:
                last_sync_payload = sync_payload
        if max_cycles > 0 and cycles >= max_cycles:
            break
        if effective_mode == "fs":
            continue
        try:
            time.sleep(poll_interval_ms / 1000.0)
        except KeyboardInterrupt:
            break

    return {
        "mode": "watch",
        "requested_watch_mode": requested_mode,
        "effective_watch_mode": effective_mode,
        "filesystem_events_available": fs_available,
        "cycles": cycles,
        "index_runs": runs,
        "changed_files_seen": changed_files_total,
        "files_indexed": files_indexed_total,
        "include_patterns": include_patterns,
        "exclude_patterns": exclude_patterns,
        "max_change_batch": max_change_batch,
        "truncated_cycles": truncated_cycles,
        "overflow_cycles": overflow_cycles,
        "poll_interval_ms": poll_interval_ms,
        "debounce_ms": debounce_ms,
        "last_index": last_index_payload,
        "last_sync": last_sync_payload,
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = build_settings(
        repo=args.repo,
        db_path=args.db_path,
        log_level=args.log_level,
        init_only=args.init_only,
        runtime_profile=str(getattr(args, "runtime_profile", "default")),
    )
    configure_logging(settings.log_level)
    _apply_runtime_profile(settings.runtime_profile)

    command = str(getattr(args, "command", "serve") or "serve")
    if command == "preflight":
        payload = _preflight_payload(settings.repo_root, settings.db_path, args)
        print(json.dumps(payload, sort_keys=True))
        if payload["status"] == "error":
            raise SystemExit(1)
        return

    if command == "workspace-init":
        workspace_file = _resolve_workspace_file(
            settings.repo_root,
            getattr(args, "workspace_file", None),
        )
        root_args = list(getattr(args, "root", []))
        roots = [
            (Path(item) if Path(item).is_absolute() else settings.repo_root / item)
            for item in root_args
        ] if root_args else [settings.repo_root]
        config = build_workspace_config(
            repo_root=settings.repo_root,
            roots=roots,
            name=str(getattr(args, "name", "workspace")),
        )
        saved = save_workspace_config(
            settings.repo_root,
            config=config,
            workspace_file=workspace_file,
        )
        payload = {
            "workspace_file": saved.as_posix(),
            "workspace_name": config.name,
            "workspace_version": int(config.version),
            "roots": [
                {
                    "id": root.id,
                    "path": root.path,
                    "db_path": root.db_path,
                    "enabled": bool(root.enabled),
                }
                for root in config.roots
            ],
        }
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "workspace-status":
        workspace_file = _resolve_workspace_file(
            settings.repo_root,
            getattr(args, "workspace_file", None),
        )
        payload = _workspace_status_payload(
            settings.repo_root,
            workspace_file=workspace_file,
            diagnostics_limit=max(1, int(getattr(args, "diagnostics_limit", 50))),
            include_disabled=bool(getattr(args, "include_disabled", False)),
        )
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "workspace-index-full":
        workspace_file = _resolve_workspace_file(
            settings.repo_root,
            getattr(args, "workspace_file", None),
        )
        plugin_manager = PluginManager.from_repo(settings.repo_root)
        payload = _run_workspace_full_index(
            repo_root=settings.repo_root,
            workspace_file=workspace_file,
            workers=max(1, int(getattr(args, "workers", 4))),
            args=args,
            include_disabled=bool(getattr(args, "include_disabled", False)),
            plugin_manager=plugin_manager,
        )
        print(json.dumps(payload, sort_keys=True))
        return

    plugin_manager = PluginManager.from_repo(settings.repo_root)
    db = Database(settings.db_path)
    db.init_schema()

    logging.getLogger(__name__).info(
        "Bombe server initialized (repo=%s, db=%s, init_only=%s)",
        settings.repo_root,
        settings.db_path,
        settings.init_only,
    )

    if settings.init_only:
        return

    if command == "index-full":
        payload = _run_full_index(
            settings.repo_root,
            db,
            int(getattr(args, "workers", 4)),
            args=args,
            plugin_manager=plugin_manager,
        )
        sync_payload = _run_hybrid_sync(settings.repo_root, db, args, changes=_all_file_changes(db))
        if sync_payload is not None:
            payload["sync"] = sync_payload
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "index-incremental":
        changes = get_changed_files(settings.repo_root)
        payload = _run_incremental_index(
            settings.repo_root,
            db,
            int(getattr(args, "workers", 4)),
            changes=changes,
            args=args,
            plugin_manager=plugin_manager,
        )
        sync_payload = _run_hybrid_sync(settings.repo_root, db, args, changes=changes)
        if sync_payload is not None:
            payload["sync"] = sync_payload
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "status":
        payload = _status_payload(
            db,
            settings.repo_root,
            diagnostics_limit=max(1, int(getattr(args, "diagnostics_limit", 50))),
            plugin_manager=plugin_manager,
        )
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "inspect-export":
        bundle = build_inspector_bundle(
            db,
            node_limit=max(1, int(getattr(args, "node_limit", 300))),
            edge_limit=max(1, int(getattr(args, "edge_limit", 500))),
            diagnostics_limit=max(1, int(getattr(args, "diagnostics_limit", 50))),
        )
        output_path_raw = getattr(args, "output", Path("ui") / "bundle.json")
        output_path = (
            output_path_raw
            if isinstance(output_path_raw, Path) and output_path_raw.is_absolute()
            else settings.repo_root / Path(str(output_path_raw))
        ).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(bundle, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        payload = {
            "output": output_path.as_posix(),
            "node_count": len(bundle.get("nodes", [])),
            "edge_count": len(bundle.get("edges", [])),
            "diagnostics_count": len(bundle.get("diagnostics", [])),
        }
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "diagnostics":
        diagnostics_limit = max(1, int(getattr(args, "diagnostics_limit", 50)))
        run_id_raw = getattr(args, "run_id", None)
        stage_raw = getattr(args, "stage", None)
        severity_raw = getattr(args, "severity", None)
        run_id = str(run_id_raw) if run_id_raw else None
        stage = str(stage_raw) if stage_raw else None
        severity = str(severity_raw) if severity_raw else None
        diagnostics = db.list_indexing_diagnostics(
            limit=diagnostics_limit,
            run_id=run_id,
            stage=stage,
            severity=severity,
        )
        payload = {
            "filters": {
                "run_id": run_id,
                "stage": stage,
                "severity": severity,
                "limit": diagnostics_limit,
            },
            "diagnostics": diagnostics,
            "count": len(diagnostics),
            "summary": db.summarize_indexing_diagnostics(run_id=run_id),
        }
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "doctor":
        payload = _doctor_payload(db, settings.repo_root, args, plugin_manager=plugin_manager)
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "watch":
        payload = _run_watch(settings.repo_root, db, args, plugin_manager=plugin_manager)
        print(json.dumps(payload, sort_keys=True))
        return

    if str(getattr(args, "index_mode", "none")) == "full":
        _run_full_index(
            settings.repo_root,
            db,
            workers=4,
            args=args,
            plugin_manager=plugin_manager,
        )
        _run_hybrid_sync(settings.repo_root, db, args, changes=_all_file_changes(db))
    elif str(getattr(args, "index_mode", "none")) == "incremental":
        serve_changes = get_changed_files(
            settings.repo_root,
            include_patterns=_pattern_list(getattr(args, "include", [])),
            exclude_patterns=_pattern_list(getattr(args, "exclude", [])),
        )
        _run_incremental_index(
            settings.repo_root,
            db,
            workers=4,
            changes=serve_changes,
            args=args,
            plugin_manager=plugin_manager,
        )
        _run_hybrid_sync(settings.repo_root, db, args, changes=serve_changes)

    class LocalServer:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def register_tool(
            self,
            name: str,
            description: str,
            input_schema_or_handler: object,
            handler: object | None = None,
        ) -> None:
            if handler is None:
                input_schema = None
                resolved_handler = input_schema_or_handler
            else:
                input_schema = input_schema_or_handler
                resolved_handler = handler
            self.tools[name] = {
                "description": description,
                "input_schema": input_schema,
                "handler": resolved_handler,
            }

    server = LocalServer()
    register_tools(
        server,
        db,
        settings.repo_root.as_posix(),
        plugin_manager=plugin_manager,
    )
    logging.getLogger(__name__).info(
        "Registered %d tool handlers.", len(server.tools)
    )

    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        logging.getLogger(__name__).info(
            "MCP runtime package unavailable; running in local registration mode."
        )
        return

    mcp_server = FastMCP("bombe")
    for tool_name, tool in server.tools.items():
        description = str(tool["description"])
        input_schema = tool.get("input_schema")
        handler = tool["handler"]
        if not callable(handler):
            continue

        if hasattr(mcp_server, "tool"):
            decorator = mcp_server.tool(name=tool_name, description=description)

            @decorator
            def _wrapped(payload: dict[str, Any], _handler=handler):
                return _handler(payload)
            continue

        if hasattr(mcp_server, "register_tool"):
            register_sig = inspect.signature(mcp_server.register_tool)
            if "input_schema" in register_sig.parameters:
                mcp_server.register_tool(
                    name=tool_name,
                    description=description,
                    input_schema=input_schema,
                    handler=handler,
                )
            else:
                mcp_server.register_tool(tool_name, description, handler)

    if hasattr(mcp_server, "run"):
        logging.getLogger(__name__).info("Starting MCP STDIO server runtime.")
        mcp_server.run()


if __name__ == "__main__":
    main()
