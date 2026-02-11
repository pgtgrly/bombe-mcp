//! SQLite shard catalog for cross-repo sharding and federation.
//!
//! Direct port of the Python `bombe.store.sharding.catalog.ShardCatalog` class.
//! Manages a SQLite catalog database that tracks registered shards, their
//! exported symbols, and cross-repo edges between symbols in different repos.

use std::path::PathBuf;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rusqlite::{params, Connection};

use crate::errors::{BombeError, BombeResult};
use crate::query::guards::MAX_EXPORTED_SYMBOLS_REFRESH;
use crate::store::database::Database;

// ---------------------------------------------------------------------------
// Schema constants
// ---------------------------------------------------------------------------

/// Current catalog schema version.
const CATALOG_SCHEMA_VERSION: i64 = 1;

/// DDL statements to create the catalog tables and indexes.
const CATALOG_SCHEMA_STATEMENTS: &[&str] = &[
    "CREATE TABLE IF NOT EXISTS catalog_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );",
    "CREATE TABLE IF NOT EXISTS shards (
        repo_id TEXT PRIMARY KEY,
        repo_path TEXT NOT NULL,
        db_path TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_indexed_at TEXT,
        symbol_count INTEGER DEFAULT 0,
        edge_count INTEGER DEFAULT 0,
        last_seen_epoch INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );",
    "CREATE TABLE IF NOT EXISTS cross_repo_edges (
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
    );",
    "CREATE TABLE IF NOT EXISTS exported_symbols (
        repo_id TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        file_path TEXT NOT NULL,
        visibility TEXT,
        pagerank_score REAL DEFAULT 0.0,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(repo_id, qualified_name, file_path)
    );",
    "CREATE INDEX IF NOT EXISTS idx_cross_edges_source \
         ON cross_repo_edges(source_repo_id, source_qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_cross_edges_target \
         ON cross_repo_edges(target_repo_id, target_qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_exported_name ON exported_symbols(name);",
    "CREATE INDEX IF NOT EXISTS idx_exported_qualified ON exported_symbols(qualified_name);",
    "CREATE INDEX IF NOT EXISTS idx_exported_kind ON exported_symbols(kind);",
];

// ---------------------------------------------------------------------------
// Helper: tilde expansion
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
fn row_to_pydict<'py>(
    py: Python<'py>,
    row: &rusqlite::Row<'_>,
    col_names: &[String],
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (i, name) in col_names.iter().enumerate() {
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
// ShardCatalog
// ---------------------------------------------------------------------------

/// Manages a SQLite catalog database for cross-repo sharding.
///
/// Mirrors the Python `ShardCatalog` class: each public method opens its own
/// connection to the catalog database so the caller never needs to manage
/// connection lifetime.
#[pyclass]
pub struct ShardCatalog {
    db_path: PathBuf,
}

impl ShardCatalog {
    /// Open a new SQLite connection to `self.db_path`, enable foreign keys,
    /// and set row factory-like behaviour through column-name-based access.
    fn connect(&self) -> BombeResult<Connection> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute_batch("PRAGMA foreign_keys = ON;")?;
        Ok(conn)
    }

    /// Read the current schema version from catalog_meta.
    fn get_schema_version(conn: &Connection) -> i64 {
        let result: Result<String, _> = conn.query_row(
            "SELECT value FROM catalog_meta WHERE key = 'schema_version';",
            [],
            |row| row.get(0),
        );
        match result {
            Ok(v) => v.parse::<i64>().unwrap_or(0),
            Err(_) => 0,
        }
    }

    /// Set the schema version in catalog_meta.
    fn set_schema_version(conn: &Connection, version: i64) -> BombeResult<()> {
        conn.execute(
            "INSERT INTO catalog_meta(key, value) \
             VALUES('schema_version', ?1) \
             ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            params![version.to_string()],
        )?;
        Ok(())
    }

    /// Run any pending schema migrations.
    fn migrate_schema(conn: &Connection) -> BombeResult<()> {
        let mut current = Self::get_schema_version(conn);
        while current < CATALOG_SCHEMA_VERSION {
            let next = current + 1;
            // Version 1: initial schema created by CATALOG_SCHEMA_STATEMENTS.
            if next == 1 {
                // No additional migration needed for the initial schema.
            }
            Self::set_schema_version(conn, next)?;
            current = next;
        }
        Ok(())
    }
}

