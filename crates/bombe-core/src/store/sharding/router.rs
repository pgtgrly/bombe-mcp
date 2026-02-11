//! Shard router: routes queries to appropriate shards and manages connection
//! pooling.
//!
//! Direct port of the Python `bombe.store.sharding.router.ShardRouter` class.
//! Uses the `ShardCatalog` to look up shards and exported symbols, then
//! determines which shard databases should be consulted for a given query.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::errors::BombeError;
use crate::query::guards::MAX_SHARDS_PER_QUERY;
use crate::store::database::Database;
use crate::store::sharding::catalog::ShardCatalog;

// ---------------------------------------------------------------------------
// ShardRouter
// ---------------------------------------------------------------------------

/// Routes queries to appropriate shards and manages shard connections.
///
/// Mirrors the Python `ShardRouter` class.  Holds a reference-counted
/// `ShardCatalog` and maintains a mutex-protected connection pool of
/// `Database` instances keyed by repo_id.
#[pyclass]
pub struct ShardRouter {
    catalog: Py<ShardCatalog>,
    max_connections: usize,
    connection_pool: Mutex<HashMap<String, Py<Database>>>,
}

#[pymethods]
impl ShardRouter {
    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /// Create a new `ShardRouter` backed by the given catalog.
    #[new]
    #[pyo3(signature = (catalog, max_connections=8))]
    fn new(catalog: Py<ShardCatalog>, max_connections: usize) -> Self {
        Self {
            catalog,
            max_connections,
            connection_pool: Mutex::new(HashMap::new()),
        }
    }

    // -----------------------------------------------------------------------
    // Connection pooling
    // -----------------------------------------------------------------------

    /// Return a `Database` for the given shard, with connection pooling.
    ///
    /// Checks if the shard exists in the catalog, then returns a cached or
    /// newly created `Database`.  If the shard is not found or the db_path
    /// does not exist, returns `None`.  Evicts the oldest connection if the
    /// pool exceeds `max_connections`.
    fn get_shard_db(&self, py: Python<'_>, repo_id: &str) -> PyResult<Option<Py<Database>>> {
        let mut pool = self.connection_pool.lock();

        // 1. Check the pool first.
        if let Some(db) = pool.get(repo_id) {
            return Ok(Some(db.clone_ref(py)));
        }

        // 2. Look up the shard in the catalog.
        let catalog_ref = self.catalog.bind(py);
        let catalog: &ShardCatalog = &catalog_ref.borrow();
        let shard_opt = catalog.get_shard(py, repo_id)?;

        let shard_obj = match shard_opt {
            Some(obj) => obj,
            None => return Ok(None),
        };

        // 3. Extract db_path from the shard dict.
        let shard_dict = shard_obj.bind(py);
        let shard: &Bound<'_, PyDict> = shard_dict.downcast::<PyDict>()?;
        let db_path_str: String = shard
            .get_item("db_path")?
            .ok_or_else(|| BombeError::Database("shard missing db_path".into()))?
            .extract()?;

        // 4. Verify the db_path exists on disk.
        if !Path::new(&db_path_str).exists() {
            return Ok(None);
        }

        // 5. Create Database, init schema, cache it.
        let db = Database::new(PathBuf::from(&db_path_str))?;
        db.init_schema()?;
        let db_py = Py::new(py, db)?;

        // 6. Evict oldest entry if pool is full.
        if pool.len() >= self.max_connections {
            // Remove the first key (insertion-order approximation via HashMap).
            if let Some(oldest_key) = pool.keys().next().cloned() {
                pool.remove(&oldest_key);
            }
        }

        pool.insert(repo_id.to_string(), db_py.clone_ref(py));
        Ok(Some(db_py))
    }

    // -----------------------------------------------------------------------
    // Query routing
    // -----------------------------------------------------------------------

