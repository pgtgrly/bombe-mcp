"""SQLite shard catalog for cross-repo sharding and federation."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from bombe.models import CrossRepoEdge, GlobalSymbolURI, ShardInfo
from bombe.query.guards import MAX_EXPORTED_SYMBOLS_REFRESH
from bombe.store.database import Database

CATALOG_SCHEMA_VERSION = 1

CATALOG_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS catalog_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS shards (
        repo_id TEXT PRIMARY KEY,
        repo_path TEXT NOT NULL,
        db_path TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_indexed_at TEXT,
        symbol_count INTEGER DEFAULT 0,
        edge_count INTEGER DEFAULT 0,
        last_seen_epoch INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS cross_repo_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_repo_id TEXT NOT NULL,
        source_qualified_name TEXT NOT NULL,
        source_file_path TEXT NOT NULL,
        target_repo_id TEXT NOT NULL,
        target_qualified_name TEXT NOT NULL,
        target_file_path TEXT NOT NULL,
        relationship TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        provenance TEXT DEFAULT 'import_resolution',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source_repo_id, source_qualified_name, source_file_path,
               target_repo_id, target_qualified_name, target_file_path, relationship)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS exported_symbols (
        repo_id TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        file_path TEXT NOT NULL,
        visibility TEXT,
        pagerank_score REAL DEFAULT 0.0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(repo_id, qualified_name, file_path)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_cross_edges_source ON cross_repo_edges(source_repo_id, source_qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_cross_edges_target ON cross_repo_edges(target_repo_id, target_qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_exported_name ON exported_symbols(name);",
    "CREATE INDEX IF NOT EXISTS idx_exported_qualified ON exported_symbols(qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_exported_kind ON exported_symbols(kind);",
)


class ShardCatalog:
    """Manages a SQLite catalog database for cross-repo sharding."""

    CATALOG_SCHEMA_VERSION = 1

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
            for statement in CATALOG_SCHEMA_STATEMENTS:
                conn.execute(statement)
            self._migrate_schema(conn)
            conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        current_version = self._get_schema_version(conn)
        while current_version < CATALOG_SCHEMA_VERSION:
            next_version = current_version + 1
            if next_version == 1:
                pass  # Initial schema created by CATALOG_SCHEMA_STATEMENTS
            self._set_schema_version(conn, next_version)
            current_version = next_version

    def _get_schema_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM catalog_meta WHERE key = 'schema_version';"
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
            INSERT INTO catalog_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """,
            (str(version),),
        )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Shard management
    # ------------------------------------------------------------------

    def register_shard(self, info: ShardInfo) -> None:
        """INSERT OR REPLACE into shards table."""
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO shards(
                    repo_id, repo_path, db_path, enabled,
                    last_indexed_at, symbol_count, edge_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                """,
                (
                    info.repo_id,
                    info.repo_path,
                    info.db_path,
                    int(info.enabled),
                    info.last_indexed_at,
                    info.symbol_count,
                    info.edge_count,
                ),
            )
            conn.commit()

    def unregister_shard(self, repo_id: str) -> None:
        """DELETE from shards, cross_repo_edges, and exported_symbols for repo_id."""
        with closing(self.connect()) as conn:
            conn.execute(
                "DELETE FROM cross_repo_edges WHERE source_repo_id = ? OR target_repo_id = ?;",
                (repo_id, repo_id),
            )
            conn.execute(
                "DELETE FROM exported_symbols WHERE repo_id = ?;",
                (repo_id,),
            )
            conn.execute(
                "DELETE FROM shards WHERE repo_id = ?;",
                (repo_id,),
            )
            conn.commit()

    def list_shards(self, enabled_only: bool = True) -> list[ShardInfo]:
        """Return all shards, optionally filtered to enabled only."""
        if enabled_only:
            rows = self.query(
                "SELECT * FROM shards WHERE enabled = 1 ORDER BY repo_id ASC;"
            )
        else:
            rows = self.query("SELECT * FROM shards ORDER BY repo_id ASC;")
        return [
            ShardInfo(
                repo_id=str(row["repo_id"]),
                repo_path=str(row["repo_path"]),
                db_path=str(row["db_path"]),
                enabled=bool(int(row["enabled"])),
                last_indexed_at=row["last_indexed_at"],
                symbol_count=int(row["symbol_count"]),
                edge_count=int(row["edge_count"]),
            )
            for row in rows
        ]

    def get_shard(self, repo_id: str) -> ShardInfo | None:
        """Return shard info by repo_id."""
        rows = self.query(
            "SELECT * FROM shards WHERE repo_id = ? LIMIT 1;",
            (repo_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return ShardInfo(
            repo_id=str(row["repo_id"]),
            repo_path=str(row["repo_path"]),
            db_path=str(row["db_path"]),
            enabled=bool(int(row["enabled"])),
            last_indexed_at=row["last_indexed_at"],
            symbol_count=int(row["symbol_count"]),
            edge_count=int(row["edge_count"]),
        )

    def update_shard_stats(self, repo_id: str, symbol_count: int, edge_count: int) -> None:
        """Update symbol_count, edge_count, last_indexed_at, updated_at for a shard."""
        with closing(self.connect()) as conn:
            conn.execute(
                """
                UPDATE shards
                SET symbol_count = ?,
                    edge_count = ?,
                    last_indexed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE repo_id = ?;
                """,
                (symbol_count, edge_count, repo_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Cross-repo edge management
    # ------------------------------------------------------------------

    def upsert_cross_repo_edges(self, edges: list[CrossRepoEdge]) -> int:
        """INSERT OR REPLACE cross-repo edges. Return count inserted."""
        if not edges:
            return 0
        count = 0
        with closing(self.connect()) as conn:
            for edge in edges:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cross_repo_edges(
                        source_repo_id, source_qualified_name, source_file_path,
                        target_repo_id, target_qualified_name, target_file_path,
                        relationship, confidence, provenance, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                    """,
                    (
                        edge.source_uri.repo_id,
                        edge.source_uri.qualified_name,
                        edge.source_uri.file_path,
                        edge.target_uri.repo_id,
                        edge.target_uri.qualified_name,
                        edge.target_uri.file_path,
                        edge.relationship,
                        edge.confidence,
                        edge.provenance,
                    ),
                )
                count += 1
            conn.commit()
        return count

    def get_cross_repo_edges_from(self, repo_id: str, qualified_name: str) -> list[CrossRepoEdge]:
        """Get outgoing cross-repo edges from a symbol."""
        rows = self.query(
            """
            SELECT source_repo_id, source_qualified_name, source_file_path,
                   target_repo_id, target_qualified_name, target_file_path,
                   relationship, confidence, provenance
            FROM cross_repo_edges
            WHERE source_repo_id = ? AND source_qualified_name = ?
            ORDER BY id ASC;
            """,
            (repo_id, qualified_name),
        )
        return [
            CrossRepoEdge(
                source_uri=GlobalSymbolURI(
                    repo_id=str(row["source_repo_id"]),
                    qualified_name=str(row["source_qualified_name"]),
                    file_path=str(row["source_file_path"]),
                ),
                target_uri=GlobalSymbolURI(
                    repo_id=str(row["target_repo_id"]),
                    qualified_name=str(row["target_qualified_name"]),
                    file_path=str(row["target_file_path"]),
                ),
                relationship=str(row["relationship"]),
                confidence=float(row["confidence"]),
                provenance=str(row["provenance"]),
            )
            for row in rows
        ]

    def get_cross_repo_edges_to(self, repo_id: str, qualified_name: str) -> list[CrossRepoEdge]:
        """Get incoming cross-repo edges to a symbol."""
        rows = self.query(
            """
            SELECT source_repo_id, source_qualified_name, source_file_path,
                   target_repo_id, target_qualified_name, target_file_path,
                   relationship, confidence, provenance
            FROM cross_repo_edges
            WHERE target_repo_id = ? AND target_qualified_name = ?
            ORDER BY id ASC;
            """,
            (repo_id, qualified_name),
        )
        return [
            CrossRepoEdge(
                source_uri=GlobalSymbolURI(
                    repo_id=str(row["source_repo_id"]),
                    qualified_name=str(row["source_qualified_name"]),
                    file_path=str(row["source_file_path"]),
                ),
                target_uri=GlobalSymbolURI(
                    repo_id=str(row["target_repo_id"]),
                    qualified_name=str(row["target_qualified_name"]),
                    file_path=str(row["target_file_path"]),
                ),
                relationship=str(row["relationship"]),
                confidence=float(row["confidence"]),
                provenance=str(row["provenance"]),
            )
            for row in rows
        ]

    def delete_cross_repo_edges_for_repo(self, repo_id: str) -> int:
        """Delete all cross-repo edges involving a repo. Return count deleted."""
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                "DELETE FROM cross_repo_edges WHERE source_repo_id = ? OR target_repo_id = ?;",
                (repo_id, repo_id),
            )
            deleted = int(cursor.rowcount or 0)
            conn.commit()
            return deleted

    # ------------------------------------------------------------------
    # Exported symbol cache
    # ------------------------------------------------------------------

    def refresh_exported_symbols(self, repo_id: str, shard_db: Database) -> int:
        """Read symbols from shard_db, write to exported_symbols. Return count.

        Uses shard_db.query() to read the symbols table, deletes old entries
        for repo_id first. Respects MAX_EXPORTED_SYMBOLS_REFRESH limit.
        """
        symbols = shard_db.query(
            """
            SELECT qualified_name, name, kind, file_path, visibility, pagerank_score
            FROM symbols
            ORDER BY pagerank_score DESC
            LIMIT ?;
            """,
            (MAX_EXPORTED_SYMBOLS_REFRESH,),
        )
        with closing(self.connect()) as conn:
            conn.execute(
                "DELETE FROM exported_symbols WHERE repo_id = ?;",
                (repo_id,),
            )
            count = 0
            for sym in symbols:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO exported_symbols(
                        repo_id, qualified_name, name, kind, file_path,
                        visibility, pagerank_score, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                    """,
                    (
                        repo_id,
                        sym["qualified_name"],
                        sym["name"],
                        sym["kind"],
                        sym["file_path"],
                        sym.get("visibility"),
                        float(sym.get("pagerank_score", 0.0)),
                    ),
                )
                count += 1
            conn.commit()
        return count

    def search_exported_symbols(
        self, query: str, kind: str = "any", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Search exported symbols by name LIKE pattern. Filter by kind if not 'any'."""
        pattern = f"%{query}%"
        safe_limit = max(1, limit)
        if kind == "any":
            return self.query(
                """
                SELECT repo_id, qualified_name, name, kind, file_path,
                       visibility, pagerank_score
                FROM exported_symbols
                WHERE name LIKE ?
                ORDER BY pagerank_score DESC
                LIMIT ?;
                """,
                (pattern, safe_limit),
            )
        return self.query(
            """
            SELECT repo_id, qualified_name, name, kind, file_path,
                   visibility, pagerank_score
            FROM exported_symbols
            WHERE name LIKE ? AND kind = ?
            ORDER BY pagerank_score DESC
            LIMIT ?;
            """,
            (pattern, kind, safe_limit),
        )

    def resolve_external_import(
        self, module_name: str, language: str
    ) -> list[dict[str, Any]]:
        """Find exported symbols matching module_name for cross-repo resolution.

        For Python: module_name like 'foo.bar' -> search qualified_name LIKE 'foo.bar%'
        For Java: module_name like 'com.example.Foo' -> search qualified_name LIKE 'com.example.Foo%'
        For TypeScript: search name matching last segment of module_name
        For Go: search qualified_name LIKE module_name%
        """
        lang_lower = language.lower()

        if lang_lower == "typescript":
            # For TypeScript, match the last segment of the module path as a name
            segments = module_name.replace("/", ".").split(".")
            last_segment = segments[-1] if segments else module_name
            return self.query(
                """
                SELECT repo_id, qualified_name, name, kind, file_path,
                       visibility, pagerank_score
                FROM exported_symbols
                WHERE name = ?
                ORDER BY pagerank_score DESC
                LIMIT 20;
                """,
                (last_segment,),
            )

        # Python, Java, Go, and other languages: prefix match on qualified_name
        prefix = f"{module_name}%"
        return self.query(
            """
            SELECT repo_id, qualified_name, name, kind, file_path,
                   visibility, pagerank_score
            FROM exported_symbols
            WHERE qualified_name LIKE ?
            ORDER BY pagerank_score DESC
            LIMIT 20;
            """,
            (prefix,),
        )
