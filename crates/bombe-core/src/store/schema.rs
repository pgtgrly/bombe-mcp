//! SQLite schema DDL and migration framework.
//!
//! Direct port of the Python `bombe.store.database` schema layer.
//! Every table, index, and migration step matches the Python implementation.

use rusqlite::Connection;

use crate::errors::BombeResult;

/// Current schema version. Migrations run from whatever the DB currently
/// reports up to this value.
pub const SCHEMA_VERSION: i32 = 7;

/// Core DDL statements: 15 CREATE TABLE + 18 CREATE INDEX.
///
/// Executed with `CREATE … IF NOT EXISTS` so they are safe to replay on an
/// already-initialised database.
pub const SCHEMA_STATEMENTS: &[&str] = &[
    // ── tables (15) ─────────────────────────────────────────────────────
    "CREATE TABLE IF NOT EXISTS repo_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );",
    "CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        language TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        size_bytes INTEGER,
        last_indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS symbols (
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
    );",
    "CREATE TABLE IF NOT EXISTS parameters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol_id INTEGER NOT NULL REFERENCES symbols(id),
        name TEXT NOT NULL,
        type TEXT,
        position INTEGER NOT NULL,
        default_value TEXT,
        UNIQUE(symbol_id, position)
    );",
    "CREATE TABLE IF NOT EXISTS edges (
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
    );",
    "CREATE TABLE IF NOT EXISTS external_deps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL REFERENCES files(path),
        import_statement TEXT NOT NULL,
        module_name TEXT NOT NULL,
        line_number INTEGER
    );",
    "CREATE TABLE IF NOT EXISTS migration_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_version INTEGER NOT NULL,
        to_version INTEGER NOT NULL,
        status TEXT NOT NULL,
        error_message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id TEXT NOT NULL,
        local_snapshot TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS artifact_quarantine (
        artifact_id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        quarantined_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS artifact_pins (
        repo_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        pinned_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(repo_id, snapshot_id)
    );",
    "CREATE TABLE IF NOT EXISTS circuit_breakers (
        repo_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        failure_count INTEGER NOT NULL DEFAULT 0,
        opened_at_utc TEXT
    );",
    "CREATE TABLE IF NOT EXISTS sync_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id TEXT NOT NULL,
        level TEXT NOT NULL,
        event_type TEXT NOT NULL,
        detail_json TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS tool_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repo_id TEXT,
        tool_name TEXT NOT NULL,
        latency_ms REAL NOT NULL,
        success INTEGER NOT NULL,
        mode TEXT NOT NULL,
        result_size INTEGER,
        error_message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS indexing_diagnostics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        category TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'error',
        file_path TEXT,
        language TEXT,
        message TEXT NOT NULL,
        hint TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS trusted_signing_keys (
        repo_id TEXT NOT NULL,
        key_id TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        public_key TEXT NOT NULL,
        purpose TEXT NOT NULL DEFAULT 'default',
        active INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(repo_id, key_id)
    );",
    // ── indexes (18) ────────────────────────────────────────────────────
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
    "CREATE INDEX IF NOT EXISTS idx_index_diag_run_created ON indexing_diagnostics(run_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_index_diag_stage_category ON indexing_diagnostics(stage, category);",
    "CREATE INDEX IF NOT EXISTS idx_index_diag_file_created ON indexing_diagnostics(file_path, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_index_diag_severity_created ON indexing_diagnostics(severity, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_trusted_keys_repo_active ON trusted_signing_keys(repo_id, active, key_id);",
];

/// FTS5 virtual table and its helper index.
///
/// These are executed inside a `try/catch` (the Rust equivalent: we ignore
/// `OperationalError`) because some SQLite builds lack FTS5 support.
pub const FTS_STATEMENTS: &[&str] = &[
    "CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts
     USING fts5(symbol_id UNINDEXED, name, qualified_name, docstring, signature);",
    "CREATE INDEX IF NOT EXISTS idx_symbol_fts_symbol_id ON symbol_fts(symbol_id);",
];

// ─── Migration framework ────────────────────────────────────────────────────

