//! Change impact analysis backend with graph-aware dependents.

use std::collections::{HashSet, VecDeque};

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::{BombeError, BombeResult};
use crate::query::guards::{
    adaptive_graph_cap, clamp_depth, truncate_query, MAX_GRAPH_EDGES, MAX_GRAPH_VISITED,
    MAX_IMPACT_DEPTH,
};

fn resolve_symbol(
    conn: &Connection,
    symbol_name: &str,
) -> BombeResult<Option<(i64, String, String, String)>> {
    let mut stmt = conn.prepare(
        "SELECT id, name, qualified_name, file_path FROM symbols \
         WHERE qualified_name = ?1 OR name = ?1 \
         ORDER BY pagerank_score DESC LIMIT 1;",
    )?;
    let result = stmt.query_row(rusqlite::params![symbol_name], |row| {
        Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
    });
    match result {
        Ok(r) => Ok(Some(r)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

fn risk_level(direct: usize, transitive: usize, type_deps: usize) -> &'static str {
    let total = direct + transitive + type_deps;
    if total >= 12 {
        "high"
    } else if total >= 4 {
        "medium"
    } else {
        "low"
    }
}

pub fn change_impact_impl(
    conn: &Connection,
    symbol_name: &str,
    change_type: &str,
    max_depth: i64,
) -> BombeResult<serde_json::Value> {
    let normalized_symbol = truncate_query(symbol_name);
    let bounded_depth = clamp_depth(max_depth, MAX_IMPACT_DEPTH);

    let total_symbols: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols;", [], |row| row.get(0))
        .unwrap_or(0);
    let dynamic_visited_cap = adaptive_graph_cap(total_symbols, MAX_GRAPH_VISITED, Some(128));
    let dynamic_edge_cap = 256i64.max(MAX_GRAPH_EDGES.min(dynamic_visited_cap * 2));

    let target = resolve_symbol(conn, &normalized_symbol)?
        .ok_or_else(|| BombeError::Query(format!("Symbol not found: {normalized_symbol}")))?;
    let (target_id, target_name, target_qname, target_file) = target;

    let mut queue: VecDeque<(i64, i64)> = VecDeque::new();
    queue.push_back((target_id, 0));
    let mut visited: HashSet<i64> = HashSet::new();
    visited.insert(target_id);
    let mut direct_callers: Vec<serde_json::Value> = Vec::new();
    let mut transitive_callers: Vec<serde_json::Value> = Vec::new();

    let mut caller_stmt = conn.prepare(
        "SELECT e.source_id, e.line_number, s.name, s.qualified_name, s.file_path \
         FROM edges e JOIN symbols s ON s.id = e.source_id \
         WHERE e.relationship = 'CALLS' AND e.target_type = 'symbol' AND e.target_id = ?1;",
    )?;

    while let Some((current, depth)) = queue.pop_front() {
        if (direct_callers.len() + transitive_callers.len()) as i64 >= dynamic_edge_cap {
            break;
        }
        if depth >= bounded_depth {
            continue;
        }
        let rows: Vec<(i64, Option<i64>, String, String, String)> = caller_stmt
            .query_map(rusqlite::params![current], |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                ))
            })?
            .filter_map(|r| r.ok())
            .collect();

        for (source_id, line_number, name, qname, fpath) in rows {
            if (direct_callers.len() + transitive_callers.len()) as i64 >= dynamic_edge_cap {
                break;
            }
            if visited.contains(&source_id) {
                continue;
            }
            if visited.len() as i64 >= dynamic_visited_cap {
                break;
            }
            visited.insert(source_id);
            let next_depth = depth + 1;
            let item = serde_json::json!({
                "id": source_id,
                "name": name,
                "qualified_name": qname,
                "file_path": fpath,
                "line": line_number.unwrap_or(0),
                "depth": next_depth,
                "impact_reason": format!("call_dependency:depth={next_depth}"),
            });
            if next_depth == 1 {
                direct_callers.push(item);
            } else {
                transitive_callers.push(item);
            }
            queue.push_back((source_id, next_depth));
        }
    }

    // Type dependents (EXTENDS/IMPLEMENTS)
    let mut type_stmt = conn.prepare(
        "SELECT e.source_id, e.relationship, s.name, s.qualified_name, s.file_path \
         FROM edges e JOIN symbols s ON s.id = e.source_id \
         WHERE e.target_type = 'symbol' AND e.target_id = ?1 \
         AND e.relationship IN ('EXTENDS', 'IMPLEMENTS');",
    )?;
    let type_dependents: Vec<serde_json::Value> = type_stmt
        .query_map(rusqlite::params![target_id], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })?
        .filter_map(|r| r.ok())
        .map(|(id, rel, name, qname, fpath)| {
            serde_json::json!({
                "id": id, "name": name, "qualified_name": qname,
                "file_path": fpath,
                "impact_reason": format!("type_dependency:{rel}"),
            })
        })
        .collect();

    let mut impacted_files: HashSet<String> = HashSet::new();
    impacted_files.insert(target_file.clone());
    for item in direct_callers
        .iter()
        .chain(transitive_callers.iter())
        .chain(type_dependents.iter())
    {
        if let Some(f) = item.get("file_path").and_then(|v| v.as_str()) {
            impacted_files.insert(f.to_string());
        }
    }
    let mut impacted_files: Vec<String> = impacted_files.into_iter().collect();
    impacted_files.sort();

    let risk = risk_level(
        direct_callers.len(),
        transitive_callers.len(),
        type_dependents.len(),
    );
    let summary = format!(
        "Impact={risk}; direct={}, transitive={}, type_dependents={}, files={}",
        direct_callers.len(),
        transitive_callers.len(),
        type_dependents.len(),
        impacted_files.len()
    );

    Ok(serde_json::json!({
        "target": {
            "id": target_id, "name": target_name,
            "qualified_name": target_qname, "file_path": target_file,
        },
        "change_type": change_type,
        "max_depth": bounded_depth,
        "summary": summary,
        "impact": {
            "direct_callers": direct_callers,
            "transitive_callers": transitive_callers,
            "type_dependents": type_dependents,
            "affected_files": impacted_files,
            "total_affected_symbols": direct_callers.len() + transitive_callers.len() + type_dependents.len(),
            "risk_level": risk,
        },
    }))
}

#[pyfunction]
#[pyo3(signature = (db, symbol_name, change_type="behavior", max_depth=3))]
pub fn change_impact(
    py: Python<'_>,
    db: &crate::store::database::Database,
    symbol_name: &str,
    change_type: &str,
    max_depth: i64,
) -> PyResult<PyObject> {
    let conn = db.connect_internal()?;
    let result = change_impact_impl(&conn, symbol_name, change_type, max_depth)?;
    let json_str = serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let json_module = py.import("json")?;
    json_module
        .call_method1("loads", (json_str,))
        .map(|o| o.into())
}
