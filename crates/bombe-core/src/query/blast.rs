//! Blast radius impact analysis backend.

use std::collections::{HashMap, HashSet, VecDeque};

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::BombeResult;

fn resolve_symbol(
    conn: &Connection,
    symbol_name: &str,
) -> BombeResult<Option<(i64, String, String)>> {
    let mut stmt = conn.prepare(
        "SELECT id, name, file_path FROM symbols \
         WHERE qualified_name = ?1 OR name = ?1 \
         ORDER BY pagerank_score DESC LIMIT 1;",
    )?;
    let result = stmt.query_row(rusqlite::params![symbol_name], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
        ))
    });
    match result {
        Ok(r) => Ok(Some(r)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

fn risk_level(direct: usize, transitive: usize) -> &'static str {
    let total = direct + transitive;
    if total >= 10 {
        "high"
    } else if total >= 3 {
        "medium"
    } else {
        "low"
    }
}

pub fn get_blast_radius_impl(
    conn: &Connection,
    symbol_name: &str,
    change_type: &str,
    max_depth: i64,
) -> BombeResult<HashMap<String, serde_json::Value>> {
    let target = resolve_symbol(conn, symbol_name)?.ok_or_else(|| {
        crate::errors::BombeError::Query(format!("Symbol not found: {symbol_name}"))
    })?;
    let (target_id, target_name, target_file) = target;

    let mut queue: VecDeque<(i64, i64)> = VecDeque::new();
    queue.push_back((target_id, 0));
    let mut visited: HashSet<i64> = HashSet::new();
    visited.insert(target_id);
    let mut direct_callers: Vec<serde_json::Value> = Vec::new();
    let mut transitive_callers: Vec<serde_json::Value> = Vec::new();

    let mut stmt = conn.prepare(
        "SELECT e.source_id, e.line_number, s.name, s.file_path \
         FROM edges e JOIN symbols s ON s.id = e.source_id \
         WHERE e.relationship = 'CALLS' AND e.target_type = 'symbol' AND e.target_id = ?1;",
    )?;

    while let Some((current, depth)) = queue.pop_front() {
        if depth >= max_depth {
            continue;
        }
        let rows: Vec<(i64, Option<i64>, String, String)> = stmt
            .query_map(rusqlite::params![current], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?
            .filter_map(|r| r.ok())
            .collect();

        for (source_id, line_number, name, file_path) in rows {
            if visited.contains(&source_id) {
                continue;
            }
            visited.insert(source_id);
            let next_depth = depth + 1;
            let item = serde_json::json!({
                "name": name,
                "file": file_path,
                "line": line_number.unwrap_or(0),
            });
            if next_depth == 1 {
                direct_callers.push(item);
            } else {
                let mut item = item;
                item.as_object_mut()
                    .unwrap()
                    .insert("depth".to_string(), serde_json::json!(next_depth));
                transitive_callers.push(item);
            }
            queue.push_back((source_id, next_depth));
        }
    }

    let mut affected_files: HashSet<String> = HashSet::new();
    affected_files.insert(target_file.clone());
    for caller in direct_callers.iter().chain(transitive_callers.iter()) {
        if let Some(f) = caller.get("file").and_then(|v| v.as_str()) {
            affected_files.insert(f.to_string());
        }
    }
    let mut affected_files: Vec<String> = affected_files.into_iter().collect();
    affected_files.sort();

    let risk = risk_level(direct_callers.len(), transitive_callers.len());
    let summary = format!(
        "{risk} - {} direct callers, {} transitive dependents",
        direct_callers.len(),
        transitive_callers.len()
    );

    let mut result = HashMap::new();
    result.insert(
        "target".to_string(),
        serde_json::json!({
            "name": target_name,
            "file_path": target_file,
        }),
    );
    result.insert("change_type".to_string(), serde_json::json!(change_type));
    result.insert(
        "impact".to_string(),
        serde_json::json!({
            "direct_callers": direct_callers,
            "transitive_callers": transitive_callers,
            "affected_files": affected_files,
            "total_affected_symbols": direct_callers.len() + transitive_callers.len(),
            "total_affected_files": affected_files.len(),
            "risk_assessment": summary,
        }),
    );

    Ok(result)
}

#[pyfunction]
pub fn get_blast_radius(
    py: Python<'_>,
    db: &crate::store::database::Database,
    symbol_name: &str,
    change_type: &str,
    max_depth: i64,
) -> PyResult<PyObject> {
    let conn = db.connect_internal()?;
    let result = get_blast_radius_impl(&conn, symbol_name, change_type, max_depth)?;
    let json_str = serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let json_module = py.import("json")?;
    json_module
        .call_method1("loads", (json_str,))
        .map(|o| o.into())
}