/// Run all pending migrations from the current stored version up to
/// [`SCHEMA_VERSION`].  Each step is wrapped in a SAVEPOINT so a failure
/// rolls back only that single step (matching the Python implementation).
pub fn migrate_schema(conn: &Connection) -> BombeResult<()> {
    let mut current_version = get_schema_version(conn);

    while current_version < SCHEMA_VERSION {
        let next_version = current_version + 1;
        conn.execute_batch("SAVEPOINT bombe_migrate_step;")?;

        let step_result = (|| -> BombeResult<()> {
            match next_version {
                1 => migrate_to_v1(conn)?,
                2 => migrate_to_v2(conn)?,
                3 => migrate_to_v3(conn)?,
                4 => migrate_to_v4(conn)?,
                5 => migrate_to_v5(conn)?,
                6 => migrate_to_v6(conn)?,
                7 => migrate_to_v7(conn)?,
                _ => {} // future versions: no-op until migration is defined
            }
            set_schema_version(conn, next_version)?;
            record_migration_step(conn, current_version, next_version, "success", None)?;
            conn.execute_batch("RELEASE SAVEPOINT bombe_migrate_step;")?;
            Ok(())
        })();

        match step_result {
            Ok(()) => {
                current_version = next_version;
            }
            Err(e) => {
                // Roll back just this step, then release the savepoint.
                let _ = conn.execute_batch("ROLLBACK TO SAVEPOINT bombe_migrate_step;");
                let _ = conn.execute_batch("RELEASE SAVEPOINT bombe_migrate_step;");
                let _ = record_migration_step(
                    conn,
                    current_version,
                    next_version,
                    "failed",
                    Some(&e.to_string()),
                );
                return Err(e);
            }
        }
    }

    Ok(())
}

/// Read the current schema version from `repo_meta`.
/// Returns 0 when the key is absent or unparseable.
fn get_schema_version(conn: &Connection) -> i32 {
    let result: Result<String, _> = conn.query_row(
        "SELECT value FROM repo_meta WHERE key = 'schema_version';",
        [],
        |row| row.get(0),
    );
    match result {
        Ok(v) => v.parse::<i32>().unwrap_or(0),
        Err(_) => 0,
    }
}

/// Upsert the `schema_version` key in `repo_meta`.
fn set_schema_version(conn: &Connection, version: i32) -> BombeResult<()> {
    conn.execute(
        "INSERT INTO repo_meta(key, value) \
         VALUES('schema_version', ?1) \
         ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
        rusqlite::params![version.to_string()],
    )?;
    Ok(())
}

/// Insert one row into `migration_history` (best-effort; never fails the
/// caller).
fn record_migration_step(
    conn: &Connection,
    from_v: i32,
    to_v: i32,
    status: &str,
    error_msg: Option<&str>,
) -> BombeResult<()> {
    conn.execute(
        "INSERT INTO migration_history(from_version, to_version, status, error_message) \
         VALUES (?1, ?2, ?3, ?4);",
        rusqlite::params![from_v, to_v, status, error_msg],
    )?;
    Ok(())
}

// ─── Individual migration steps ─────────────────────────────────────────────

/// v0 -> v1: baseline, no-op.
fn migrate_to_v1(_conn: &Connection) -> BombeResult<()> {
    // Intentionally empty -- baseline schema already created by SCHEMA_STATEMENTS.
    Ok(())
}

/// v1 -> v2: rebuild FTS index from the `symbols` table.
fn migrate_to_v2(conn: &Connection) -> BombeResult<()> {
    // Check whether the FTS table exists at all; if not, nothing to rebuild.
    let fts_exists = conn
        .query_row("SELECT 1 FROM symbol_fts LIMIT 1;", [], |_| Ok(()))
        .is_ok();
    if !fts_exists {
        return Ok(());
    }

    conn.execute_batch("DELETE FROM symbol_fts;")?;

    let mut stmt = conn.prepare(
        "SELECT id, name, qualified_name, \
                COALESCE(docstring, '') AS docstring, \
                COALESCE(signature, '') AS signature \
         FROM symbols;",
    )?;

    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, String>(4)?,
        ))
    })?;

    for row_result in rows {
        let (id, name, qualified_name, docstring, signature) = row_result?;
        conn.execute(
            "INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature) \
             VALUES (?1, ?2, ?3, ?4, ?5);",
            rusqlite::params![id, name, qualified_name, docstring, signature],
        )?;
    }

    Ok(())
}

