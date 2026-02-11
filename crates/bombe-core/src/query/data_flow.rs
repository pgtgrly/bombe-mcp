//! Data flow tracing backend for callgraph traversal.

use std::collections::{HashMap, HashSet, VecDeque};

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::{BombeError, BombeResult};
use crate::query::guards::{
    adaptive_graph_cap, clamp_depth, truncate_query, MAX_FLOW_DEPTH, MAX_GRAPH_EDGES,
    MAX_GRAPH_VISITED,
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
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
        ))
    });
    match result {
        Ok(r) => Ok(Some(r)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

pub fn trace_data_flow_impl(
    conn: &Connection,
    symbol_name: &str,
    direction: &str,
    max_depth: i64,
) -> BombeResult<serde_json::Value> {
    let normalized_symbol = truncate_query(symbol_name);
    let bounded_depth = clamp_depth(max_depth, MAX_FLOW_DEPTH);

    let total_symbols: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols;", [], |row| row.get(0))
        .unwrap_or(0);
    let dynamic_visited_cap = adaptive_graph_cap(total_symbols, MAX_GRAPH_VISITED, Some(128));
    let dynamic_edge_cap = 256i64.max(MAX_GRAPH_EDGES.min(dynamic_visited_cap * 2));

    let target = resolve_symbol(conn, &normalized_symbol)?
        .ok_or_else(|| BombeError::Query(format!("Symbol not found: {normalized_symbol}")))?;
    let (target_id, target_name, target_qname, target_file) = target;

    let mut queue: VecDeque<(i64, i64, String)> = VecDeque::new();
    queue.push_back((target_id, 0, "target".to_string()));
    let mut seen: HashSet<(i64, String)> = HashSet::new();
    seen.insert((target_id, "target".to_string()));
    let mut paths: Vec<serde_json::Value> = Vec::new();
    let mut nodes: HashMap<i64, serde_json::Value> = HashMap::new();
    nodes.insert(
        target_id,
        serde_json::json!({
            "id": target_id,
            "name": target_name,
            "qualified_name": target_qname,
            "file_path": target_file,
            "role": "target",
        }),
    );

    let mut upstream_stmt = conn.prepare(
        "SELECT e.source_id, e.line_number, s.name, s.qualified_name, s.file_path \
         FROM edges e JOIN symbols s ON s.id = e.source_id \
         WHERE e.relationship = 'CALLS' AND e.target_type = 'symbol' AND e.target_id = ?1;",
    )?;
    let mut downstream_stmt = conn.prepare(
        "SELECT e.target_id, e.line_number, s.name, s.qualified_name, s.file_path \
         FROM edges e JOIN symbols s ON s.id = e.target_id \
         WHERE e.relationship = 'CALLS' AND e.source_type = 'symbol' AND e.source_id = ?1;",
    )?;

    while let Some((current_id, depth, _role)) = queue.pop_front() {
        if paths.len() as i64 >= dynamic_edge_cap || nodes.len() as i64 >= dynamic_visited_cap {
            break;
        }
        if depth >= bounded_depth {
            continue;
        }
        let current_name = nodes
            .get(&current_id)
            .and_then(|n| n.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if direction == "upstream" || direction == "both" {
            let rows: Vec<(i64, Option<i64>, String, String, String)> = upstream_stmt
                .query_map(rusqlite::params![current_id], |row| {
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
            for (neighbor_id, line_number, name, qname, fpath) in rows {
                if paths.len() as i64 >= dynamic_edge_cap
                    || nodes.len() as i64 >= dynamic_visited_cap
                {
                    break;
                }
                nodes.entry(neighbor_id).or_insert_with(|| {
                    serde_json::json!({
                        "id": neighbor_id, "name": name, "qualified_name": qname,
                        "file_path": fpath, "role": "upstream",
                    })
                });
                paths.push(serde_json::json!({
                    "from_id": neighbor_id, "from_name": name,
                    "to_id": current_id, "to_name": current_name,
                    "line": line_number.unwrap_or(0), "depth": depth + 1,
                    "relationship": "CALLS",
                }));
                let key = (neighbor_id, "upstream".to_string());
                if !seen.contains(&key) {
                    seen.insert(key);
                    queue.push_back((neighbor_id, depth + 1, "upstream".to_string()));
                }
            }
        }

        if direction == "downstream" || direction == "both" {
            let rows: Vec<(i64, Option<i64>, String, String, String)> = downstream_stmt
                .query_map(rusqlite::params![current_id], |row| {
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
            for (neighbor_id, line_number, name, qname, fpath) in rows {
                if paths.len() as i64 >= dynamic_edge_cap
                    || nodes.len() as i64 >= dynamic_visited_cap
                {
                    break;
                }
                nodes.entry(neighbor_id).or_insert_with(|| {
                    serde_json::json!({
                        "id": neighbor_id, "name": name, "qualified_name": qname,
                        "file_path": fpath, "role": "downstream",
                    })
                });
                paths.push(serde_json::json!({
                    "from_id": current_id, "from_name": current_name,
                    "to_id": neighbor_id, "to_name": name,
                    "line": line_number.unwrap_or(0), "depth": depth + 1,
                    "relationship": "CALLS",
                }));
                let key = (neighbor_id, "downstream".to_string());
                if !seen.contains(&key) {
                    seen.insert(key);
                    queue.push_back((neighbor_id, depth + 1, "downstream".to_string()));
                }
            }
        }
    }

    paths.sort_by(|a, b| {
        let ad = a.get("depth").and_then(|v| v.as_i64()).unwrap_or(0);
        let bd = b.get("depth").and_then(|v| v.as_i64()).unwrap_or(0);
        ad.cmp(&bd).then_with(|| {
            let al = a.get("line").and_then(|v| v.as_i64()).unwrap_or(0);
            let bl = b.get("line").and_then(|v| v.as_i64()).unwrap_or(0);
            al.cmp(&bl)
        })
    });

    let mut node_list: Vec<serde_json::Value> = nodes.into_values().collect();
    node_list.sort_by(|a, b| {
        let af = a.get("file_path").and_then(|v| v.as_str()).unwrap_or("");
        let bf = b.get("file_path").and_then(|v| v.as_str()).unwrap_or("");
        af.cmp(bf).then_with(|| {
            let an = a.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let bn = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
            an.cmp(bn)
        })
    });

    let summary = format!(
        "Traced {} call edges across {} symbols (direction={direction}, depth<={bounded_depth}).",
        paths.len(),
        node_list.len()
    );

    Ok(serde_json::json!({
        "target": {
            "id": target_id, "name": target_name,
            "qualified_name": target_qname, "file_path": target_file,
        },
        "direction": direction,
        "max_depth": bounded_depth,
        "summary": summary,
        "nodes": node_list,
        "paths": paths,
    }))
}

#[pyfunction]
#[pyo3(signature = (db, symbol_name, direction="both", max_depth=3))]
pub fn trace_data_flow(
    py: Python<'_>,
    db: &crate::store::database::Database,
    symbol_name: &str,
    direction: &str,
    max_depth: i64,
) -> PyResult<PyObject> {
    let conn = db.connect_internal()?;
    let result = trace_data_flow_impl(&conn, symbol_name, direction, max_depth)?;
    let json_str = serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let json_module = py.import("json")?;
    json_module
        .call_method1("loads", (json_str,))
        .map(|o| o.into())
}
