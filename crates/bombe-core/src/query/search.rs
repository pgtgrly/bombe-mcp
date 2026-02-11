//! Symbol search query backend.

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::BombeResult;
use crate::query::guards::{clamp_limit, truncate_query, MAX_SEARCH_LIMIT};
use crate::query::hybrid::rank_symbol;

fn count_refs(conn: &Connection, symbol_id: i64) -> BombeResult<(i64, i64)> {
    let callers: i64 = conn.query_row(
        "SELECT COUNT(*) FROM edges WHERE relationship = 'CALLS' AND target_type = 'symbol' AND target_id = ?1;",
        rusqlite::params![symbol_id],
        |row| row.get(0),
    )?;
    let callees: i64 = conn.query_row(
        "SELECT COUNT(*) FROM edges WHERE relationship = 'CALLS' AND source_type = 'symbol' AND source_id = ?1;",
        rusqlite::params![symbol_id],
        |row| row.get(0),
    )?;
    Ok((callers, callees))
}

struct SymbolRow {
    id: i64,
    name: String,
    qualified_name: String,
    kind: String,
    file_path: String,
    start_line: i64,
    end_line: i64,
    signature: Option<String>,
    docstring: Option<String>,
    visibility: Option<String>,
    pagerank_score: f64,
}

fn search_with_like(
    conn: &Connection,
    query: &str,
    kind: &str,
    file_pattern: Option<&str>,
    limit: i64,
) -> BombeResult<Vec<SymbolRow>> {
    let query_value = format!("%{}%", query.to_lowercase());
    let mut sql = String::from(
        "SELECT id, name, qualified_name, kind, file_path, start_line, end_line, \
         signature, docstring, visibility, pagerank_score FROM symbols WHERE \
         (LOWER(name) LIKE ?1 OR LOWER(qualified_name) LIKE ?2)",
    );
    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> =
        vec![Box::new(query_value.clone()), Box::new(query_value)];
    let mut param_idx = 3;

    if kind != "any" {
        sql.push_str(&format!(" AND kind = ?{param_idx}"));
        params.push(Box::new(kind.to_string()));
        param_idx += 1;
    }
    if let Some(fp) = file_pattern {
        sql.push_str(&format!(" AND file_path LIKE ?{param_idx}"));
        params.push(Box::new(fp.replace('*', "%")));
        param_idx += 1;
    }
    sql.push_str(&format!(
        " ORDER BY pagerank_score DESC, name ASC LIMIT ?{param_idx}"
    ));
    params.push(Box::new(limit));

    let mut stmt = conn.prepare(&sql)?;
    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows = stmt
        .query_map(param_refs.as_slice(), |row| {
            Ok(SymbolRow {
                id: row.get(0)?,
                name: row.get(1)?,
                qualified_name: row.get(2)?,
                kind: row.get(3)?,
                file_path: row.get(4)?,
                start_line: row.get(5)?,
                end_line: row.get(6)?,
                signature: row.get(7)?,
                docstring: row.get(8)?,
                visibility: row.get(9)?,
                pagerank_score: row.get::<_, f64>(10).unwrap_or(0.0),
            })
        })?
        .filter_map(|r| r.ok())
        .collect();
    Ok(rows)
}

fn search_with_fts(
    conn: &Connection,
    query: &str,
    kind: &str,
    file_pattern: Option<&str>,
    limit: i64,
) -> BombeResult<Vec<SymbolRow>> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(vec![]);
    }

    let mut sql = String::from(
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.file_path, s.start_line, s.end_line, \
         s.signature, s.docstring, s.visibility, s.pagerank_score \
         FROM symbol_fts f JOIN symbols s ON s.id = f.symbol_id WHERE symbol_fts MATCH ?1",
    );
    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![Box::new(query.to_string())];
    let mut param_idx = 2;

    if kind != "any" {
        sql.push_str(&format!(" AND s.kind = ?{param_idx}"));
        params.push(Box::new(kind.to_string()));
        param_idx += 1;
    }
    if let Some(fp) = file_pattern {
        sql.push_str(&format!(" AND s.file_path LIKE ?{param_idx}"));
        params.push(Box::new(fp.replace('*', "%")));
        param_idx += 1;
    }
    sql.push_str(&format!(
        " ORDER BY rank ASC, s.pagerank_score DESC LIMIT ?{param_idx}"
    ));
    params.push(Box::new(limit));

    let mut stmt = conn.prepare(&sql)?;
    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows = stmt
        .query_map(param_refs.as_slice(), |row| {
            Ok(SymbolRow {
                id: row.get(0)?,
                name: row.get(1)?,
                qualified_name: row.get(2)?,
                kind: row.get(3)?,
                file_path: row.get(4)?,
                start_line: row.get(5)?,
                end_line: row.get(6)?,
                signature: row.get(7)?,
                docstring: row.get(8)?,
                visibility: row.get(9)?,
                pagerank_score: row.get::<_, f64>(10).unwrap_or(0.0),
            })
        })?
        .filter_map(|r| r.ok())
        .collect();
    Ok(rows)
}