    /// Determine which shard repo_ids may contain the named symbol.
    ///
    /// Uses the exported_symbols cache: search for symbol_name in
    /// exported_symbols.  Returns a list of unique repo_ids where the symbol
    /// was found.  Falls back to `all_shard_ids()` if no cache hits.
    /// Capped to `MAX_SHARDS_PER_QUERY`.
    fn route_symbol_query(&self, py: Python<'_>, symbol_name: &str) -> PyResult<Vec<String>> {
        let catalog_ref = self.catalog.bind(py);
        let catalog: &ShardCatalog = &catalog_ref.borrow();

        // Search exported symbols for the symbol_name.
        let hits_obj = match catalog.search_exported_symbols(py, symbol_name, "any", 100) {
            Ok(obj) => obj,
            Err(_) => return self.all_shard_ids(py),
        };

        let hits_list = hits_obj.bind(py);
        let hits: &Bound<'_, PyList> = match hits_list.downcast::<PyList>() {
            Ok(l) => l,
            Err(_) => return self.all_shard_ids(py),
        };

        let mut matched: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();

        for hit_obj in hits.iter() {
            if let Ok(hit) = hit_obj.downcast::<PyDict>() {
                if let Ok(Some(repo_id_obj)) = hit.get_item("repo_id") {
                    if let Ok(rid) = repo_id_obj.extract::<String>() {
                        if seen.insert(rid.clone()) {
                            matched.push(rid);
                        }
                    }
                }
            }
        }

        if matched.is_empty() {
            return self.all_shard_ids(py);
        }