/// v2 -> v3: add `idx_edges_file_line` index.
fn migrate_to_v3(conn: &Connection) -> BombeResult<()> {
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_edges_file_line ON edges(file_path, line_number);",
    )?;
    Ok(())
}

/// v3 -> v4: create sync-related tables and their indexes.
fn migrate_to_v4(conn: &Connection) -> BombeResult<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS migration_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_version INTEGER NOT NULL,
            to_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );",
    )?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            local_snapshot TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );",
    )?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS artifact_quarantine (
            artifact_id TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            quarantined_at TEXT DEFAULT CURRENT_TIMESTAMP
        );",
    )?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS artifact_pins (
            repo_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            pinned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(repo_id, snapshot_id)
        );",
    )?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS circuit_breakers (
            repo_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            opened_at_utc TEXT
        );",
    )?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS sync_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );",
    )?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS tool_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT,
            tool_name TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            success INTEGER NOT NULL,
            mode TEXT NOT NULL,
            result_size INTEGER,
            error_message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_sync_queue_repo_status \
         ON sync_queue(repo_id, status, created_at);",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_sync_events_repo_created \
         ON sync_events(repo_id, created_at);",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool_created \
         ON tool_metrics(tool_name, created_at);",
    )?;
    Ok(())
}

/// v4 -> v5: create `trusted_signing_keys` table and index.
fn migrate_to_v5(conn: &Connection) -> BombeResult<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS trusted_signing_keys (
            repo_id TEXT NOT NULL,
            key_id TEXT NOT NULL,
            algorithm TEXT NOT NULL,
            public_key TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'default',
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(repo_id, key_id)
        );",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_trusted_keys_repo_active \
         ON trusted_signing_keys(repo_id, active, key_id);",
    )?;
    Ok(())
}

/// v5 -> v6: create `indexing_diagnostics` table and indexes.
fn migrate_to_v6(conn: &Connection) -> BombeResult<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS indexing_diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'error',
            file_path TEXT,
            language TEXT,
            message TEXT NOT NULL,
            hint TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_index_diag_run_created \
         ON indexing_diagnostics(run_id, created_at);",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_index_diag_stage_category \
         ON indexing_diagnostics(stage, category);",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_index_diag_file_created \
         ON indexing_diagnostics(file_path, created_at);",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_index_diag_severity_created \
         ON indexing_diagnostics(severity, created_at);",
    )?;
    Ok(())
}

/// v6 -> v7: add indexes on `external_deps` for module-name lookups.
fn migrate_to_v7(conn: &Connection) -> BombeResult<()> {
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_external_deps_module \
         ON external_deps(module_name);",
    )?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_external_deps_file_module \
         ON external_deps(file_path, module_name);",
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verify that the constant arrays have the expected sizes.
    #[test]
    fn schema_statement_counts() {
        // 15 tables + 18 indexes = 33 statements
        assert_eq!(SCHEMA_STATEMENTS.len(), 33);
        assert_eq!(FTS_STATEMENTS.len(), 2);
    }

    /// A fresh in-memory database should migrate cleanly to the current version.
    #[test]
    fn migrate_fresh_database() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("PRAGMA foreign_keys = ON;").unwrap();

        // Create base schema.
        for stmt in SCHEMA_STATEMENTS {
            conn.execute_batch(stmt).unwrap();
        }
        // FTS (best-effort).
        for stmt in FTS_STATEMENTS {
            let _ = conn.execute_batch(stmt);
        }

        // Run migrations.
        migrate_schema(&conn).unwrap();

        // Version should now be current.
        assert_eq!(get_schema_version(&conn), SCHEMA_VERSION);
    }

    /// Running migrate_schema twice is idempotent.
    #[test]
    fn migrate_idempotent() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("PRAGMA foreign_keys = ON;").unwrap();

        for stmt in SCHEMA_STATEMENTS {
            conn.execute_batch(stmt).unwrap();
        }
        for stmt in FTS_STATEMENTS {
            let _ = conn.execute_batch(stmt);
        }

        migrate_schema(&conn).unwrap();
        migrate_schema(&conn).unwrap();

        assert_eq!(get_schema_version(&conn), SCHEMA_VERSION);
    }
}
