//! Cross-repo import resolver for the Bombe shard catalog.
//!
//! Direct port of the Python `bombe.store.sharding.cross_repo_resolver`
//! module.  Resolves external dependencies against the shard catalog's
//! exported symbol cache to create cross-repo edges, and provides the
//! post-indexing sync function that refreshes exported symbols and discovers
//! inter-repo links.

use std::collections::HashSet;
use std::path::PathBuf;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use sha2::{Digest, Sha256};
use tracing::{debug, info, warn};

use crate::store::database::Database;
use crate::store::sharding::catalog::ShardCatalog;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Compute a deterministic repo_id from a canonical path.
///
/// Uses SHA-256 of the POSIX path string, taking the first 16 hex characters.
/// Matches the Python `compute_repo_id` and the Rust `_repo_id_from_path`
/// in `models.rs`.
#[pyfunction]
pub fn compute_repo_id(path: &str) -> String {
    // Expand ~ and resolve to absolute.
    let raw = PathBuf::from(path);
    let expanded = if path == "~" || path.starts_with("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            let mut h = PathBuf::from(home);
            if path.len() > 2 {
                h.push(&path[2..]);
            }
            h
        } else {
            raw
        }
    } else {
        raw
    };

    // Resolve to absolute (canonicalize if possible, else cwd-join).
    let resolved = match std::fs::canonicalize(&expanded) {
        Ok(p) => p,
        Err(_) => {
            if expanded.is_absolute() {
                expanded
            } else {
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join(&expanded)
            }
        }
    };

    // Convert to POSIX-style string for hashing.
    let posix = resolved.to_string_lossy().replace('\\', "/");
    let mut hasher = Sha256::new();
    hasher.update(posix.as_bytes());
    let digest = format!("{:x}", hasher.finalize());
    digest[..16].to_string()
}

// ---------------------------------------------------------------------------
// Cross-repo resolution
// ---------------------------------------------------------------------------