pub fn search_symbols_impl(
    conn: &Connection,
    query: &str,
    kind: &str,
    file_pattern: Option<&str>,
    limit: i64,
) -> BombeResult<serde_json::Value> {
    let normalized_query = truncate_query(query);
    let bounded_limit = clamp_limit(limit, MAX_SEARCH_LIMIT);
    let expanded_limit = clamp_limit(bounded_limit * 3, MAX_SEARCH_LIMIT);

    // Try FTS first
    let fts_rows = search_with_fts(conn, &normalized_query, kind, file_pattern, expanded_limit)
        .unwrap_or_default();
    let like_rows = search_with_like(conn, &normalized_query, kind, file_pattern, expanded_limit)?;

    // Combine, FTS takes priority
    let mut combined: indexmap::IndexMap<i64, (SymbolRow, String)> = indexmap::IndexMap::new();
    for row in like_rows {
        let id = row.id;
        combined.entry(id).or_insert((row, "like".to_string()));
    }
    for row in fts_rows {
        let id = row.id;
        combined.insert(id, (row, "fts".to_string()));
    }
    let search_mode = if combined.values().any(|(_, s)| s == "fts") {
        "fts"
    } else {
        "like"
    };

    let mut scored: Vec<(f64, serde_json::Value)> = Vec::new();
    for (_, (row, strategy)) in &combined {
        let (callers_count, callees_count) = count_refs(conn, row.id)?;
        let ranking_score = rank_symbol(
            &normalized_query,
            &row.name,
            &row.qualified_name,
            row.signature.as_deref(),
            row.docstring.as_deref(),
            row.pagerank_score,
            callers_count,
            callees_count,
        );
        let file_pat = file_pattern.unwrap_or("*");
        let match_reason = format!(
            "{search_mode}:query='{}',kind='{}',file='{}'",
            normalized_query, kind, file_pat
        );
        scored.push((
            ranking_score,
            serde_json::json!({
                "name": row.name,
                "qualified_name": row.qualified_name,
                "kind": row.kind,
                "file_path": row.file_path,
                "start_line": row.start_line,
                "end_line": row.end_line,
                "signature": row.signature,
                "visibility": row.visibility,
                "importance_score": row.pagerank_score,
                "callers_count": callers_count,
                "callees_count": callees_count,
                "match_strategy": strategy,
                "match_reason": match_reason,
            }),
        ));
    }

    scored.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                let aq =
                    a.1.get("qualified_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                let bq =
                    b.1.get("qualified_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                aq.cmp(bq)
            })
            .then_with(|| {
                let af = a.1.get("file_path").and_then(|v| v.as_str()).unwrap_or("");
                let bf = b.1.get("file_path").and_then(|v| v.as_str()).unwrap_or("");
                af.cmp(bf)
            })
    });

    let payload: Vec<serde_json::Value> = scored
        .into_iter()
        .take(bounded_limit as usize)
        .map(|(_, v)| v)
        .collect();
    let total = payload.len() as i64;

    Ok(serde_json::json!({
        "symbols": payload,
        "total_matches": total,
    }))
}

#[pyfunction]
#[pyo3(signature = (db, query, kind="any", file_pattern=None, limit=20))]
pub fn search_symbols(
    py: Python<'_>,
    db: &crate::store::database::Database,
    query: &str,
    kind: &str,
    file_pattern: Option<&str>,
    limit: i64,
) -> PyResult<PyObject> {
    let conn = db.connect_internal()?;
    let result = search_symbols_impl(&conn, query, kind, file_pattern, limit)?;
    let json_str = serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let json_module = py.import("json")?;
    json_module
        .call_method1("loads", (json_str,))
        .map(|o| o.into())
}
