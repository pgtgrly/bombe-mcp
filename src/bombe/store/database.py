"""SQLite storage layer for Bombe."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence

from bombe.models import EdgeRecord, ExternalDepRecord, FileRecord, SymbolRecord


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
    "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);",
    "CREATE INDEX IF NOT EXISTS idx_symbols_pagerank ON symbols(pagerank_score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id, source_type);",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id, target_type);",
    "CREATE INDEX IF NOT EXISTS idx_edges_relationship ON edges(relationship);",
    "CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);",
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
            conn.commit()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with closing(self.connect()) as conn:
            cursor = conn.execute(sql, params)
            return list(cursor.fetchall())

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
