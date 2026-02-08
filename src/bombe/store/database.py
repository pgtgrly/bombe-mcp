"""SQLite storage layer for Bombe."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence

from bombe.models import EdgeRecord, ExternalDepRecord, FileRecord, SymbolRecord


SCHEMA_VERSION = 4

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS repo_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        language TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        size_bytes INTEGER,
        last_indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS symbols (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        file_path TEXT NOT NULL REFERENCES files(path),
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        signature TEXT,
        return_type TEXT,
        visibility TEXT,
        is_async BOOLEAN DEFAULT FALSE,
        is_static BOOLEAN DEFAULT FALSE,
        parent_symbol_id INTEGER REFERENCES symbols(id),
        docstring TEXT,
        pagerank_score REAL DEFAULT 0.0,
        UNIQUE(qualified_name, file_path)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS parameters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol_id INTEGER NOT NULL REFERENCES symbols(id),
        name TEXT NOT NULL,
        type TEXT,
        position INTEGER NOT NULL,
        default_value TEXT,
        UNIQUE(symbol_id, position)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        source_type TEXT NOT NULL,
        target_type TEXT NOT NULL,
        relationship TEXT NOT NULL,
        file_path TEXT,
        line_number INTEGER,
        confidence REAL DEFAULT 1.0,
        UNIQUE(source_id, target_id, source_type, target_type, relationship)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS external_deps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL REFERENCES files(path),
        import_statement TEXT NOT NULL,
        module_name TEXT NOT NULL,
        line_number INTEGER
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS migration_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_version INTEGER NOT NULL,
        to_version INTEGER NOT NULL,
        status TEXT NOT NULL,
        error_message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id TEXT NOT NULL,
        local_snapshot TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_quarantine (
        artifact_id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        quarantined_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_pins (
        repo_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        pinned_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(repo_id, snapshot_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS circuit_breakers (
        repo_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        failure_count INTEGER NOT NULL DEFAULT 0,
        opened_at_utc TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id TEXT NOT NULL,
        level TEXT NOT NULL,
        event_type TEXT NOT NULL,
        detail_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id TEXT,
        tool_name TEXT NOT NULL,
        latency_ms REAL NOT NULL,
        success INTEGER NOT NULL,
        mode TEXT NOT NULL,
        result_size INTEGER,
        error_message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_pagerank ON symbols(pagerank_score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id, source_type);",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id, target_type);",
    "CREATE INDEX IF NOT EXISTS idx_edges_relationship ON edges(relationship);",
    "CREATE INDEX IF NOT EXISTS idx_edges_file_line ON edges(file_path, line_number);",
    "CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_sync_queue_repo_status ON sync_queue(repo_id, status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_sync_events_repo_created ON sync_events(repo_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool_created ON tool_metrics(tool_name, created_at);",
)

FTS_STATEMENTS = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts
    USING fts5(symbol_id UNINDEXED, name, qualified_name, docstring, signature);
    """,
    "CREATE INDEX IF NOT EXISTS idx_symbol_fts_symbol_id ON symbol_fts(symbol_id);",
)


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_schema(self) -> None:
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            for statement in FTS_STATEMENTS:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    continue
            self._migrate_schema(conn)
            conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        current_version = self._get_schema_version(conn)
        while current_version < SCHEMA_VERSION:
            next_version = current_version + 1
            conn.execute("SAVEPOINT bombe_migrate_step;")
            try:
                if next_version == 1:
                    self._migrate_to_v1(conn)
                elif next_version == 2:
                    self._migrate_to_v2(conn)
                elif next_version == 3:
                    self._migrate_to_v3(conn)
                elif next_version == 4:
                    self._migrate_to_v4(conn)
                self._set_schema_version(conn, next_version)
                self._record_migration_step(
                    conn=conn,
                    from_version=current_version,
                    to_version=next_version,
                    status="success",
                    error_message=None,
                )
                conn.execute("RELEASE SAVEPOINT bombe_migrate_step;")
                current_version = next_version
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT bombe_migrate_step;")
                conn.execute("RELEASE SAVEPOINT bombe_migrate_step;")
                self._record_migration_step(
                    conn=conn,
                    from_version=current_version,
                    to_version=next_version,
                    status="failed",
                    error_message=str(exc),
                )
                raise

    def _migrate_to_v1(self, conn: sqlite3.Connection) -> None:
        del conn

    def _migrate_to_v2(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("SELECT 1 FROM symbol_fts LIMIT 1;")
        except sqlite3.OperationalError:
            return
        conn.execute("DELETE FROM symbol_fts;")
        rows = conn.execute(
            """
            SELECT
                id,
                name,
                qualified_name,
                COALESCE(docstring, '') AS docstring,
                COALESCE(signature, '') AS signature
            FROM symbols;
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature)
                VALUES (?, ?, ?, ?, ?);
                """,
                (
                    int(row["id"]),
                    row["name"],
                    row["qualified_name"],
                    row["docstring"],
                    row["signature"],
                ),
            )

    def _migrate_to_v3(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_file_line ON edges(file_path, line_number);"
        )

    def _migrate_to_v4(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_version INTEGER NOT NULL,
                to_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                local_snapshot TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_quarantine (
                artifact_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                quarantined_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_pins (
                repo_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                pinned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(repo_id, snapshot_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS circuit_breakers (
                repo_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                failure_count INTEGER NOT NULL DEFAULT 0,
                opened_at_utc TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                level TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT,
                tool_name TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                success INTEGER NOT NULL,
                mode TEXT NOT NULL,
                result_size INTEGER,
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_queue_repo_status ON sync_queue(repo_id, status, created_at);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_events_repo_created ON sync_events(repo_id, created_at);"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool_created ON tool_metrics(tool_name, created_at);"
        )

    def _get_schema_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM repo_meta WHERE key = 'schema_version';"
        ).fetchone()
        if not row:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def _set_schema_version(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            """
            INSERT INTO repo_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (str(version),),
        )

    def _record_migration_step(
        self,
        conn: sqlite3.Connection,
        from_version: int,
        to_version: int,
        status: str,
        error_message: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO migration_history(from_version, to_version, status, error_message)
            VALUES (?, ?, ?, ?);
            """,
            (from_version, to_version, status, error_message),
        )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def upsert_files(self, records: Sequence[FileRecord]) -> None:
        if not records:
            return
        rows = [
            (r.path, r.language, r.content_hash, r.size_bytes)
            for r in records
        ]
        with closing(self.connect()) as conn:
            conn.executemany(
                """
                INSERT INTO files (path, language, content_hash, size_bytes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    language = excluded.language,
                    content_hash = excluded.content_hash,
                    size_bytes = excluded.size_bytes,
                    last_indexed_at = CURRENT_TIMESTAMP;
                """,
                rows,
            )
            conn.commit()

    def replace_file_symbols(self, file_path: str, symbols: Sequence[SymbolRecord]) -> None:
        with closing(self.connect()) as conn:
            old_symbol_rows = conn.execute(
                "SELECT id FROM symbols WHERE file_path = ?;",
                (file_path,),
            ).fetchall()
            old_symbol_ids = [int(row["id"]) for row in old_symbol_rows]
            for symbol_id in old_symbol_ids:
                try:
                    conn.execute("DELETE FROM symbol_fts WHERE symbol_id = ?;", (symbol_id,))
                except sqlite3.OperationalError:
                    break
            conn.execute(
                "DELETE FROM parameters WHERE symbol_id IN (SELECT id FROM symbols WHERE file_path = ?);",
                (file_path,),
            )
            conn.execute("DELETE FROM symbols WHERE file_path = ?;", (file_path,))
            for symbol in symbols:
                cursor = conn.execute(
                    """
                    INSERT INTO symbols (
                        name, qualified_name, kind, file_path, start_line, end_line, signature,
                        return_type, visibility, is_async, is_static, parent_symbol_id, docstring, pagerank_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        symbol.name,
                        symbol.qualified_name,
                        symbol.kind,
                        symbol.file_path,
                        symbol.start_line,
                        symbol.end_line,
                        symbol.signature,
                        symbol.return_type,
                        symbol.visibility,
                        int(symbol.is_async),
                        int(symbol.is_static),
                        symbol.parent_symbol_id,
                        symbol.docstring,
                        symbol.pagerank_score,
                    ),
                )
                symbol_id = int(cursor.lastrowid)
                for param in symbol.parameters:
                    conn.execute(
                        """
                        INSERT INTO parameters (symbol_id, name, type, position, default_value)
                        VALUES (?, ?, ?, ?, ?);
                        """,
                        (
                            symbol_id,
                            param.name,
                            param.type,
                            param.position,
                            param.default_value,
                        ),
                    )
                try:
                    conn.execute(
                        """
                        INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature)
                        VALUES (?, ?, ?, ?, ?);
                        """,
                        (
                            symbol_id,
                            symbol.name,
                            symbol.qualified_name,
                            symbol.docstring or "",
                            symbol.signature or "",
                        ),
                    )
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def replace_file_edges(self, file_path: str, edges: Sequence[EdgeRecord]) -> None:
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM edges WHERE file_path = ?;", (file_path,))
            conn.executemany(
                """
                INSERT OR IGNORE INTO edges (
                    source_id, target_id, source_type, target_type, relationship,
                    file_path, line_number, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                [
                    (
                        e.source_id,
                        e.target_id,
                        e.source_type,
                        e.target_type,
                        e.relationship,
                        e.file_path,
                        e.line_number,
                        e.confidence,
                    )
                    for e in edges
                ],
            )
            conn.commit()

    def replace_external_deps(self, file_path: str, deps: Sequence[ExternalDepRecord]) -> None:
        with closing(self.connect()) as conn:
            conn.execute("DELETE FROM external_deps WHERE file_path = ?;", (file_path,))
            conn.executemany(
                """
                INSERT INTO external_deps (file_path, import_statement, module_name, line_number)
                VALUES (?, ?, ?, ?);
                """,
                [
                    (
                        d.file_path,
                        d.import_statement,
                        d.module_name,
                        d.line_number,
                    )
                    for d in deps
                ],
            )
            conn.commit()

    def delete_file_graph(self, file_path: str) -> None:
        with closing(self.connect()) as conn:
            symbol_rows = conn.execute(
                "SELECT id FROM symbols WHERE file_path = ?;",
                (file_path,),
            ).fetchall()
            for row in symbol_rows:
                try:
                    conn.execute("DELETE FROM symbol_fts WHERE symbol_id = ?;", (int(row["id"]),))
                except sqlite3.OperationalError:
                    break
            conn.execute("DELETE FROM edges WHERE file_path = ?;", (file_path,))
            conn.execute("DELETE FROM external_deps WHERE file_path = ?;", (file_path,))
            conn.execute(
                "DELETE FROM parameters WHERE symbol_id IN (SELECT id FROM symbols WHERE file_path = ?);",
                (file_path,),
            )
            conn.execute("DELETE FROM symbols WHERE file_path = ?;", (file_path,))
            conn.execute("DELETE FROM files WHERE path = ?;", (file_path,))
            conn.commit()

    def rename_file(self, old_path: str, new_path: str) -> None:
        with closing(self.connect()) as conn:
            source_rows = conn.execute(
                "SELECT language, content_hash, size_bytes, last_indexed_at FROM files WHERE path = ?;",
                (old_path,),
            ).fetchall()
            if not source_rows:
                return
            source = source_rows[0]
            conn.execute(
                """
                INSERT INTO files (path, language, content_hash, size_bytes, last_indexed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    language = excluded.language,
                    content_hash = excluded.content_hash,
                    size_bytes = excluded.size_bytes,
                    last_indexed_at = excluded.last_indexed_at;
                """,
                (
                    new_path,
                    source["language"],
                    source["content_hash"],
                    source["size_bytes"],
                    source["last_indexed_at"],
                ),
            )
            conn.execute("UPDATE symbols SET file_path = ? WHERE file_path = ?;", (new_path, old_path))
            conn.execute("UPDATE edges SET file_path = ? WHERE file_path = ?;", (new_path, old_path))
            conn.execute("UPDATE external_deps SET file_path = ? WHERE file_path = ?;", (new_path, old_path))
            conn.execute("DELETE FROM files WHERE path = ?;", (old_path,))
            conn.commit()

    def backup_to(self, destination: Path) -> Path:
        backup_path = destination.expanduser().resolve()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as source_conn:
            with closing(sqlite3.connect(backup_path)) as backup_conn:
                source_conn.backup(backup_conn)
                backup_conn.commit()
        return backup_path

    def restore_from(self, source: Path) -> None:
        source_path = source.expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Backup file does not exist: {source_path}")
        with closing(sqlite3.connect(source_path)) as source_conn:
            with closing(self.connect()) as target_conn:
                source_conn.backup(target_conn)
                target_conn.commit()

    def enqueue_sync_delta(self, repo_id: str, local_snapshot: str, payload_json: str) -> int:
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_queue(repo_id, local_snapshot, payload_json, status)
                VALUES (?, ?, ?, 'queued');
                """,
                (repo_id, local_snapshot, payload_json),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_pending_sync_deltas(self, repo_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT id, repo_id, local_snapshot, payload_json, status, attempt_count, last_error, created_at, updated_at
            FROM sync_queue
            WHERE repo_id = ? AND status IN ('queued', 'retry')
            ORDER BY created_at ASC
            LIMIT ?;
            """,
            (repo_id, max(1, limit)),
        )

    def mark_sync_delta_status(self, queue_id: int, status: str, last_error: str | None = None) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                UPDATE sync_queue
                SET
                    status = ?,
                    last_error = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (status, last_error, queue_id),
            )
            conn.commit()

    def set_artifact_pin(self, repo_id: str, snapshot_id: str, artifact_id: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO artifact_pins(repo_id, snapshot_id, artifact_id, pinned_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(repo_id, snapshot_id) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    pinned_at = excluded.pinned_at;
                """,
                (repo_id, snapshot_id, artifact_id),
            )
            conn.commit()

    def get_artifact_pin(self, repo_id: str, snapshot_id: str) -> str | None:
        rows = self.query(
            """
            SELECT artifact_id
            FROM artifact_pins
            WHERE repo_id = ? AND snapshot_id = ?
            LIMIT 1;
            """,
            (repo_id, snapshot_id),
        )
        if not rows:
            return None
        return str(rows[0]["artifact_id"])

    def quarantine_artifact(self, artifact_id: str, reason: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO artifact_quarantine(artifact_id, reason, quarantined_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    reason = excluded.reason,
                    quarantined_at = excluded.quarantined_at;
                """,
                (artifact_id, reason),
            )
            conn.commit()

    def is_artifact_quarantined(self, artifact_id: str) -> bool:
        rows = self.query(
            "SELECT artifact_id FROM artifact_quarantine WHERE artifact_id = ? LIMIT 1;",
            (artifact_id,),
        )
        return bool(rows)

    def list_quarantined_artifacts(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT artifact_id, reason, quarantined_at
            FROM artifact_quarantine
            ORDER BY quarantined_at DESC
            LIMIT ?;
            """,
            (max(1, limit),),
        )

    def set_circuit_breaker_state(
        self,
        repo_id: str,
        state: str,
        failure_count: int,
        opened_at_utc: str | None,
    ) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO circuit_breakers(repo_id, state, failure_count, opened_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    state = excluded.state,
                    failure_count = excluded.failure_count,
                    opened_at_utc = excluded.opened_at_utc;
                """,
                (repo_id, state, max(0, failure_count), opened_at_utc),
            )
            conn.commit()

    def get_circuit_breaker_state(self, repo_id: str) -> dict[str, Any] | None:
        rows = self.query(
            """
            SELECT state, failure_count, opened_at_utc
            FROM circuit_breakers
            WHERE repo_id = ?
            LIMIT 1;
            """,
            (repo_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "state": str(row["state"]),
            "failure_count": int(row["failure_count"]),
            "opened_at_utc": row["opened_at_utc"],
        }

    def record_sync_event(
        self,
        repo_id: str,
        level: str,
        event_type: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        detail_json = json.dumps(detail, sort_keys=True) if detail is not None else None
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO sync_events(repo_id, level, event_type, detail_json)
                VALUES (?, ?, ?, ?);
                """,
                (repo_id, level, event_type, detail_json),
            )
            conn.commit()

    def record_tool_metric(
        self,
        tool_name: str,
        latency_ms: float,
        success: bool,
        mode: str,
        repo_id: str | None = None,
        result_size: int | None = None,
        error_message: str | None = None,
    ) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO tool_metrics(repo_id, tool_name, latency_ms, success, mode, result_size, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    repo_id,
                    tool_name,
                    float(latency_ms),
                    int(bool(success)),
                    mode,
                    result_size,
                    error_message,
                ),
            )
            conn.commit()

    def recent_tool_metrics(self, tool_name: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT repo_id, tool_name, latency_ms, success, mode, result_size, error_message, created_at
            FROM tool_metrics
            WHERE tool_name = ?
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (tool_name, max(1, limit)),
        )