/// Resolve unresolved external_deps from a shard DB against the catalog's
/// exported symbol cache.
///
/// For each external dependency in *shard_db*:
/// 1. Look up the file's language from the `files` table.
/// 2. Query `catalog.resolve_external_import(module_name, language)` for
///    candidate matches in other shards.
/// 3. Skip matches whose `repo_id` matches the current *repo_id*
///    (self-edges are not cross-repo).
/// 4. Build a dict for every remaining match.
/// 5. Deduplicate by `(source_uri, target_uri, relationship)`.
///
/// Returns a Python list of edge dicts.
#[pyfunction]
pub fn resolve_cross_repo_imports(
    py: Python<'_>,
    catalog: &ShardCatalog,
    repo_id: &str,
    db: &Database,
) -> PyResult<PyObject> {
    let edges = PyList::empty(py);
    let mut seen: HashSet<(String, String, String)> = HashSet::new();

    // Fetch all external deps from the shard database.
    let ext_deps_obj = match db.query(
        py,
        "SELECT file_path, import_statement, module_name, line_number \
         FROM external_deps;",
        Some(vec![]),
    ) {
        Ok(obj) => obj,
        Err(e) => {
            warn!("Failed to query external_deps from shard database: {e}");
            return Ok(edges.into_any().unbind());
        }
    };

    let ext_deps_list = ext_deps_obj.bind(py);
    let ext_deps: &Bound<'_, PyList> = match ext_deps_list.downcast::<PyList>() {
        Ok(l) => l,
        Err(_) => return Ok(edges.into_any().unbind()),
    };

    let dep_count = ext_deps.len();

    for dep_obj in ext_deps.iter() {
        let dep: &Bound<'_, PyDict> = match dep_obj.downcast::<PyDict>() {
            Ok(d) => d,
            Err(_) => continue,
        };

        let file_path: String = match dep.get_item("file_path") {
            Ok(Some(v)) => match v.extract() {
                Ok(s) => s,
                Err(_) => continue,
            },
            _ => continue,
        };
        let module_name: String = match dep.get_item("module_name") {
            Ok(Some(v)) => match v.extract() {
                Ok(s) => s,
                Err(_) => continue,
            },
            _ => continue,
        };

        // Determine the language of the source file.
        let lang_obj = match db.query(
            py,
            "SELECT language FROM files WHERE path = ?1;",
            Some(vec![file_path
                .clone()
                .into_pyobject(py)?
                .into_any()
                .unbind()]),
        ) {
            Ok(obj) => obj,
            Err(_) => {
                warn!(
                    "Failed to query language for file {}; skipping dep {}",
                    file_path, module_name
                );
                continue;
            }
        };

        let lang_list = lang_obj.bind(py);
        let lang_rows: &Bound<'_, PyList> = match lang_list.downcast::<PyList>() {
            Ok(l) => l,
            Err(_) => continue,
        };

        if lang_rows.len() == 0 {
            debug!(
                "No files entry for path {}; skipping dep {}",
                file_path, module_name
            );
            continue;
        }

        let language: String = {
            let first = lang_rows.get_item(0)?;
            let row: &Bound<'_, PyDict> = match first.downcast::<PyDict>() {
                Ok(d) => d,
                Err(_) => continue,
            };
            match row.get_item("language") {
                Ok(Some(v)) => match v.extract() {
                    Ok(s) => s,
                    Err(_) => continue,
                },
                _ => continue,
            }
        };

        // Ask the catalog for matching exported symbols.
        let matches_obj = match catalog.resolve_external_import(py, &module_name, &language) {
            Ok(obj) => obj,
            Err(_) => {
                warn!(
                    "Catalog lookup failed for module_name={} language={}",
                    module_name, language
                );
                continue;
            }
        };

        let matches_list = matches_obj.bind(py);
        let matches: &Bound<'_, PyList> = match matches_list.downcast::<PyList>() {
            Ok(l) => l,
            Err(_) => continue,
        };

        for match_obj in matches.iter() {
            let m: &Bound<'_, PyDict> = match match_obj.downcast::<PyDict>() {
                Ok(d) => d,
                Err(_) => continue,
            };

            let match_repo_id: String = match m.get_item("repo_id") {
                Ok(Some(v)) => match v.extract() {
                    Ok(s) => s,
                    Err(_) => continue,
                },
                _ => continue,
            };

            // Skip self-references.
            if match_repo_id == repo_id {
                continue;
            }

            let match_qualified_name: String = match m.get_item("qualified_name") {
                Ok(Some(v)) => match v.extract() {
                    Ok(s) => s,
                    Err(_) => continue,
                },
                _ => continue,
            };
            let match_file_path: String = match m.get_item("file_path") {
                Ok(Some(v)) => match v.extract() {
                    Ok(s) => s,
                    Err(_) => continue,
                },
                _ => continue,
            };

            // Build URIs for deduplication.
            let source_uri = format!("bombe://{}/{}#{}", repo_id, module_name, file_path);
            let target_uri = format!(
                "bombe://{}/{}#{}",
                match_repo_id, match_qualified_name, match_file_path
            );
            let relationship = "IMPORTS".to_string();

            let dedup_key = (source_uri.clone(), target_uri.clone(), relationship.clone());
            if seen.contains(&dedup_key) {
                continue;
            }
            seen.insert(dedup_key);

            // Build edge dict.
            let edge = PyDict::new(py);
            edge.set_item("source_repo_id", repo_id)?;
            edge.set_item("source_qualified_name", &module_name)?;
            edge.set_item("source_file_path", &file_path)?;
            edge.set_item("target_repo_id", &match_repo_id)?;
            edge.set_item("target_qualified_name", &match_qualified_name)?;
            edge.set_item("target_file_path", &match_file_path)?;
            edge.set_item("relationship", &relationship)?;
            edge.set_item("confidence", 0.8)?;
            edge.set_item("provenance", "import_resolution")?;
            edges.append(edge)?;
        }
    }

    info!(
        "Resolved {} cross-repo edges for repo_id={} from {} external deps",
        edges.len(),
        repo_id,
        dep_count,
    );

    Ok(edges.into_any().unbind())
}

// ---------------------------------------------------------------------------
// Post-indexing sync
// ---------------------------------------------------------------------------

