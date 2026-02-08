"""CLI entry point for the Bombe MCP server."""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import time
from pathlib import Path
from typing import Any

from bombe.indexer.pipeline import full_index, incremental_index
from bombe.indexer.semantic import backend_statuses
from bombe.models import FileChange
from bombe.sync.orchestrator import run_sync_cycle
from bombe.sync.transport import FileControlPlaneTransport
from bombe.watcher.git_diff import get_changed_files
from bombe.config import build_settings
from bombe.store.database import Database, SCHEMA_VERSION
from bombe.tools.definitions import build_tool_registry, register_tools


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
        "--sync-timeout-ms",
        type=int,
        default=500,
        help="Per-request timeout for sync operations in milliseconds.",
    )

    subparsers = parser.add_subparsers(dest="command")

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

    subparsers.add_parser("status", help="Print local index status and exit.")
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
    return payload


def _run_full_index(repo_root: Path, db: Database, workers: int) -> dict[str, Any]:
    stats = full_index(repo_root, db, workers=max(1, workers))
    payload = _stats_to_payload(stats, mode="full")
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
) -> dict[str, Any]:
    del workers
    resolved_changes = changes if changes is not None else get_changed_files(repo_root)
    stats = incremental_index(repo_root, db, resolved_changes)
    payload = _stats_to_payload(stats, mode="incremental", changed_files=resolved_changes)
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
    if control_plane_root is None:
        control_plane_root = repo_root / ".bombe" / "control-plane"
    transport = FileControlPlaneTransport(Path(control_plane_root))
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
        "control_plane_root": Path(control_plane_root).expanduser().resolve().as_posix(),
    }
    logging.getLogger(__name__).info(
        "Hybrid sync complete: queue_id=%d push=%s pull=%s pinned=%s",
        report.queue_id,
        report.push["reason"],
        report.pull["reason"],
        report.pinned_artifact_id,
    )
    return payload


def _status_payload(db: Database, repo_root: Path) -> dict[str, Any]:
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
        },
        "latest_indexed_files": latest,
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
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ".bombe-write-check"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _doctor_payload(db: Database, repo_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    fixes_applied: list[dict[str, Any]] = []
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

    tool_registry = build_tool_registry(db, repo_root.as_posix())
    checks.append(
        {
            "name": "tool_registry",
            "status": "ok" if len(tool_registry) >= 7 else "degraded",
            "detail": {"tool_count": len(tool_registry)},
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
        "repo_root": repo_root.as_posix(),
        "db_path": db.db_path.as_posix(),
        "fixes_applied": fixes_applied,
        "checks": checks,
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
) -> list[FileChange]:
    try:
        from watchdog.events import FileSystemEventHandler  # type: ignore
        from watchdog.observers import Observer  # type: ignore
    except Exception:
        return []

    changed_paths: set[str] = set()

    class _Collector(FileSystemEventHandler):  # type: ignore[misc]
        def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
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

            dest_path = str(getattr(event, "dest_path", "") or "")
            if dest_path:
                try:
                    rel_dest = Path(dest_path).resolve().relative_to(repo_root.resolve()).as_posix()
                    changed_paths.add(rel_dest)
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
    return changes


def _run_watch(
    repo_root: Path,
    db: Database,
    args: argparse.Namespace,
) -> dict[str, Any]:
    poll_interval_ms = max(100, int(getattr(args, "poll_interval_ms", 1000)))
    max_cycles = max(0, int(getattr(args, "max_cycles", 0)))
    debounce_ms = max(0, int(getattr(args, "debounce_ms", 250)))
    workers = int(getattr(args, "workers", 4))
    requested_mode = str(getattr(args, "watch_mode", "auto"))
    fs_available = _filesystem_events_available()
    if requested_mode == "poll":
        effective_mode = "poll"
    elif requested_mode == "fs":
        effective_mode = "fs" if fs_available else "poll"
    else:
        effective_mode = "fs" if fs_available else "poll"

    cycles = 0
    runs = 0
    changed_files_total = 0
    files_indexed_total = 0
    last_index_payload: dict[str, Any] | None = None
    last_sync_payload: dict[str, Any] | None = None

    while True:
        cycles += 1
        if effective_mode == "fs":
            changes = _collect_fs_changes(
                repo_root=repo_root,
                interval_ms=poll_interval_ms,
                debounce_ms=debounce_ms,
            )
        else:
            changes = get_changed_files(repo_root)
        changed_files_total += len(changes)
        if changes:
            last_index_payload = _run_incremental_index(
                repo_root=repo_root,
                db=db,
                workers=workers,
                changes=changes,
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
    )
    configure_logging(settings.log_level)
    db = Database(settings.db_path)
    db.init_schema()

    logging.getLogger(__name__).info(
        "Bombe server initialized (repo=%s, db=%s, init_only=%s)",
        settings.repo_root,
        settings.db_path,
        settings.init_only,
    )

    command = str(getattr(args, "command", "serve") or "serve")
    if settings.init_only:
        return

    if command == "index-full":
        payload = _run_full_index(settings.repo_root, db, int(getattr(args, "workers", 4)))
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
        )
        sync_payload = _run_hybrid_sync(settings.repo_root, db, args, changes=changes)
        if sync_payload is not None:
            payload["sync"] = sync_payload
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "status":
        payload = _status_payload(db, settings.repo_root)
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "doctor":
        payload = _doctor_payload(db, settings.repo_root, args)
        print(json.dumps(payload, sort_keys=True))
        return

    if command == "watch":
        payload = _run_watch(settings.repo_root, db, args)
        print(json.dumps(payload, sort_keys=True))
        return

    if str(getattr(args, "index_mode", "none")) == "full":
        _run_full_index(settings.repo_root, db, workers=4)
        _run_hybrid_sync(settings.repo_root, db, args, changes=_all_file_changes(db))
    elif str(getattr(args, "index_mode", "none")) == "incremental":
        serve_changes = get_changed_files(settings.repo_root)
        _run_incremental_index(settings.repo_root, db, workers=4, changes=serve_changes)
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
    register_tools(server, db, settings.repo_root.as_posix())
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
