"""Hybrid sync orchestration between local index and control-plane artifacts."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bombe import __version__
from bombe.models import DELTA_SCHEMA_VERSION, DeltaHeader, FileChange, FileDelta, IndexDelta, QualityStats
from bombe.models import EdgeContractRecord, SymbolKey
from bombe.store.database import Database
from bombe.sync.client import ArtifactQuarantineStore, CircuitBreaker, CompatibilityPolicy, SyncClient
from bombe.sync.reconcile import reconcile_artifact


@dataclass(frozen=True)
class SyncCycleReport:
    repo_id: str
    snapshot_id: str
    parent_snapshot: str | None
    queue_id: int
    push: dict[str, Any]
    pull: dict[str, Any]
    pinned_artifact_id: str | None


def _git_rev_parse(repo_root: Path, revision: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "rev-parse", revision],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _snapshot_lineage(repo_root: Path) -> tuple[str, str | None]:
    head = _git_rev_parse(repo_root, "HEAD")
    parent = _git_rev_parse(repo_root, "HEAD^")
    if head:
        return head, parent
    fallback = f"local-{int(datetime.now(timezone.utc).timestamp())}"
    return fallback, None


def _repo_id(repo_root: Path) -> str:
    return repo_root.expanduser().resolve().as_posix()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_deltas(db: Database, changes: list[FileChange]) -> list[FileDelta]:
    file_deltas: list[FileDelta] = []
    for change in changes:
        rows = db.query(
            "SELECT content_hash, size_bytes FROM files WHERE path = ? LIMIT 1;",
            (change.path,),
        )
        content_hash = str(rows[0]["content_hash"]) if rows else None
        size_bytes = int(rows[0]["size_bytes"]) if rows and rows[0]["size_bytes"] is not None else None
        file_deltas.append(
            FileDelta(
                status=change.status,
                path=change.path,
                old_path=change.old_path,
                content_hash=content_hash,
                size_bytes=size_bytes,
            )
        )
    return file_deltas


def _symbols_for_paths(db: Database, file_paths: list[str]) -> list[Any]:
    if not file_paths:
        return []
    placeholders = ", ".join("?" for _ in file_paths)
    return db.query(
        f"""
        SELECT name, qualified_name, kind, file_path, start_line, end_line, signature, return_type,
               visibility, is_async, is_static, parent_symbol_id, docstring, pagerank_score
        FROM symbols
        WHERE file_path IN ({placeholders});
        """,
        tuple(file_paths),
    )


def _edges_for_paths(db: Database, file_paths: list[str]) -> list[Any]:
    if not file_paths:
        return []
    placeholders = ", ".join("?" for _ in file_paths)
    return db.query(
        f"""
        SELECT
            e.relationship,
            e.line_number,
            e.confidence,
            src.qualified_name AS src_qn,
            src.file_path AS src_file,
            src.start_line AS src_start,
            src.end_line AS src_end,
            src.signature AS src_sig,
            dst.qualified_name AS dst_qn,
            dst.file_path AS dst_file,
            dst.start_line AS dst_start,
            dst.end_line AS dst_end,
            dst.signature AS dst_sig
        FROM edges e
        JOIN symbols src ON src.id = e.source_id
        JOIN symbols dst ON dst.id = e.target_id
        WHERE e.file_path IN ({placeholders})
          AND e.source_type = 'symbol'
          AND e.target_type = 'symbol';
        """,
        tuple(file_paths),
    )


def _build_delta(repo_root: Path, db: Database, changes: list[FileChange]) -> IndexDelta:
    repo_identifier = _repo_id(repo_root)
    snapshot_id, parent_snapshot = _snapshot_lineage(repo_root)
    header = DeltaHeader(
        repo_id=repo_identifier,
        parent_snapshot=parent_snapshot,
        local_snapshot=snapshot_id,
        tool_version=__version__,
        schema_version=DELTA_SCHEMA_VERSION,
        created_at_utc=_now_utc(),
    )

    file_deltas = _file_deltas(db, changes)
    changed_paths = sorted({change.path for change in changes if change.status in {"A", "M", "R"}})

    symbol_rows = _symbols_for_paths(db, changed_paths)
    symbol_upserts = []
    for row in symbol_rows:
        symbol_upserts.append(
            {
                "name": row["name"],
                "qualified_name": row["qualified_name"],
                "kind": row["kind"],
                "file_path": row["file_path"],
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "signature": row["signature"],
                "return_type": row["return_type"],
                "visibility": row["visibility"],
                "is_async": bool(row["is_async"]),
                "is_static": bool(row["is_static"]),
                "parent_symbol_id": row["parent_symbol_id"],
                "docstring": row["docstring"],
                "pagerank_score": float(row["pagerank_score"] or 0.0),
                "parameters": [],
            }
        )

    edge_rows = _edges_for_paths(db, changed_paths)
    edge_upserts: list[EdgeContractRecord] = []
    ambiguous_count = 0
    for row in edge_rows:
        confidence = float(row["confidence"] or 0.0)
        if confidence < 1.0:
            ambiguous_count += 1
        edge_upserts.append(
            EdgeContractRecord(
                source=SymbolKey.from_fields(
                    qualified_name=str(row["src_qn"]),
                    file_path=str(row["src_file"]),
                    start_line=int(row["src_start"]),
                    end_line=int(row["src_end"]),
                    signature=row["src_sig"],
                ),
                target=SymbolKey.from_fields(
                    qualified_name=str(row["dst_qn"]),
                    file_path=str(row["dst_file"]),
                    start_line=int(row["dst_start"]),
                    end_line=int(row["dst_end"]),
                    signature=row["dst_sig"],
                ),
                relationship=str(row["relationship"]),
                line_number=int(row["line_number"]) if row["line_number"] is not None else 0,
                confidence=confidence,
            )
        )
    unresolved_rows = db.query(
        f"""
        SELECT COUNT(*) AS count
        FROM external_deps
        WHERE file_path IN ({", ".join("?" for _ in changed_paths)}) ;
        """,
        tuple(changed_paths),
    ) if changed_paths else [{"count": 0}]
    unresolved_imports = int(unresolved_rows[0]["count"]) if unresolved_rows else 0
    quality_stats = QualityStats(
        ambiguity_rate=(ambiguous_count / max(1, len(edge_upserts))),
        unresolved_imports=unresolved_imports,
        parse_failures=0,
    )

    from bombe.models import SymbolRecord  # delayed to keep top imports compact

    symbol_records = [SymbolRecord(**payload) for payload in symbol_upserts]
    return IndexDelta(
        header=header,
        file_changes=file_deltas,
        symbol_upserts=symbol_records,
        edge_upserts=edge_upserts,
        quality_stats=quality_stats,
    )


def run_sync_cycle(
    repo_root: Path,
    db: Database,
    transport: Any,
    changes: list[FileChange],
    timeout_seconds: float = 0.5,
    signing_key: str | None = None,
) -> SyncCycleReport:
    delta = _build_delta(repo_root, db, changes)
    repo_identifier = delta.header.repo_id
    breaker_state = db.get_circuit_breaker_state(repo_identifier)
    if breaker_state is None:
        breaker = CircuitBreaker()
    else:
        breaker = CircuitBreaker.from_persisted(
            state=str(breaker_state["state"]),
            failure_count=int(breaker_state["failure_count"]),
            opened_at_utc=(
                str(breaker_state["opened_at_utc"]) if breaker_state["opened_at_utc"] else None
            ),
        )

    quarantine_store = ArtifactQuarantineStore()
    for row in db.list_quarantined_artifacts(limit=500):
        quarantine_store.preload(
            artifact_id=str(row["artifact_id"]),
            reason=str(row["reason"]),
            quarantined_at_utc=(
                str(row["quarantined_at"]) if row["quarantined_at"] is not None else None
            ),
        )

    policy = CompatibilityPolicy(tool_version=__version__)
    resolved_signing_key = signing_key or os.getenv("BOMBE_SYNC_SIGNING_KEY")
    client = SyncClient(
        transport=transport,
        policy=policy,
        timeout_seconds=max(0.01, timeout_seconds),
        circuit_breaker=breaker,
        quarantine_store=quarantine_store,
        signing_key=resolved_signing_key,
    )
    try:
        queue_payload = json.dumps(asdict(delta), sort_keys=True)
        queue_id = db.enqueue_sync_delta(repo_identifier, delta.header.local_snapshot, queue_payload)
        push_result = client.push_delta(delta)
        db.mark_sync_delta_status(
            queue_id,
            status="pushed" if push_result.ok else "retry",
            last_error=None if push_result.ok else push_result.reason,
        )
        db.record_sync_event(
            repo_identifier,
            level="INFO" if push_result.ok else "WARNING",
            event_type="sync_push",
            detail={"mode": push_result.mode, "reason": push_result.reason},
        )

        pull_result = client.pull_artifact(
            repo_id=repo_identifier,
            snapshot_id=delta.header.local_snapshot,
            parent_snapshot=delta.header.parent_snapshot,
        )
        pinned_artifact_id: str | None = None
        if pull_result.artifact is not None:
            merged = reconcile_artifact(delta, pull_result.artifact)
            db.set_artifact_pin(repo_identifier, merged.snapshot_id, merged.artifact_id)
            pinned_artifact_id = merged.artifact_id
            db.record_sync_event(
                repo_identifier,
                level="INFO",
                event_type="artifact_pinned",
                detail={"artifact_id": merged.artifact_id, "snapshot_id": merged.snapshot_id},
            )
        else:
            db.record_sync_event(
                repo_identifier,
                level="WARNING",
                event_type="sync_pull_fallback",
                detail={"mode": pull_result.mode, "reason": pull_result.reason},
            )

        if pull_result.reason == "checksum_mismatch":
            artifact_id = str(pull_result.detail.get("artifact_id", ""))
            if artifact_id:
                db.quarantine_artifact(artifact_id, reason="checksum_mismatch")

        for record in quarantine_store.records():
            db.quarantine_artifact(record.artifact_id, record.reason)

        breaker_snapshot = client.circuit_state()
        db.set_circuit_breaker_state(
            repo_id=repo_identifier,
            state=str(breaker_snapshot["state"]),
            failure_count=int(breaker_snapshot["failure_count"]),
            opened_at_utc=(
                str(breaker_snapshot["opened_at_utc"])
                if breaker_snapshot.get("opened_at_utc") is not None
                else None
            ),
        )
        return SyncCycleReport(
            repo_id=repo_identifier,
            snapshot_id=delta.header.local_snapshot,
            parent_snapshot=delta.header.parent_snapshot,
            queue_id=queue_id,
            push={"ok": push_result.ok, "mode": push_result.mode, "reason": push_result.reason},
            pull={"mode": pull_result.mode, "reason": pull_result.reason},
            pinned_artifact_id=pinned_artifact_id,
        )
    finally:
        client.close()