/// Post-indexing step: sync exported symbols and resolve cross-repo imports.
///
/// Workflow:
/// 1. Compute `repo_id` from *repo_path*.
/// 2. Store `repo_id` in the shard's `repo_meta` table.
/// 3. Register the shard in the catalog.
/// 4. Refresh the catalog's exported-symbol cache for this shard.
/// 5. Gather local symbol/edge counts and update catalog shard stats.
/// 6. Delete stale cross-repo edges for this repo in the catalog.
/// 7. Resolve cross-repo imports and upsert new edges.
///
/// Returns a Python dict summary suitable for telemetry or logging.
#[pyfunction]
pub fn post_index_cross_repo_sync(
    py: Python<'_>,
    catalog: &ShardCatalog,
    repo_path: &str,
    db: &Database,
) -> PyResult<PyObject> {
    let repo_id = compute_repo_id(repo_path);

    // -- 2. Store repo_id in shard meta ----------------------------------
    if let Err(e) = db.set_repo_meta("repo_id", &repo_id) {
        warn!("Failed to set repo_id in repo_meta for {}: {}", repo_id, e);
    }

    // -- 3. Register shard in catalog ------------------------------------
    // Determine the db_path from the Database (we use repo_path + default).
    // We need the shard's db_path; construct it from conventional location.
    let shard_db_path = format!("{}/.bombe/bombe.db", repo_path);
    if let Err(e) = catalog.register_shard(&repo_id, repo_path, &shard_db_path) {
        warn!("Failed to register shard for repo_id={}: {}", repo_id, e);
    }

    // -- 4. Refresh exported symbols -------------------------------------
    let exported_count: i64 = match catalog.refresh_exported_symbols(py, &repo_id, db) {
        Ok(count) => count,
        Err(e) => {
            warn!(
                "Failed to refresh exported symbols for repo_id={}: {}",
                repo_id, e
            );
            0
        }
    };

    // -- 5. Gather local counts and update shard stats -------------------
    let symbol_count: i64 = {
        match db.query(py, "SELECT COUNT(*) AS cnt FROM symbols;", Some(vec![])) {
            Ok(rows_obj) => {
                let rows_list = rows_obj.bind(py);
                if let Ok(rows) = rows_list.downcast::<PyList>() {
                    if let Some(first) = rows.iter().next() {
                        if let Ok(row) = first.downcast::<PyDict>() {
                            if let Ok(Some(cnt_obj)) = row.get_item("cnt") {
                                cnt_obj.extract::<i64>().unwrap_or(0)
                            } else {
                                0
                            }
                        } else {
                            0
                        }
                    } else {
                        0
                    }
                } else {
                    0
                }
            }
            Err(e) => {
                warn!("Failed to count symbols for repo_id={}: {}", repo_id, e);
                0
            }
        }
    };

    let edge_count: i64 = {
        match db.query(py, "SELECT COUNT(*) AS cnt FROM edges;", Some(vec![])) {
            Ok(rows_obj) => {
                let rows_list = rows_obj.bind(py);
                if let Ok(rows) = rows_list.downcast::<PyList>() {
                    if let Some(first) = rows.iter().next() {
                        if let Ok(row) = first.downcast::<PyDict>() {
                            if let Ok(Some(cnt_obj)) = row.get_item("cnt") {
                                cnt_obj.extract::<i64>().unwrap_or(0)
                            } else {
                                0
                            }
                        } else {
                            0
                        }
                    } else {
                        0
                    }
                } else {
                    0
                }
            }
            Err(e) => {
                warn!("Failed to count edges for repo_id={}: {}", repo_id, e);
                0
            }
        }
    };

    if let Err(e) = catalog.update_shard_stats(&repo_id, symbol_count, edge_count) {
        warn!(
            "Failed to update shard stats for repo_id={}: {}",
            repo_id, e
        );
    }

    // -- 6. Delete old cross-repo edges ----------------------------------
    if let Err(e) = catalog.delete_cross_repo_edges_for_repo(&repo_id) {
        warn!(
            "Failed to delete old cross-repo edges for repo_id={}: {}",
            repo_id, e
        );
    }

    // -- 7. Resolve cross-repo imports -----------------------------------
    let edges_obj = match resolve_cross_repo_imports(py, catalog, &repo_id, db) {
        Ok(obj) => obj,
        Err(e) => {
            warn!(
                "Failed to resolve cross-repo imports for repo_id={}: {}",
                repo_id, e
            );
            PyList::empty(py).into_any().unbind()
        }
    };

    let edges_count: i64 = {
        let el = edges_obj.bind(py);
        if let Ok(l) = el.downcast::<PyList>() {
            l.len() as i64
        } else {
            0
        }
    };

    // -- 8. Upsert new cross-repo edges ----------------------------------
    {
        let el = edges_obj.bind(py);
        if let Ok(edges_list) = el.downcast::<PyList>() {
            if let Err(e) = catalog.upsert_cross_repo_edges(py, edges_list) {
                warn!(
                    "Failed to upsert {} cross-repo edges for repo_id={}: {}",
                    edges_count, repo_id, e
                );
            }
        }
    }

    // -- Build summary dict ----------------------------------------------
    let summary = PyDict::new(py);
    summary.set_item("repo_id", &repo_id)?;
    summary.set_item("exported_symbols", exported_count)?;
    summary.set_item("cross_repo_edges_discovered", edges_count)?;
    summary.set_item("symbol_count", symbol_count)?;
    summary.set_item("edge_count", edge_count)?;

    Ok(summary.into_any().unbind())
}