#[pymethods]
impl ShardCatalog {
    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /// Create a new `ShardCatalog`.  The path is expanded and parent
    /// directories are created if they do not already exist.
    #[new]
    fn new(catalog_db_path: PathBuf) -> PyResult<Self> {
        let db_str = catalog_db_path.to_string_lossy();
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
        let catalog = Self { db_path: resolved };
        // Initialise schema on construction (matching Python __init__ + init_schema pattern).
        catalog.init_schema()?;
        Ok(catalog)
    }

    /// Initialise the catalog schema: set WAL mode, create all tables and
    /// indexes, then run pending migrations.
    pub fn init_schema(&self) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute_batch("PRAGMA journal_mode = WAL;")
            .map_err(BombeError::from)?;
        for stmt in CATALOG_SCHEMA_STATEMENTS {
            conn.execute_batch(stmt).map_err(BombeError::from)?;
        }
        Self::migrate_schema(&conn)?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Generic query (exposed to Python)
    // -----------------------------------------------------------------------

    /// Execute an arbitrary SQL statement and return a list of Python dicts.
    #[pyo3(signature = (sql, params=None))]
    pub fn query(
        &self,
        py: Python<'_>,
        sql: &str,
        params: Option<Vec<PyObject>>,
    ) -> PyResult<PyObject> {
        let conn = self.connect()?;
        let mut stmt = conn.prepare(sql).map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();

        let params_vec = params.unwrap_or_default();
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

    // -----------------------------------------------------------------------
    // Shard management
    // -----------------------------------------------------------------------

    /// Register a shard by repo_id, repo_path, and db_path.
    /// Uses INSERT OR REPLACE into the shards table.
    pub fn register_shard(&self, repo_id: &str, repo_path: &str, db_path: &str) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "INSERT OR REPLACE INTO shards(\
                 repo_id, repo_path, db_path, enabled, updated_at\
             ) VALUES (?1, ?2, ?3, 1, CURRENT_TIMESTAMP);",
            params![repo_id, repo_path, db_path],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    /// Unregister a shard: delete its cross-repo edges, exported symbols,
    /// and shard row.
    pub fn unregister_shard(&self, repo_id: &str) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "DELETE FROM cross_repo_edges WHERE source_repo_id = ?1 OR target_repo_id = ?1;",
            params![repo_id],
        )
        .map_err(BombeError::from)?;
        conn.execute(
            "DELETE FROM exported_symbols WHERE repo_id = ?1;",
            params![repo_id],
        )
        .map_err(BombeError::from)?;
        conn.execute("DELETE FROM shards WHERE repo_id = ?1;", params![repo_id])
            .map_err(BombeError::from)?;
        Ok(())
    }

    /// List all shards, optionally filtered to enabled only.
    /// Returns a list of Python dicts with shard info fields.
    #[pyo3(signature = (enabled_only=true))]
    pub fn list_shards(&self, py: Python<'_>, enabled_only: bool) -> PyResult<PyObject> {
        let conn = self.connect()?;
        let sql = if enabled_only {
            "SELECT repo_id, repo_path, db_path, enabled, last_indexed_at, \
                    symbol_count, edge_count \
             FROM shards WHERE enabled = 1 ORDER BY repo_id ASC;"
        } else {
            "SELECT repo_id, repo_path, db_path, enabled, last_indexed_at, \
                    symbol_count, edge_count \
             FROM shards ORDER BY repo_id ASC;"
        };
        let mut stmt = conn.prepare(sql).map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut rows_out: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt.query([]).map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            rows_out.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, rows_out.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    /// Return shard info by repo_id, or None if not found.
    /// Returns a Python dict with shard info fields.
    pub fn get_shard(&self, py: Python<'_>, repo_id: &str) -> PyResult<Option<PyObject>> {
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "SELECT repo_id, repo_path, db_path, enabled, last_indexed_at, \
                        symbol_count, edge_count \
                 FROM shards WHERE repo_id = ?1 LIMIT 1;",
            )
            .map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut rows = stmt.query(params![repo_id]).map_err(BombeError::from)?;
        match rows.next().map_err(BombeError::from)? {
            Some(row) => {
                let dict = row_to_pydict(py, row, &col_names)?;
                Ok(Some(dict.into_any().unbind()))
            }
            None => Ok(None),
        }
    }

    /// Update symbol_count, edge_count, last_indexed_at, updated_at for a
    /// shard.
    pub fn update_shard_stats(
        &self,
        repo_id: &str,
        symbol_count: i64,
        edge_count: i64,
    ) -> PyResult<()> {
        let conn = self.connect()?;
        conn.execute(
            "UPDATE shards \
             SET symbol_count = ?1, \
                 edge_count = ?2, \
                 last_indexed_at = CURRENT_TIMESTAMP, \
                 updated_at = CURRENT_TIMESTAMP \
             WHERE repo_id = ?3;",
            params![symbol_count, edge_count, repo_id],
        )
        .map_err(BombeError::from)?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Exported symbol cache
    // -----------------------------------------------------------------------

    /// Read symbols from a shard Database, write to exported_symbols.
    /// Deletes old entries for repo_id first.  Respects
    /// `MAX_EXPORTED_SYMBOLS_REFRESH` limit.  Returns count of symbols
    /// refreshed.
    pub fn refresh_exported_symbols(
        &self,
        py: Python<'_>,
        repo_id: &str,
        db: &Database,
    ) -> PyResult<i64> {
        // Query symbols from the shard database.
        let limit_params: Vec<PyObject> = vec![MAX_EXPORTED_SYMBOLS_REFRESH
            .into_pyobject(py)?
            .into_any()
            .unbind()];
        let symbols_obj = db.query(
            py,
            "SELECT qualified_name, name, kind, file_path, visibility, pagerank_score \
             FROM symbols \
             ORDER BY pagerank_score DESC \
             LIMIT ?1;",
            Some(limit_params),
        )?;

        // Extract the list of dicts from the returned PyObject.
        let symbols_list = symbols_obj.bind(py);
        let symbols: &Bound<'_, PyList> = symbols_list.downcast::<PyList>()?;

        let conn = self.connect()?;
        conn.execute(
            "DELETE FROM exported_symbols WHERE repo_id = ?1;",
            params![repo_id],
        )
        .map_err(BombeError::from)?;

        let mut count: i64 = 0;
        for sym_obj in symbols.iter() {
            let sym: &Bound<'_, PyDict> = sym_obj.downcast::<PyDict>()?;
            let qualified_name: String = sym
                .get_item("qualified_name")?
                .ok_or_else(|| BombeError::Database("missing qualified_name".into()))?
                .extract()?;
            let name: String = sym
                .get_item("name")?
                .ok_or_else(|| BombeError::Database("missing name".into()))?
                .extract()?;
            let kind: String = sym
                .get_item("kind")?
                .ok_or_else(|| BombeError::Database("missing kind".into()))?
                .extract()?;
            let file_path: String = sym
                .get_item("file_path")?
                .ok_or_else(|| BombeError::Database("missing file_path".into()))?
                .extract()?;
            let visibility: Option<String> =
                sym.get_item("visibility")?.and_then(|v| v.extract().ok());
            let pagerank_score: f64 = sym
                .get_item("pagerank_score")?
                .map(|v| v.extract().unwrap_or(0.0))
                .unwrap_or(0.0);

            conn.execute(
                "INSERT OR REPLACE INTO exported_symbols(\
                     repo_id, qualified_name, name, kind, file_path, \
                     visibility, pagerank_score, updated_at\
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, CURRENT_TIMESTAMP);",
                params![
                    repo_id,
                    qualified_name,
                    name,
                    kind,
                    file_path,
                    visibility,
                    pagerank_score,
                ],
            )
            .map_err(BombeError::from)?;
            count += 1;
        }
        Ok(count)
    }

    /// Search exported symbols by name LIKE pattern.
    /// Filter by kind if not "any".  Returns a list of Python dicts.
    #[pyo3(signature = (name, kind="any", limit=20))]
    pub fn search_exported_symbols(
        &self,
        py: Python<'_>,
        name: &str,
        kind: &str,
        limit: i64,
    ) -> PyResult<PyObject> {
        let pattern = format!("%{name}%");
        let safe_limit = std::cmp::max(1, limit);
        let conn = self.connect()?;

        let (sql, params_vec): (&str, Vec<Box<dyn rusqlite::types::ToSql>>) = if kind == "any" {
            (
                "SELECT repo_id, qualified_name, name, kind, file_path, \
                        visibility, pagerank_score \
                 FROM exported_symbols \
                 WHERE name LIKE ?1 \
                 ORDER BY pagerank_score DESC \
                 LIMIT ?2;",
                vec![
                    Box::new(pattern) as Box<dyn rusqlite::types::ToSql>,
                    Box::new(safe_limit),
                ],
            )
        } else {
            (
                "SELECT repo_id, qualified_name, name, kind, file_path, \
                        visibility, pagerank_score \
                 FROM exported_symbols \
                 WHERE name LIKE ?1 AND kind = ?2 \
                 ORDER BY pagerank_score DESC \
                 LIMIT ?3;",
                vec![
                    Box::new(pattern) as Box<dyn rusqlite::types::ToSql>,
                    Box::new(kind.to_string()),
                    Box::new(safe_limit),
                ],
            )
        };

        let mut stmt = conn.prepare(sql).map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            params_vec.iter().map(|b| b.as_ref()).collect();
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

    /// Find exported symbols matching module_name for cross-repo resolution.
    ///
    /// Language-aware matching:
    /// - TypeScript: match the last segment of the module path as a name.
    /// - Python/Java/Go/other: prefix match on qualified_name.
    pub fn resolve_external_import(
        &self,
        py: Python<'_>,
        module_name: &str,
        language: &str,
    ) -> PyResult<PyObject> {
        let lang_lower = language.to_lowercase();
        let conn = self.connect()?;

        let (sql, params_vec): (&str, Vec<Box<dyn rusqlite::types::ToSql>>) =
            if lang_lower == "typescript" {
                let normalized = module_name.replace('/', ".");
                let segments: Vec<&str> = normalized
                    .split('.')
                    .map(|s| s.trim())
                    .filter(|s| !s.is_empty())
                    .collect();
                let last_segment = segments.last().copied().unwrap_or(module_name).to_string();
                (
                    "SELECT repo_id, qualified_name, name, kind, file_path, \
                            visibility, pagerank_score \
                     FROM exported_symbols \
                     WHERE name = ?1 \
                     ORDER BY pagerank_score DESC \
                     LIMIT 20;",
                    vec![Box::new(last_segment) as Box<dyn rusqlite::types::ToSql>],
                )
            } else {
                let prefix = format!("{module_name}%");
                (
                    "SELECT repo_id, qualified_name, name, kind, file_path, \
                            visibility, pagerank_score \
                     FROM exported_symbols \
                     WHERE qualified_name LIKE ?1 \
                     ORDER BY pagerank_score DESC \
                     LIMIT 20;",
                    vec![Box::new(prefix) as Box<dyn rusqlite::types::ToSql>],
                )
            };

        let mut stmt = conn.prepare(sql).map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            params_vec.iter().map(|b| b.as_ref()).collect();
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

    // -----------------------------------------------------------------------
    // Cross-repo edge management
    // -----------------------------------------------------------------------

    /// Upsert cross-repo edges from a list of Python dicts.
    ///
    /// Each dict must have keys: source_repo_id, source_qualified_name,
    /// source_file_path, target_repo_id, target_qualified_name,
    /// target_file_path, relationship, confidence, provenance.
    ///
    /// Returns the count of edges upserted.
    pub fn upsert_cross_repo_edges(
        &self,
        py: Python<'_>,
        edges: &Bound<'_, PyList>,
    ) -> PyResult<i64> {
        if edges.len() == 0 {
            return Ok(0);
        }
        let conn = self.connect()?;
        let mut count: i64 = 0;

        for edge_obj in edges.iter() {
            let edge: &Bound<'_, PyDict> = edge_obj.downcast::<PyDict>()?;

            let source_repo_id: String = edge
                .get_item("source_repo_id")?
                .ok_or_else(|| BombeError::Database("missing source_repo_id".into()))?
                .extract()?;
            let source_qualified_name: String = edge
                .get_item("source_qualified_name")?
                .ok_or_else(|| BombeError::Database("missing source_qualified_name".into()))?
                .extract()?;
            let source_file_path: String = edge
                .get_item("source_file_path")?
                .ok_or_else(|| BombeError::Database("missing source_file_path".into()))?
                .extract()?;
            let target_repo_id: String = edge
                .get_item("target_repo_id")?
                .ok_or_else(|| BombeError::Database("missing target_repo_id".into()))?
                .extract()?;
            let target_qualified_name: String = edge
                .get_item("target_qualified_name")?
                .ok_or_else(|| BombeError::Database("missing target_qualified_name".into()))?
                .extract()?;
            let target_file_path: String = edge
                .get_item("target_file_path")?
                .ok_or_else(|| BombeError::Database("missing target_file_path".into()))?
                .extract()?;
            let relationship: String = edge
                .get_item("relationship")?
                .ok_or_else(|| BombeError::Database("missing relationship".into()))?
                .extract()?;
            let confidence: f64 = edge
                .get_item("confidence")?
                .map(|v| v.extract().unwrap_or(1.0))
                .unwrap_or(1.0);
            let provenance: String = edge
                .get_item("provenance")?
                .map(|v| {
                    v.extract()
                        .unwrap_or_else(|_| "import_resolution".to_string())
                })
                .unwrap_or_else(|| "import_resolution".to_string());

            conn.execute(
                "INSERT OR REPLACE INTO cross_repo_edges(\
                     source_repo_id, source_qualified_name, source_file_path, \
                     target_repo_id, target_qualified_name, target_file_path, \
                     relationship, confidence, provenance, updated_at\
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, CURRENT_TIMESTAMP);",
                params![
                    source_repo_id,
                    source_qualified_name,
                    source_file_path,
                    target_repo_id,
                    target_qualified_name,
                    target_file_path,
                    relationship,
                    confidence,
                    provenance,
                ],
            )
            .map_err(BombeError::from)?;
            count += 1;
        }
        // In rusqlite autocommit mode, each execute is committed.
        // For batch efficiency we could use a transaction, but matching Python
        // pattern of individual inserts for simplicity.
        let _ = py;
        Ok(count)
    }

    /// Get outgoing cross-repo edges from a symbol.
    /// Returns a list of Python dicts.
    pub fn get_cross_repo_edges_from(
        &self,
        py: Python<'_>,
        repo_id: &str,
        symbol_name: &str,
    ) -> PyResult<PyObject> {
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "SELECT source_repo_id, source_qualified_name, source_file_path, \
                        target_repo_id, target_qualified_name, target_file_path, \
                        relationship, confidence, provenance \
                 FROM cross_repo_edges \
                 WHERE source_repo_id = ?1 AND source_qualified_name = ?2 \
                 ORDER BY id ASC;",
            )
            .map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut rows_out: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(params![repo_id, symbol_name])
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            rows_out.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, rows_out.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    /// Get incoming cross-repo edges to a symbol.
    /// Returns a list of Python dicts.
    pub fn get_cross_repo_edges_to(
        &self,
        py: Python<'_>,
        repo_id: &str,
        symbol_name: &str,
    ) -> PyResult<PyObject> {
        let conn = self.connect()?;
        let mut stmt = conn
            .prepare(
                "SELECT source_repo_id, source_qualified_name, source_file_path, \
                        target_repo_id, target_qualified_name, target_file_path, \
                        relationship, confidence, provenance \
                 FROM cross_repo_edges \
                 WHERE target_repo_id = ?1 AND target_qualified_name = ?2 \
                 ORDER BY id ASC;",
            )
            .map_err(BombeError::from)?;
        let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let mut rows_out: Vec<Bound<'_, PyDict>> = Vec::new();
        let mut rows = stmt
            .query(params![repo_id, symbol_name])
            .map_err(BombeError::from)?;
        while let Some(row) = rows.next().map_err(BombeError::from)? {
            rows_out.push(row_to_pydict(py, row, &col_names)?);
        }
        let list = PyList::new(py, rows_out.iter().map(|d| d.as_any()))?;
        Ok(list.into_any().unbind())
    }

    /// Delete all cross-repo edges involving a repo.
    /// Returns the count of deleted rows.
    pub fn delete_cross_repo_edges_for_repo(&self, repo_id: &str) -> PyResult<i64> {
        let conn = self.connect()?;
        let deleted = conn
            .execute(
                "DELETE FROM cross_repo_edges \
                 WHERE source_repo_id = ?1 OR target_repo_id = ?1;",
                params![repo_id],
            )
            .map_err(BombeError::from)?;
        Ok(deleted as i64)
    }

    // -----------------------------------------------------------------------
    // Rust-side helpers for use by router and resolver
    // -----------------------------------------------------------------------

    /// Return the db_path for a shard, or None if the shard doesn't exist.
    /// (Rust-only helper, not exposed to Python.)
    pub fn get_shard_db_path(&self, repo_id: &str) -> PyResult<Option<String>> {
        let conn = self.connect()?;
        let result: Result<String, _> = conn.query_row(
            "SELECT db_path FROM shards WHERE repo_id = ?1 AND enabled = 1 LIMIT 1;",
            params![repo_id],
            |row| row.get(0),
        );
        match result {
            Ok(v) => Ok(Some(v)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(BombeError::from(e).into()),
        }
    }
}
