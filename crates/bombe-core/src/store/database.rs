//! SQLite storage layer for Bombe.
//!
//! Direct port of the Python `bombe.store.database.Database` class.
//! Each public method opens its own connection (matching Python behaviour).

use std::collections::HashSet;
use std::path::PathBuf;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rusqlite::{params, Connection};

use crate::errors::{BombeError, BombeResult};
use crate::models::{EdgeRecord, ExternalDepRecord, FileRecord, SymbolRecord};
use crate::store::schema;

// ---------------------------------------------------------------------------
// Helper: tilde expansion (equivalent to Python's Path.expanduser())
// ---------------------------------------------------------------------------

/// Expand a leading `~` to the user's home directory.
fn expand_tilde(path: &str) -> PathBuf {
    if path == "~" || path.starts_with("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            let mut expanded = PathBuf::from(home);
            if path.len() > 2 {
                expanded.push(&path[2..]);
            }
            return expanded;
        }
    }
    PathBuf::from(path)
}

// ---------------------------------------------------------------------------
// Helper: convert a rusqlite row into a Python dict using column names.
// ---------------------------------------------------------------------------

/// Build a Python dict from the current row of a `rusqlite::Statement`.
/// Column names are used as keys; values are mapped to their natural Python
/// types (String -> str, i64 -> int, f64 -> float, Null -> None).
fn row_to_pydict<'py>(
    py: Python<'py>,
    row: &rusqlite::Row<'_>,
    col_names: &[String],
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (i, name) in col_names.iter().enumerate() {
        // Try types in order: i64, f64, String, then fallback to None.
        if let Ok(v) = row.get::<_, i64>(i) {
            dict.set_item(name, v)?;
        } else if let Ok(v) = row.get::<_, f64>(i) {
            dict.set_item(name, v)?;
        } else if let Ok(v) = row.get::<_, String>(i) {
            dict.set_item(name, v)?;
        } else {
            dict.set_item(name, py.None())?;
        }
    }
    Ok(dict)
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

/// SQLite graph store for Bombe.
///
/// Mirrors the Python `Database` class: every public method opens its own
/// connection so that the caller never has to manage connection lifetime.
#[pyclass]
pub struct Database {
    db_path: PathBuf,
}

impl Database {
    /// Open a new SQLite connection to `self.db_path`, enable `foreign_keys`,
    /// and return it.
    fn connect(&self) -> BombeResult<Connection> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute_batch("PRAGMA foreign_keys = ON;")?;
        Ok(conn)
    }

    /// Public alias for internal connect, used by query engines.
    pub fn connect_internal(&self) -> BombeResult<Connection> {
        self.connect()
    }

    // -- private helpers (matching Python private methods) -------------------

    fn _set_repo_meta(conn: &Connection, key: &str, value: &str) -> BombeResult<()> {
        conn.execute(
            "INSERT INTO repo_meta(key, value) VALUES(?1, ?2) \
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            params![key, value],
        )?;
        Ok(())
    }
}

#[pymethods]
impl Database {
    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /// Create a new `Database`.  The path is expanded and parent directories
    /// are created if they do not already exist (matching Python behaviour).
    #[new]
    pub fn new(db_path: std::path::PathBuf) -> PyResult<Self> {
        let db_str = db_path.to_string_lossy();
        let expanded = expand_tilde(&db_str);
        let resolved = if expanded.is_absolute() {
            expanded
        } else {
            std::env::current_dir()
                .map_err(BombeError::Io)?
                .join(&expanded)
        };
        if let Some(parent) = resolved.parent() {
            std::fs::create_dir_all(parent).map_err(BombeError::Io)?;
        }
        Ok(Self { db_path: resolved })
    }

    /// Return the resolved database path as a string.
    #[getter]
    fn db_path(&self) -> String {
        self.db_path.to_string_lossy().into_owned()
    }

    /// Return a Python ``sqlite3.Connection`` to the database.
    ///
    /// This allows callers (tests, server code) to drop into raw SQL when the
    /// high-level helpers are not sufficient.
    #[pyo3(name = "connect")]
    fn py_connect(&self, py: Python<'_>) -> PyResult<PyObject> {
        let sqlite3 = py.import("sqlite3")?;
        let path_str = self.db_path.to_string_lossy().into_owned();
        let conn = sqlite3.call_method1("connect", (path_str,))?;
        // Set row_factory so rows are accessible by column name (matching
        // the previous Python Database.connect() behaviour).
        let row_cls = sqlite3.getattr("Row")?;
        conn.setattr("row_factory", row_cls)?;
        conn.call_method1("execute", ("PRAGMA foreign_keys = ON;",))?;
        Ok(conn.into())
    }

    // -----------------------------------------------------------------------
    // Schema / meta
    // -----------------------------------------------------------------------

    /// Initialise the database schema: set WAL mode, create all tables and
    /// indexes, attempt FTS5 creation (ignoring errors for builds without it),
    /// then run pending migrations.
    pub fn init_schema(&self) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute_batch("PRAGMA journal_mode = WAL;")
            .map_err(BombeError::from)?;

