//! Reference traversal backend for callers, callees, implementors, and supers.

use std::collections::{HashSet, VecDeque};

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::{BombeError, BombeResult};
use crate::query::guards::{
    adaptive_graph_cap, clamp_depth, truncate_query, MAX_GRAPH_EDGES, MAX_GRAPH_VISITED,
    MAX_REFERENCE_DEPTH,
};

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Resolve a symbol name to its row id.
///
/// Tries an exact match on `qualified_name` first; falls back to a match on
/// `name` ordered by `pagerank_score` descending.
fn resolve_symbol_id(conn: &Connection, symbol_name: &str) -> BombeResult<Option<i64>> {
    // Exact match on qualified_name
    let exact: Result<i64, _> = conn.query_row(
        "SELECT id FROM symbols WHERE qualified_name = ?1 ORDER BY pagerank_score DESC LIMIT 1;",
        rusqlite::params![symbol_name],
        |row| row.get(0),
    );
    match exact {
        Ok(id) => return Ok(Some(id)),
        Err(rusqlite::Error::QueryReturnedNoRows) => {}
        Err(e) => return Err(e.into()),
    }

    // Fallback: match on name
    let fallback: Result<i64, _> = conn.query_row(
        "SELECT id FROM symbols WHERE name = ?1 ORDER BY pagerank_score DESC LIMIT 1;",
        rusqlite::params![symbol_name],
        |row| row.get(0),
    );
    match fallback {
        Ok(id) => Ok(Some(id)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

/// Load core symbol fields by id.
fn load_symbol(conn: &Connection, symbol_id: i64) -> BombeResult<Option<serde_json::Value>> {
    let result = conn.query_row(
        "SELECT id, name, file_path, signature, start_line, end_line, qualified_name \
         FROM symbols WHERE id = ?1;",
        rusqlite::params![symbol_id],
        |row| {
            Ok(serde_json::json!({
                "id": row.get::<_, i64>(0)?,
                "name": row.get::<_, String>(1)?,
                "file_path": row.get::<_, String>(2)?,
                "signature": row.get::<_, Option<String>>(3)?,
                "start_line": row.get::<_, i64>(4)?,
                "end_line": row.get::<_, i64>(5)?,
                "qualified_name": row.get::<_, String>(6)?,
            }))
        },
    );
    match result {
        Ok(v) => Ok(Some(v)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

/// Read source lines from a file on disk.
///
/// Returns the slice `[start_line..=end_line]` (1-based inclusive) joined with
/// newlines. Returns an empty string on any IO error.
fn read_source(file_path: &str, start_line: i64, end_line: i64) -> String {
    let content = match std::fs::read_to_string(file_path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };
    let lines: Vec<&str> = content.lines().collect();
    let start = (start_line.max(1) - 1) as usize;
    let end = (end_line.max(1) as usize).min(lines.len());
    if start >= lines.len() || start >= end {
        return String::new();
    }
    lines[start..end].join("\n")
}

// ---------------------------------------------------------------------------
// BFS walk
// ---------------------------------------------------------------------------

/// A single traversal step produced by `_walk`.
struct WalkEntry {
    next_id: i64,
    line_number: Option<i64>,
    depth: i64,
    relationship: String,
}

/// BFS walk over the edge table in a given direction.
///
/// `direction` must be one of `"callers"`, `"callees"`, `"implementors"`,
/// or `"supers"`.
fn walk(
    conn: &Connection,
    start_id: i64,
    direction: &str,
    max_depth: i64,
    max_edges: i64,
    max_visited: i64,
) -> BombeResult<Vec<WalkEntry>> {
    let sql = match direction {
        "callers" => {
            "SELECT source_id, line_number FROM edges \
             WHERE relationship = 'CALLS' AND target_type = 'symbol' AND target_id = ?1;"
        }
        "callees" => {
            "SELECT target_id, line_number FROM edges \
             WHERE relationship = 'CALLS' AND source_type = 'symbol' AND source_id = ?1;"
        }
        "implementors" => {
            "SELECT source_id, line_number FROM edges \
             WHERE relationship = 'IMPLEMENTS' AND target_type = 'symbol' AND target_id = ?1;"
        }
        "supers" => {
            "SELECT target_id, line_number FROM edges \
             WHERE relationship IN ('EXTENDS', 'IMPLEMENTS') AND source_type = 'symbol' AND source_id = ?1;"
        }
        _ => {
            return Err(BombeError::Query(format!(
                "Invalid walk direction: {direction}"
            )));
        }
    };

    let relationship_label = match direction {
        "callers" => "CALLS",
        "callees" => "CALLS",
        "implementors" => "IMPLEMENTS",
        "supers" => "EXTENDS_OR_IMPLEMENTS",
        _ => direction,
    };

    let mut stmt = conn.prepare(sql)?;
    let mut results: Vec<WalkEntry> = Vec::new();
    let mut visited: HashSet<i64> = HashSet::new();
    visited.insert(start_id);

    let mut queue: VecDeque<(i64, i64)> = VecDeque::new();
    queue.push_back((start_id, 0));

    while let Some((current_id, depth)) = queue.pop_front() {
        if results.len() as i64 >= max_edges || visited.len() as i64 >= max_visited {
            break;
        }
        if depth >= max_depth {
            continue;
        }

        let rows: Vec<(i64, Option<i64>)> = stmt
            .query_map(rusqlite::params![current_id], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, Option<i64>>(1)?))
            })?
            .filter_map(|r| r.ok())
            .collect();

        for (next_id, line_number) in rows {
            if results.len() as i64 >= max_edges || visited.len() as i64 >= max_visited {
                break;
            }
            if visited.contains(&next_id) {
                continue;
            }
            visited.insert(next_id);
            let next_depth = depth + 1;

            results.push(WalkEntry {
                next_id,
                line_number,
                depth: next_depth,
                relationship: relationship_label.to_string(),
            });

            queue.push_back((next_id, next_depth));
        }
    }

    Ok(results)
}

// ---------------------------------------------------------------------------
// Main implementation (pure Rust, no Python dependency)
// ---------------------------------------------------------------------------

pub fn get_references_impl(
    conn: &Connection,
    symbol_name: &str,
    direction: &str,
    depth: i64,
    include_source: bool,
) -> BombeResult<serde_json::Value> {
    let normalized_symbol = truncate_query(symbol_name);
    let bounded_depth = clamp_depth(depth, MAX_REFERENCE_DEPTH);

    // Dynamic caps based on index size
    let total_symbols: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols;", [], |row| row.get(0))
        .unwrap_or(0);
    let dynamic_visited_cap = adaptive_graph_cap(total_symbols, MAX_GRAPH_VISITED, Some(200));
    let dynamic_edge_cap = 256i64.max(MAX_GRAPH_EDGES.min(dynamic_visited_cap * 2));

    // Resolve the target symbol
    let symbol_id = resolve_symbol_id(conn, &normalized_symbol)?
        .ok_or_else(|| BombeError::Query(format!("Symbol not found: {normalized_symbol}")))?;

    let target_symbol = load_symbol(conn, symbol_id)?
        .ok_or_else(|| BombeError::Query(format!("Symbol row missing for id: {symbol_id}")))?;

    // Determine which directions to walk
    let directions: Vec<&str> = match direction {
        "callers" => vec!["callers"],
        "callees" => vec!["callees"],
        "both" => vec!["callers", "callees"],
        "implementors" => vec!["implementors"],
        "supers" => vec!["supers"],
        _ => vec!["callers", "callees"],
    };

    let mut callers_list: Vec<serde_json::Value> = Vec::new();
    let mut callees_list: Vec<serde_json::Value> = Vec::new();
    let mut implementors_list: Vec<serde_json::Value> = Vec::new();
    let mut supers_list: Vec<serde_json::Value> = Vec::new();

    for dir in &directions {
        let entries = walk(
            conn,
            symbol_id,
            dir,
            bounded_depth,
            dynamic_edge_cap,
            dynamic_visited_cap,
        )?;

        for entry in entries {
            let sym = load_symbol(conn, entry.next_id)?;
            let sym_value = match sym {
                Some(v) => v,
                None => continue,
            };

            let mut item = serde_json::json!({
                "id": entry.next_id,
                "name": sym_value.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                "qualified_name": sym_value.get("qualified_name").and_then(|v| v.as_str()).unwrap_or(""),
                "file_path": sym_value.get("file_path").and_then(|v| v.as_str()).unwrap_or(""),
                "signature": sym_value.get("signature"),
                "start_line": sym_value.get("start_line").and_then(|v| v.as_i64()).unwrap_or(0),
                "end_line": sym_value.get("end_line").and_then(|v| v.as_i64()).unwrap_or(0),
                "line_number": entry.line_number.unwrap_or(0),
                "depth": entry.depth,
                "relationship": entry.relationship,
            });

            if include_source {
                let file_path = sym_value
                    .get("file_path")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let start = sym_value
                    .get("start_line")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let end = sym_value
                    .get("end_line")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let source = read_source(file_path, start, end);
                item.as_object_mut()
                    .unwrap()
                    .insert("source".to_string(), serde_json::json!(source));
            }

            match *dir {
                "callers" => callers_list.push(item),
                "callees" => callees_list.push(item),
                "implementors" => implementors_list.push(item),
                "supers" => supers_list.push(item),
                _ => {}
            }
        }
    }

    // Build target_symbol entry, optionally with source
    let mut target_out = target_symbol.clone();
    if include_source {
        let file_path = target_symbol
            .get("file_path")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let start = target_symbol
            .get("start_line")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let end = target_symbol
            .get("end_line")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let source = read_source(file_path, start, end);
        target_out
            .as_object_mut()
            .unwrap()
            .insert("source".to_string(), serde_json::json!(source));
    }

    Ok(serde_json::json!({
        "target_symbol": target_out,
        "callers": callers_list,
        "callees": callees_list,
        "implementors": implementors_list,
        "supers": supers_list,
    }))
}

// ---------------------------------------------------------------------------
// Python-exposed entry point
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(signature = (db, symbol_name, direction="both", depth=1, include_source=false))]
pub fn get_references(
    py: Python<'_>,
    db: &crate::store::database::Database,
    symbol_name: &str,
    direction: &str,
    depth: i64,
    include_source: bool,
) -> PyResult<PyObject> {
    let conn = db.connect_internal()?;
    let result = get_references_impl(&conn, symbol_name, direction, depth, include_source)?;
    let json_str = serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let json_module = py.import("json")?;
    json_module
        .call_method1("loads", (json_str,))
        .map(|o| o.into())
}