        let cap = MAX_SHARDS_PER_QUERY as usize;
        matched.truncate(cap);
        Ok(matched)
    }

    /// Determine shards for a reference/caller/callee query.
    ///
    /// 1. Start with source_repo_id if provided.
    /// 2. Add shards found via `route_symbol_query`.
    /// 3. Add shards connected via cross_repo_edges (both from and to).
    /// 4. Deduplicate and cap to `MAX_SHARDS_PER_QUERY`.
    #[pyo3(signature = (symbol_name, source_repo_id=None))]
    fn route_reference_query(
        &self,
        py: Python<'_>,
        symbol_name: &str,
        source_repo_id: Option<&str>,
    ) -> PyResult<Vec<String>> {
        let mut result: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();

        // 1. Start with source_repo_id if provided.
        if let Some(src_id) = source_repo_id {
            let s = src_id.to_string();
            seen.insert(s.clone());
            result.push(s);
        }

        // 2. Add shards found via route_symbol_query.
        let symbol_ids = self.route_symbol_query(py, symbol_name)?;
        for rid in symbol_ids {
            if seen.insert(rid.clone()) {
                result.push(rid);
            }
        }

        // 3. Add shards connected via cross_repo_edges (from and to).
        let catalog_ref = self.catalog.bind(py);
        let catalog: &ShardCatalog = &catalog_ref.borrow();

        let current_ids: Vec<String> = seen.iter().cloned().collect();
        for rid in &current_ids {
            // Outgoing edges.
            if let Ok(outgoing_obj) = catalog.get_cross_repo_edges_from(py, rid, symbol_name) {
                if let Ok(outgoing_list) = outgoing_obj.bind(py).downcast::<PyList>() {
                    for edge_obj in outgoing_list.iter() {
                        if let Ok(edge) = edge_obj.downcast::<PyDict>() {
                            if let Ok(Some(to_repo_obj)) = edge.get_item("target_repo_id") {
                                if let Ok(to_repo) = to_repo_obj.extract::<String>() {
                                    if seen.insert(to_repo.clone()) {
                                        result.push(to_repo);
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Incoming edges.
            if let Ok(incoming_obj) = catalog.get_cross_repo_edges_to(py, rid, symbol_name) {
                if let Ok(incoming_list) = incoming_obj.bind(py).downcast::<PyList>() {
                    for edge_obj in incoming_list.iter() {
                        if let Ok(edge) = edge_obj.downcast::<PyDict>() {
                            if let Ok(Some(from_repo_obj)) = edge.get_item("source_repo_id") {
                                if let Ok(from_repo) = from_repo_obj.extract::<String>() {
                                    if seen.insert(from_repo.clone()) {
                                        result.push(from_repo);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // 4. Cap to MAX_SHARDS_PER_QUERY.
        let cap = MAX_SHARDS_PER_QUERY as usize;
        result.truncate(cap);
        Ok(result)
    }

    /// Return all enabled shard repo_ids, capped to `MAX_SHARDS_PER_QUERY`.
    fn all_shard_ids(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        let catalog_ref = self.catalog.bind(py);
        let catalog: &ShardCatalog = &catalog_ref.borrow();

        let shards_obj = catalog.list_shards(py, true)?;
        let shards_list = shards_obj.bind(py);
        let shards: &Bound<'_, PyList> = shards_list.downcast::<PyList>()?;

        let cap = MAX_SHARDS_PER_QUERY as usize;
        let mut ids: Vec<String> = Vec::new();

        for shard_obj in shards.iter() {
            if ids.len() >= cap {
                break;
            }
            if let Ok(shard) = shard_obj.downcast::<PyDict>() {
                if let Ok(Some(repo_id_obj)) = shard.get_item("repo_id") {
                    if let Ok(rid) = repo_id_obj.extract::<String>() {
                        ids.push(rid);
                    }
                }
            }
        }

        Ok(ids)
    }

    // -----------------------------------------------------------------------
    // Health / diagnostics
    // -----------------------------------------------------------------------

    /// Return health status for each enabled shard.
    ///
    /// For each shard: check if db_path exists, try to connect and query
    /// `SELECT COUNT(*) FROM symbols`, report status (ok/unavailable/error).
    /// Returns a list of Python dicts with repo_id, repo_path, db_path,
    /// status, symbol_count, and error (if any).
    fn shard_health(&self, py: Python<'_>) -> PyResult<PyObject> {
        let all_ids = self.all_shard_ids(py)?;
        let reports = PyList::empty(py);

        let catalog_ref = self.catalog.bind(py);
        let catalog: &ShardCatalog = &catalog_ref.borrow();

        for repo_id in &all_ids {
            let report = PyDict::new(py);

            let shard_opt = catalog.get_shard(py, repo_id)?;
            let shard_obj = match shard_opt {
                Some(obj) => obj,
                None => {
                    report.set_item("repo_id", repo_id)?;
                    report.set_item("repo_path", py.None())?;
                    report.set_item("db_path", py.None())?;
                    report.set_item("status", "unavailable")?;
                    report.set_item("symbol_count", 0)?;
                    report.set_item("error", "shard not found in catalog")?;
                    reports.append(report)?;
                    continue;
                }
            };

            let shard_dict = shard_obj.bind(py);
            let shard: &Bound<'_, PyDict> = shard_dict.downcast::<PyDict>()?;

            let repo_path: String = shard
                .get_item("repo_path")?
                .map(|v| v.extract().unwrap_or_default())
                .unwrap_or_default();
            let db_path: String = shard
                .get_item("db_path")?
                .map(|v| v.extract().unwrap_or_default())
                .unwrap_or_default();

            report.set_item("repo_id", repo_id)?;
            report.set_item("repo_path", &repo_path)?;
            report.set_item("db_path", &db_path)?;
            report.set_item("status", "unavailable")?;
            report.set_item("symbol_count", 0)?;
            report.set_item("error", py.None())?;

            if !Path::new(&db_path).exists() {
                report.set_item("error", format!("db_path does not exist: {db_path}"))?;
                reports.append(report)?;
                continue;
            }

            match self.get_shard_db(py, repo_id)? {
                None => {
                    report.set_item("error", "failed to obtain database connection")?;
                    reports.append(report)?;
                    continue;
                }
                Some(db_py) => {
                    let db_ref = db_py.bind(py);
                    let db: &Database = &db_ref.borrow();
                    let count_params: Vec<PyObject> = vec![];
                    match db.query(
                        py,
                        "SELECT COUNT(*) AS cnt FROM symbols;",
                        Some(count_params),
                    ) {
                        Ok(rows_obj) => {
                            let rows_list = rows_obj.bind(py);
                            if let Ok(rows) = rows_list.downcast::<PyList>() {
                                if let Some(first) = rows.iter().next() {
                                    if let Ok(row) = first.downcast::<PyDict>() {
                                        if let Ok(Some(cnt_obj)) = row.get_item("cnt") {
                                            let cnt: i64 = cnt_obj.extract().unwrap_or(0);
                                            report.set_item("symbol_count", cnt)?;
                                        }
                                    }
                                }
                            }
                            report.set_item("status", "ok")?;
                        }
                        Err(e) => {
                            report.set_item("status", "error")?;
                            report.set_item("error", e.to_string())?;
                        }
                    }
                }
            }

            reports.append(report)?;
        }

        Ok(reports.into_any().unbind())
    }

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    /// Release all pooled connections (clear the pool).
    fn close_all(&self) {
        let mut pool = self.connection_pool.lock();
        pool.clear();
    }
}