        for stmt in schema::SCHEMA_STATEMENTS {
            conn.execute_batch(stmt).map_err(BombeError::from)?;
        }
        for stmt in schema::FTS_STATEMENTS {
            // Best-effort: some SQLite builds lack FTS5.
            let _ = conn.execute_batch(stmt);
        }
        schema::migrate_schema(&conn)?;
        // rusqlite runs in autocommit mode by default, so DDL statements
        // are committed immediately.  No explicit COMMIT needed.
        Ok(())
    }

    /// Execute an arbitrary SQL statement and return a list of Python dicts.
    ///
    /// `params` is a Python list of positional bind values.
    #[pyo3(signature = (sql, params=None))]
    pub fn query(
        &self,
        py: Python<'_>,
        sql: &str,
        params: Option<Vec<PyObject>>,
    ) -> PyResult<PyObject> {
        let conn = self.connect()?;
        let mut stmt = conn.prepare(sql).map_err(BombeError::from)?;

        // Collect column names before borrowing the statement for query.
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();

        let params_vec = params.unwrap_or_default();

        // Convert PyObjects -> rusqlite-compatible values.
        let bound_params: Vec<Box<dyn rusqlite::types::ToSql>> = params_vec
            .iter()
            .map(|obj| -> Box<dyn rusqlite::types::ToSql> {
                Python::with_gil(|py2| {
                    let bound = obj.bind(py2);
                    if bound.is_none() {
                        return Box::new(rusqlite::types::Null) as Box<dyn rusqlite::types::ToSql>;
                    }
                    if let Ok(v) = bound.extract::<i64>() {
                        return Box::new(v);
                    }
                    if let Ok(v) = bound.extract::<f64>() {
                        return Box::new(v);
                    }
                    if let Ok(v) = bound.extract::<String>() {
                        return Box::new(v);
                    }
                    // Fallback: convert to string repr.
                    let repr = bound.str().map(|s| s.to_string()).unwrap_or_default();
                    Box::new(repr)
                })
            })
            .collect();

        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            bound_params.iter().map(|b| b.as_ref()).collect();

        let mut rows_out: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(param_refs.as_slice())
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            rows_out.push(row_to_pydict(py, row, &col_names)?);
        }

        let list = PyList::new(py, rows_out.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    /// Get a single repo_meta value by key, or `None`.
    fn get_repo_meta(&self, key: &str) -> PyResult<Option<String>> {
        let conn = self.connect()?;
        let result: Result<String, _> = conn.query_row(
            "SELECT value FROM repo_meta WHERE key = ?1 LIMIT 1;",
            params![key],
            |row| row.get(0),
        );
        match result {
            Ok(v) => Ok(Some(v)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(BombeError::from(e).into()),
        }
    }

    /// Upsert a single repo_meta key/value pair.
    pub fn set_repo_meta(&self, key: &str, value: &str) -> PyResult<()> {
        let conn = self.connect()?;
        Self::_set_repo_meta(&conn, key, value)?;
        Ok(())
    }

    /// Return the current cache epoch (initialising to 1 if absent).
    fn get_cache_epoch(&self) -> PyResult<i64> {
        let value = self.get_repo_meta("cache_epoch")?;
        match value {
            None => {
                self.set_repo_meta("cache_epoch", "1")?;
                Ok(1)
            }
            Some(v) => {
                let parsed = v.parse::<i64>().unwrap_or(1);
                Ok(if parsed < 1 { 1 } else { parsed })
            }
        }
    }

    /// Atomically increment the cache epoch and return the new value.
    fn bump_cache_epoch(&self) -> PyResult<i64> {
        let conn = self.connect()?;
        let current_row: Result<String, _> = conn.query_row(
            "SELECT value FROM repo_meta WHERE key = 'cache_epoch';",
            [],
            |row| row.get(0),
        );
        let current: i64 = match current_row {
            Ok(v) => v.parse::<i64>().unwrap_or(0),
            Err(_) => 0,
        };
        let next_epoch = std::cmp::max(1, current + 1);
        Self::_set_repo_meta(&conn, "cache_epoch", &next_epoch.to_string())?;
        Ok(next_epoch)
    }

    // -----------------------------------------------------------------------
    // File / symbol CRUD
    // -----------------------------------------------------------------------

    /// Upsert a batch of file records into the `files` table.
    fn upsert_files(&self, records: Vec<Py<FileRecord>>) -> PyResult<()> {
        if records.is_empty() {
            return Ok(());
        }
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "INSERT INTO files (path, language, content_hash, size_bytes) \
                 VALUES (?1, ?2, ?3, ?4) \
                 ON CONFLICT(path) DO UPDATE SET \
                     language = excluded.language, \
                     content_hash = excluded.content_hash, \
                     size_bytes = excluded.size_bytes, \
                     last_indexed_at = CURRENT_TIMESTAMP;",
            )
            .map_err(BombeError::from)?;

        Python::with_gil(|py| -> PyResult<()> {
            for rec_py in &records {
                let rec: PyRef<'_, FileRecord> = rec_py.bind(py).borrow();
                stmt.execute(params![
                    rec.path,
                    rec.language,
                    rec.content_hash,
                    rec.size_bytes
                ])
                .map_err(BombeError::from)?;
            }
            Ok(())
        })?;
        Ok(())
    }

    /// Replace all symbols (and their parameters + FTS entries) for a given
    /// file path.  Symbols are deduped by (qualified_name, file_path).
    fn replace_file_symbols(
        &self,
        file_path: &str,
        symbols: Vec<Py<SymbolRecord>>,
    ) -> PyResult<()> {
        let conn = self.connect()?;

        // Collect old symbol ids for FTS cleanup.
        let mut old_id_stmt = conn
            .prepare("SELECT id FROM symbols WHERE file_path = ?1;")
            .map_err(BombeError::from)?;
        let old_ids: Vec<i64> = old_id_stmt
            .query_map(params![file_path], |row| row.get(0))
            .map_err(BombeError::from)?
            .filter_map(|r| r.ok())
            .collect();

        // Delete old FTS rows (best-effort).
        for sid in &old_ids {
            match conn.execute("DELETE FROM symbol_fts WHERE symbol_id = ?1;", params![sid]) {
                Ok(_) => {}
                Err(_) => break, // FTS table may not exist
            }
        }

        // Delete old parameters and symbols.
        conn.execute(
            "DELETE FROM parameters WHERE symbol_id IN \
             (SELECT id FROM symbols WHERE file_path = ?1);",
            params![file_path],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "DELETE FROM symbols WHERE file_path = ?1;",
            params![file_path],
        )
        .map_err(BombeError::from)?;

        // Dedup symbols by (qualified_name, file_path).
        Python::with_gil(|py| -> PyResult<()> {
            let mut seen: HashSet<(String, String)> = HashSet::new();
            for sym_py in &symbols {
                let sym: PyRef<'_, SymbolRecord> = sym_py.bind(py).borrow();
                let key = (sym.qualified_name.clone(), sym.file_path.clone());
                if seen.contains(&key) {
                    continue;
                }
                seen.insert(key);

                // Insert symbol.
                conn.execute(
                    "INSERT INTO symbols ( \
                         name, qualified_name, kind, file_path, start_line, end_line, \
                         signature, return_type, visibility, is_async, is_static, \
                         parent_symbol_id, docstring, pagerank_score \
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14);",
                    params![
                        sym.name,
                        sym.qualified_name,
                        sym.kind,
                        sym.file_path,
                        sym.start_line,
                        sym.end_line,
                        sym.signature,
                        sym.return_type,
                        sym.visibility,
                        sym.is_async as i64,
                        sym.is_static as i64,
                        sym.parent_symbol_id,
                        sym.docstring,
                        sym.pagerank_score,
                    ],
                )
                .map_err(BombeError::from)?;

                let symbol_id = conn.last_insert_rowid();

                // Insert parameters.
                for param in &sym.parameters {
                    conn.execute(
                        "INSERT INTO parameters (symbol_id, name, type, position, default_value) \
                         VALUES (?1, ?2, ?3, ?4, ?5);",
                        params![
                            symbol_id,
                            param.name,
                            param.type_,
                            param.position,
                            param.default_value,
                        ],
                    )
                    .map_err(BombeError::from)?;
                }

                // Insert FTS (best-effort).
                let _ = conn.execute(
                    "INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature) \
                     VALUES (?1, ?2, ?3, ?4, ?5);",
                    params![
                        symbol_id,
                        sym.name,
                        sym.qualified_name,
                        sym.docstring.as_deref().unwrap_or(""),
                        sym.signature.as_deref().unwrap_or(""),
                    ],
                );
            }
            Ok(())
        })?;
        Ok(())
    }

    /// Replace all edges for a given file path.
    fn replace_file_edges(&self, file_path: &str, edges: Vec<Py<EdgeRecord>>) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "DELETE FROM edges WHERE file_path = ?1;",
            params![file_path],
        )
        .map_err(BombeError::from)?;

        let mut stmt = conn
            .prepare(
                "INSERT OR IGNORE INTO edges ( \
                     source_id, target_id, source_type, target_type, relationship, \
                     file_path, line_number, confidence \
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8);",
            )
            .map_err(BombeError::from)?;

        Python::with_gil(|py| -> PyResult<()> {
            for edge_py in &edges {
                let e: PyRef<'_, EdgeRecord> = edge_py.bind(py).borrow();
                stmt.execute(params![
                    e.source_id,
                    e.target_id,
                    e.source_type,
                    e.target_type,
                    e.relationship,
                    e.file_path,
                    e.line_number,
                    e.confidence,
                ])
                .map_err(BombeError::from)?;
            }
            Ok(())
        })?;
        Ok(())
    }

    /// Replace all external dependency records for a given file path.
    fn replace_external_deps(
        &self,
        file_path: &str,
        deps: Vec<Py<ExternalDepRecord>>,
    ) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "DELETE FROM external_deps WHERE file_path = ?1;",
            params![file_path],
        )
        .map_err(BombeError::from)?;

        let mut stmt = conn
            .prepare(
                "INSERT INTO external_deps (file_path, import_statement, module_name, line_number) \
                 VALUES (?1, ?2, ?3, ?4);",
            )
            .map_err(BombeError::from)?;

        Python::with_gil(|py| -> PyResult<()> {
            for dep_py in &deps {
                let d: PyRef<'_, ExternalDepRecord> = dep_py.bind(py).borrow();
                stmt.execute(params![
                    d.file_path,
                    d.import_statement,
                    d.module_name,
                    d.line_number
                ])
                .map_err(BombeError::from)?;
            }
            Ok(())
        })?;
        Ok(())
    }

    /// Delete all graph data (symbols, edges, parameters, FTS, file row) for
    /// a given file path.
    fn delete_file_graph(&self, file_path: &str) -> PyResult<()> {
        let conn = self.connect()?;

        // Collect symbol ids for FTS cleanup.
        let mut id_stmt = conn
            .prepare("SELECT id FROM symbols WHERE file_path = ?1;")
            .map_err(BombeError::from)?;
        let symbol_ids: Vec<i64> = id_stmt
            .query_map(params![file_path], |row| row.get(0))
            .map_err(BombeError::from)?
            .filter_map(|r| r.ok())
            .collect();

        for sid in &symbol_ids {
            match conn.execute("DELETE FROM symbol_fts WHERE symbol_id = ?1;", params![sid]) {
                Ok(_) => {}
                Err(_) => break,
            }
        }

        conn.execute(
            "DELETE FROM edges WHERE file_path = ?1;",
            params![file_path],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "DELETE FROM external_deps WHERE file_path = ?1;",
            params![file_path],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "DELETE FROM parameters WHERE symbol_id IN \
             (SELECT id FROM symbols WHERE file_path = ?1);",
            params![file_path],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "DELETE FROM symbols WHERE file_path = ?1;",
            params![file_path],
        )
        .map_err(BombeError::from)?;
        conn.execute("DELETE FROM files WHERE path = ?1;", params![file_path])
            .map_err(BombeError::from)?;
        Ok(())
    }

    /// Rename a file in the index, moving all associated symbols, edges, and
    /// external deps to the new path.
    fn rename_file(&self, old_path: &str, new_path: &str) -> PyResult<()> {
        let conn = self.connect()?;

        // Fetch old file row.
        let source = conn.query_row(
            "SELECT language, content_hash, size_bytes, last_indexed_at \
             FROM files WHERE path = ?1;",
            params![old_path],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<i64>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                ))
            },
        );

        let (language, content_hash, size_bytes, last_indexed_at) = match source {
            Ok(v) => v,
            Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(()),
            Err(e) => return Err(BombeError::from(e).into()),
        };

        conn.execute(
            "INSERT INTO files (path, language, content_hash, size_bytes, last_indexed_at) \
             VALUES (?1, ?2, ?3, ?4, ?5) \
             ON CONFLICT(path) DO UPDATE SET \
                 language = excluded.language, \
                 content_hash = excluded.content_hash, \
                 size_bytes = excluded.size_bytes, \
                 last_indexed_at = excluded.last_indexed_at;",
            params![
                new_path,
                language,
                content_hash,
                size_bytes,
                last_indexed_at
            ],
        )
        .map_err(BombeError::from)?;

        conn.execute(
            "UPDATE symbols SET file_path = ?1 WHERE file_path = ?2;",
            params![new_path, old_path],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "UPDATE edges SET file_path = ?1 WHERE file_path = ?2;",
            params![new_path, old_path],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "UPDATE external_deps SET file_path = ?1 WHERE file_path = ?2;",
            params![new_path, old_path],
        )
        .map_err(BombeError::from)?;
        conn.execute("DELETE FROM files WHERE path = ?1;", params![old_path])
            .map_err(BombeError::from)?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Backup
    // -----------------------------------------------------------------------

    /// Create a backup of the database at `destination` using the SQLite
    /// backup API.  Returns the resolved path as a string.
    fn backup_to(&self, destination: std::path::PathBuf) -> PyResult<String> {
        let backup_path = expand_tilde(&destination.to_string_lossy());
        let resolved = if backup_path.is_absolute() {
            backup_path
        } else {
            std::env::current_dir()
                .map_err(BombeError::Io)?
                .join(&backup_path)
        };
        if let Some(parent) = resolved.parent() {
            std::fs::create_dir_all(parent).map_err(BombeError::Io)?;
        }

        let src_conn = self.connect()?;
        let mut dst_conn = Connection::open(&resolved).map_err(BombeError::from)?;
        let backup =
            rusqlite::backup::Backup::new(&src_conn, &mut dst_conn).map_err(BombeError::from)?;
        backup
            .run_to_completion(100, std::time::Duration::from_millis(10), None)
            .map_err(BombeError::from)?;
        Ok(resolved.to_string_lossy().into_owned())
    }

    /// Restore the database from a backup file.
    fn restore_from(&self, source: std::path::PathBuf) -> PyResult<()> {
        let source_path = expand_tilde(&source.to_string_lossy());
        let resolved = if source_path.is_absolute() {
            source_path
        } else {
            std::env::current_dir()
                .map_err(BombeError::Io)?
                .join(&source_path)
        };
        if !resolved.exists() {
            return Err(BombeError::Io(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                format!("Backup file does not exist: {}", resolved.display()),
            ))
            .into());
        }
        let src_conn = Connection::open(&resolved).map_err(BombeError::from)?;
        let mut dst_conn = self.connect()?;
        let backup =
            rusqlite::backup::Backup::new(&src_conn, &mut dst_conn).map_err(BombeError::from)?;
        backup
            .run_to_completion(100, std::time::Duration::from_millis(10), None)
            .map_err(BombeError::from)?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Sync queue
    // -----------------------------------------------------------------------

    /// Enqueue a new sync delta and return its row id.
    fn enqueue_sync_delta(
        &self,
        repo_id: &str,
        local_snapshot: &str,
        payload_json: &str,
    ) -> PyResult<i64> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO sync_queue(repo_id, local_snapshot, payload_json, status) \
             VALUES (?1, ?2, ?3, 'queued');",
            params![repo_id, local_snapshot, payload_json],
        )
        .map_err(BombeError::from)?;
        Ok(conn.last_insert_rowid())
    }

    /// List pending (queued or retry) sync deltas for a repo, up to `limit`.
    #[pyo3(signature = (repo_id, limit=None))]
    fn list_pending_sync_deltas(
        &self,
        py: Python<'_>,
        repo_id: &str,
        limit: Option<i64>,
    ) -> PyResult<PyObject> {
        let effective_limit = std::cmp::max(1, limit.unwrap_or(20));
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "SELECT id, repo_id, local_snapshot, payload_json, status, \
                        attempt_count, last_error, created_at, updated_at \
                 FROM sync_queue \
                 WHERE repo_id = ?1 AND status IN ('queued', 'retry') \
                 ORDER BY created_at ASC \
                 LIMIT ?2;",
            )
            .map_err(BombeError::from)?;

        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut result: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(params![repo_id, effective_limit])
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            result.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, result.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    /// Mark a sync delta with a new status and optionally record an error.
    #[pyo3(signature = (queue_id, status, last_error=None))]
    fn mark_sync_delta_status(
        &self,
        queue_id: i64,
        status: &str,
        last_error: Option<&str>,
    ) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "UPDATE sync_queue \
             SET status = ?1, last_error = ?2, \
                 attempt_count = attempt_count + 1, \
                 updated_at = CURRENT_TIMESTAMP \
             WHERE id = ?3;",
            params![status, last_error, queue_id],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Normalise sync queue entries with unknown statuses back to 'retry'.
    /// Returns the number of rows fixed.
    fn normalize_sync_queue_statuses(&self) -> PyResult<i64> {
        let allowed: HashSet<&str> = ["queued", "retry", "pushed", "failed"]
            .iter()
            .copied()
            .collect();
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare("SELECT id, status FROM sync_queue;")
            .map_err(BombeError::from)?;
        let rows: Vec<(i64, String)> = stmt
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
            .map_err(BombeError::from)?
            .filter_map(|r| r.ok())
            .collect();

        let to_fix: Vec<i64> = rows
            .iter()
            .filter(|(_, s)| !allowed.contains(s.as_str()))
            .map(|(id, _)| *id)
            .collect();

        for queue_id in &to_fix {
            conn.execute(
                "UPDATE sync_queue \
                 SET status = 'retry', updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ?1;",
                params![queue_id],
            )
            .map_err(BombeError::from)?;
        }
        Ok(to_fix.len() as i64)
    }

    // -----------------------------------------------------------------------
    // Artifacts
    // -----------------------------------------------------------------------

    /// Pin an artifact to a (repo_id, snapshot_id) pair.
    fn set_artifact_pin(
        &self,
        repo_id: &str,
        snapshot_id: &str,
        artifact_id: &str,
    ) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO artifact_pins(repo_id, snapshot_id, artifact_id, pinned_at) \
             VALUES (?1, ?2, ?3, CURRENT_TIMESTAMP) \
             ON CONFLICT(repo_id, snapshot_id) DO UPDATE SET \
                 artifact_id = excluded.artifact_id, \
                 pinned_at = excluded.pinned_at;",
            params![repo_id, snapshot_id, artifact_id],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Get the artifact id pinned to a (repo_id, snapshot_id) pair, or None.
    fn get_artifact_pin(&self, repo_id: &str, snapshot_id: &str) -> PyResult<Option<String>> {
        let conn = self.connect()?;
        let result: Result<String, _> = conn.query_row(
            "SELECT artifact_id FROM artifact_pins \
             WHERE repo_id = ?1 AND snapshot_id = ?2 LIMIT 1;",
            params![repo_id, snapshot_id],
            |row| row.get(0),
        );
        match result {
            Ok(v) => Ok(Some(v)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(BombeError::from(e).into()),
        }
    }

    /// Quarantine an artifact, recording the reason.
    fn quarantine_artifact(&self, artifact_id: &str, reason: &str) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO artifact_quarantine(artifact_id, reason, quarantined_at) \
             VALUES (?1, ?2, CURRENT_TIMESTAMP) \
             ON CONFLICT(artifact_id) DO UPDATE SET \
                 reason = excluded.reason, \
                 quarantined_at = excluded.quarantined_at;",
            params![artifact_id, reason],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Check whether an artifact has been quarantined.
    fn is_artifact_quarantined(&self, artifact_id: &str) -> PyResult<bool> {
        let conn = self.connect()?;
        let result: Result<String, _> = conn.query_row(
            "SELECT artifact_id FROM artifact_quarantine WHERE artifact_id = ?1 LIMIT 1;",
            params![artifact_id],
            |row| row.get(0),
        );
        Ok(result.is_ok())
    }

    /// List quarantined artifacts, most recent first.
    #[pyo3(signature = (limit=None))]
    fn list_quarantined_artifacts(&self, py: Python<'_>, limit: Option<i64>) -> PyResult<PyObject> {
        let effective_limit = std::cmp::max(1, limit.unwrap_or(100));
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "SELECT artifact_id, reason, quarantined_at \
                 FROM artifact_quarantine \
                 ORDER BY quarantined_at DESC \
                 LIMIT ?1;",
            )
            .map_err(BombeError::from)?;

        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut result: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(params![effective_limit])
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            result.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, result.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    // -----------------------------------------------------------------------
    // Circuit breakers
    // -----------------------------------------------------------------------

    /// Set (upsert) the circuit breaker state for a repo.
    #[pyo3(signature = (repo_id, state, failure_count, opened_at_utc=None))]
    fn set_circuit_breaker_state(
        &self,
        repo_id: &str,
        state: &str,
        failure_count: i64,
        opened_at_utc: Option<&str>,
    ) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO circuit_breakers(repo_id, state, failure_count, opened_at_utc) \
             VALUES (?1, ?2, ?3, ?4) \
             ON CONFLICT(repo_id) DO UPDATE SET \
                 state = excluded.state, \
                 failure_count = excluded.failure_count, \
                 opened_at_utc = excluded.opened_at_utc;",
            params![
                repo_id,
                state,
                std::cmp::max(0, failure_count),
                opened_at_utc
            ],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Get the circuit breaker state for a repo, or None if not set.
    fn get_circuit_breaker_state(
        &self,
        py: Python<'_>,
        repo_id: &str,
    ) -> PyResult<Option<PyObject>> {
        let conn = self.connect()?;
        let result = conn.query_row(
            "SELECT state, failure_count, opened_at_utc \
             FROM circuit_breakers WHERE repo_id = ?1 LIMIT 1;",
            params![repo_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, Option<String>>(2)?,
                ))
            },
        );
        match result {
            Ok((state, failure_count, opened_at_utc)) => {
                let dict = PyDict::new(py);
                dict.set_item("state", state)?;
                dict.set_item("failure_count", failure_count)?;
                match opened_at_utc {
                    Some(v) => dict.set_item("opened_at_utc", v)?,
                    None => dict.set_item("opened_at_utc", py.None())?,
                }
                Ok(Some(dict.into_any().unbind()))
            }
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(BombeError::from(e).into()),
        }
    }

    // -----------------------------------------------------------------------
    // Events / metrics
    // -----------------------------------------------------------------------

    /// Record a sync event.
    #[pyo3(signature = (repo_id, level, event_type, detail=None))]
    fn record_sync_event(
        &self,
        py: Python<'_>,
        repo_id: &str,
        level: &str,
        event_type: &str,
        detail: Option<PyObject>,
    ) -> PyResult<()> {
        let detail_json: Option<String> = match detail {
            Some(obj) => {
                let bound = obj.bind(py);
                if bound.is_none() {
                    None
                } else {
                    // Use Python's json.dumps for faithful serialisation.
                    let json_mod = py.import("json")?;
                    let kwargs = PyDict::new(py);
                    kwargs.set_item("sort_keys", true)?;
                    let dumped = json_mod.call_method("dumps", (bound,), Some(&kwargs))?;
                    Some(dumped.extract::<String>()?)
                }
            }
            None => None,
        };
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO sync_events(repo_id, level, event_type, detail_json) \
             VALUES (?1, ?2, ?3, ?4);",
            params![repo_id, level, event_type, detail_json],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Record a tool metric observation.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (tool_name, latency_ms, success, mode, repo_id=None, result_size=None, error_message=None))]
    fn record_tool_metric(
        &self,
        tool_name: &str,
        latency_ms: f64,
        success: bool,
        mode: &str,
        repo_id: Option<&str>,
        result_size: Option<i64>,
        error_message: Option<&str>,
    ) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO tool_metrics(repo_id, tool_name, latency_ms, success, mode, result_size, error_message) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7);",
            params![
                repo_id,
                tool_name,
                latency_ms,
                success as i64,
                mode,
                result_size,
                error_message,
            ],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Retrieve recent tool metrics for a given tool, most recent first.
    #[pyo3(signature = (tool_name, limit=None))]
    fn recent_tool_metrics(
        &self,
        py: Python<'_>,
        tool_name: &str,
        limit: Option<i64>,
    ) -> PyResult<PyObject> {
        let effective_limit = std::cmp::max(1, limit.unwrap_or(50));
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "SELECT repo_id, tool_name, latency_ms, success, mode, \
                        result_size, error_message, created_at \
                 FROM tool_metrics \
                 WHERE tool_name = ?1 \
                 ORDER BY created_at DESC \
                 LIMIT ?2;",
            )
            .map_err(BombeError::from)?;

        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut result: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(params![tool_name, effective_limit])
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            result.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, result.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    // -----------------------------------------------------------------------
    // Diagnostics
    // -----------------------------------------------------------------------

    /// Record an indexing diagnostic entry.
    #[pyo3(signature = (run_id, stage, category, message, hint=None, file_path=None, language=None, severity=None))]
    #[allow(clippy::too_many_arguments)]
    fn record_indexing_diagnostic(
        &self,
        run_id: &str,
        stage: &str,
        category: &str,
        message: &str,
        hint: Option<&str>,
        file_path: Option<&str>,
        language: Option<&str>,
        severity: Option<&str>,
    ) -> PyResult<()> {
        let effective_severity = severity.unwrap_or("error");
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO indexing_diagnostics( \
                 run_id, stage, category, severity, file_path, language, message, hint \
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8);",
            params![
                run_id,
                stage,
                category,
                effective_severity,
                file_path,
                language,
                message,
                hint,
            ],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// List indexing diagnostics with optional filters.
    #[pyo3(signature = (limit=None, offset=None, run_id=None, stage=None, severity=None))]
    fn list_indexing_diagnostics(
        &self,
        py: Python<'_>,
        limit: Option<i64>,
        offset: Option<i64>,
        run_id: Option<&str>,
        stage: Option<&str>,
        severity: Option<&str>,
    ) -> PyResult<PyObject> {
        let effective_limit = std::cmp::max(1, limit.unwrap_or(100));
        let effective_offset = std::cmp::max(0, offset.unwrap_or(0));

        let mut where_clauses: Vec<String> = Vec::new();
        let mut param_values: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

        if let Some(rid) = run_id {
            where_clauses.push("run_id = ?".to_string());
            param_values.push(Box::new(rid.to_string()));
        }
        if let Some(st) = stage {
            where_clauses.push("stage = ?".to_string());
            param_values.push(Box::new(st.to_string()));
        }
        if let Some(sev) = severity {
            where_clauses.push("severity = ?".to_string());
            param_values.push(Box::new(sev.to_string()));
        }

        let where_sql = if where_clauses.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", where_clauses.join(" AND "))
        };

        param_values.push(Box::new(effective_limit));
        param_values.push(Box::new(effective_offset));

        let sql = format!(
            "SELECT id, run_id, stage, category, severity, file_path, language, \
                    message, hint, created_at \
             FROM indexing_diagnostics \
             {} \
             ORDER BY id DESC \
             LIMIT ? OFFSET ?;",
            where_sql
        );

        let conn = self.connect()?;
        let mut stmt = conn.prepare(&sql).map_err(BombeError::from)?;

        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            param_values.iter().map(|b| b.as_ref()).collect();

        let mut result: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(param_refs.as_slice())
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            result.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, result.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    /// Return a summary dict of indexing diagnostics, optionally filtered by
    /// run_id.
    #[pyo3(signature = (run_id=None))]
    fn summarize_indexing_diagnostics(
        &self,
        py: Python<'_>,
        run_id: Option<&str>,
    ) -> PyResult<PyObject> {
        let (where_sql, where_params): (String, Vec<Box<dyn rusqlite::types::ToSql>>) = match run_id
        {
            Some(rid) => (
                "WHERE run_id = ?1".to_string(),
                vec![Box::new(rid.to_string()) as Box<dyn rusqlite::types::ToSql>],
            ),
            None => (String::new(), Vec::new()),
        };

        let conn = self.connect()?;

        // Total count.
        let total_sql = format!(
            "SELECT COUNT(*) AS count FROM indexing_diagnostics {};",
            where_sql
        );
        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            where_params.iter().map(|b| b.as_ref()).collect();
        let total: i64 = conn
            .query_row(&total_sql, param_refs.as_slice(), |row| row.get(0))
            .map_err(BombeError::from)?;

        // Group by stage.
        let by_stage_sql = format!(
            "SELECT stage, COUNT(*) AS count FROM indexing_diagnostics {} \
             GROUP BY stage ORDER BY stage ASC;",
            where_sql
        );
        let mut by_stage_stmt = conn.prepare(&by_stage_sql).map_err(BombeError::from)?;
        let by_stage_dict = PyDict::new(py);
        {
            let mut rows = by_stage_stmt
                .query(param_refs.as_slice())
                .map_err(BombeError::from)?;
            while let Some(row) = rows.next().map_err(BombeError::from)? {
                let stage: String = row.get(0).map_err(BombeError::from)?;
                let count: i64 = row.get(1).map_err(BombeError::from)?;
                by_stage_dict.set_item(stage, count)?;
            }
        }

        // Group by category.
        let by_category_sql = format!(
            "SELECT category, COUNT(*) AS count FROM indexing_diagnostics {} \
             GROUP BY category ORDER BY category ASC;",
            where_sql
        );
        let mut by_category_stmt = conn.prepare(&by_category_sql).map_err(BombeError::from)?;
        let by_category_dict = PyDict::new(py);
        {
            let mut rows = by_category_stmt
                .query(param_refs.as_slice())
                .map_err(BombeError::from)?;
            while let Some(row) = rows.next().map_err(BombeError::from)? {
                let category: String = row.get(0).map_err(BombeError::from)?;
                let count: i64 = row.get(1).map_err(BombeError::from)?;
                by_category_dict.set_item(category, count)?;
            }
        }

        // Group by severity.
        let by_severity_sql = format!(
            "SELECT severity, COUNT(*) AS count FROM indexing_diagnostics {} \
             GROUP BY severity ORDER BY severity ASC;",
            where_sql
        );
        let mut by_severity_stmt = conn.prepare(&by_severity_sql).map_err(BombeError::from)?;
        let by_severity_dict = PyDict::new(py);
        {
            let mut rows = by_severity_stmt
                .query(param_refs.as_slice())
                .map_err(BombeError::from)?;
            while let Some(row) = rows.next().map_err(BombeError::from)? {
                let sev: String = row.get(0).map_err(BombeError::from)?;
                let count: i64 = row.get(1).map_err(BombeError::from)?;
                by_severity_dict.set_item(sev, count)?;
            }
        }

        // Latest run_id.
        let latest_sql = format!(
            "SELECT run_id FROM indexing_diagnostics {} ORDER BY id DESC LIMIT 1;",
            where_sql
        );
        let latest_run_id: Option<String> = conn
            .query_row(&latest_sql, param_refs.as_slice(), |row| row.get(0))
            .ok();

        let result = PyDict::new(py);
        result.set_item("total", total)?;
        match run_id {
            Some(rid) => result.set_item("run_id", rid)?,
            None => result.set_item("run_id", py.None())?,
        }
        match &latest_run_id {
            Some(v) => result.set_item("latest_run_id", v)?,
            None => result.set_item("latest_run_id", py.None())?,
        }
        result.set_item("by_stage", by_stage_dict)?;
        result.set_item("by_category", by_category_dict)?;
        result.set_item("by_severity", by_severity_dict)?;

        Ok(result.into_any().unbind())
    }

    /// Delete indexing diagnostics, optionally filtered by run_id.
    /// Returns the number of rows deleted.
    #[pyo3(signature = (run_id=None))]
    fn clear_indexing_diagnostics(&self, run_id: Option<&str>) -> PyResult<i64> {
        let conn = self.connect()?;
        let deleted = match run_id {
            Some(rid) => conn
                .execute(
                    "DELETE FROM indexing_diagnostics WHERE run_id = ?1;",
                    params![rid],
                )
                .map_err(BombeError::from)?,
            None => conn
                .execute("DELETE FROM indexing_diagnostics;", [])
                .map_err(BombeError::from)?,
        };
        Ok(deleted as i64)
    }

    // -----------------------------------------------------------------------
    // Signing keys
    // -----------------------------------------------------------------------

    /// Upsert a trusted signing key for a repo.
    #[pyo3(signature = (repo_id, key_id, algorithm, public_key, purpose=None, active=None))]
    fn set_trusted_signing_key(
        &self,
        repo_id: &str,
        key_id: &str,
        algorithm: &str,
        public_key: &str,
        purpose: Option<&str>,
        active: Option<bool>,
    ) -> PyResult<()> {
        let effective_purpose = purpose.unwrap_or("default");
        let effective_active = active.unwrap_or(true) as i64;
        let conn = self.connect()?;
        conn.execute(
            "INSERT INTO trusted_signing_keys( \
                 repo_id, key_id, algorithm, public_key, purpose, active, updated_at \
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, CURRENT_TIMESTAMP) \
             ON CONFLICT(repo_id, key_id) DO UPDATE SET \
                 algorithm = excluded.algorithm, \
                 public_key = excluded.public_key, \
                 purpose = excluded.purpose, \
                 active = excluded.active, \
                 updated_at = excluded.updated_at;",
            params![
                repo_id,
                key_id,
                algorithm,
                public_key,
                effective_purpose,
                effective_active,
            ],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Get a single trusted signing key, or None.
    fn get_trusted_signing_key(
        &self,
        py: Python<'_>,
        repo_id: &str,
        key_id: &str,
    ) -> PyResult<Option<PyObject>> {
        let conn = self.connect()?;
        let result = conn.query_row(
            "SELECT repo_id, key_id, algorithm, public_key, purpose, active, updated_at \
             FROM trusted_signing_keys \
             WHERE repo_id = ?1 AND key_id = ?2 LIMIT 1;",
            params![repo_id, key_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, Option<String>>(6)?,
                ))
            },
        );
        match result {
            Ok((repo, kid, algo, pubkey, purpose, active_int, updated_at)) => {
                let dict = PyDict::new(py);
                dict.set_item("repo_id", repo)?;
                dict.set_item("key_id", kid)?;
                dict.set_item("algorithm", algo)?;
                dict.set_item("public_key", pubkey)?;
                dict.set_item("purpose", purpose)?;
                dict.set_item("active", active_int != 0)?;
                match updated_at {
                    Some(v) => dict.set_item("updated_at", v)?,
                    None => dict.set_item("updated_at", py.None())?,
                }
                Ok(Some(dict.into_any().unbind()))
            }
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(BombeError::from(e).into()),
        }
    }

    /// List trusted signing keys for a repo, optionally filtered to active
    /// only.
    #[pyo3(signature = (repo_id, active_only=None))]
    fn list_trusted_signing_keys(
        &self,
        py: Python<'_>,
        repo_id: &str,
        active_only: Option<bool>,
    ) -> PyResult<PyObject> {
        let effective_active_only = active_only.unwrap_or(true);
        let conn = self.connect()?;

        let sql = if effective_active_only {
            "SELECT repo_id, key_id, algorithm, public_key, purpose, active, updated_at \
             FROM trusted_signing_keys \
             WHERE repo_id = ?1 AND active = 1 \
             ORDER BY key_id ASC;"
        } else {
            "SELECT repo_id, key_id, algorithm, public_key, purpose, active, updated_at \
             FROM trusted_signing_keys \
             WHERE repo_id = ?1 \
             ORDER BY key_id ASC;"
        };

        let mut stmt = conn.prepare(sql).map_err(BombeError::from)?;
        let mut rows = stmt.query(params![repo_id]).map_err(BombeError::from)?;
        let mut result: Vec<Bound<'_, PyDict>> = Vec::new();

        while let Some(row) = rows.next().map_err(BombeError::from)? {
            let dict = PyDict::new(py);
            dict.set_item(
                "repo_id",
                row.get::<_, String>(0).map_err(BombeError::from)?,
            )?;
            dict.set_item("key_id", row.get::<_, String>(1).map_err(BombeError::from)?)?;
            dict.set_item(
                "algorithm",
                row.get::<_, String>(2).map_err(BombeError::from)?,
            )?;
            dict.set_item(
                "public_key",
                row.get::<_, String>(3).map_err(BombeError::from)?,
            )?;
            dict.set_item(
                "purpose",
                row.get::<_, String>(4).map_err(BombeError::from)?,
            )?;
            let active_int: i64 = row.get(5).map_err(BombeError::from)?;
            dict.set_item("active", active_int != 0)?;
            let updated_at: Option<String> = row.get(6).map_err(BombeError::from)?;
            match updated_at {
                Some(v) => dict.set_item("updated_at", v)?,
                None => dict.set_item("updated_at", py.None())?,
            }
            result.push(dict);
        }

        let list = PyList::new(py, result.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }
}
