//! PageRank computation over symbol graph edges.

use std::collections::HashMap;

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::BombeResult;

const PAGERANK_RELATIONSHIPS: &[&str] = &["CALLS", "IMPORTS_SYMBOL", "EXTENDS", "IMPLEMENTS"];

pub fn recompute_pagerank_impl(conn: &Connection, damping: f64, epsilon: f64) -> BombeResult<()> {
    let mut stmt = conn.prepare("SELECT id FROM symbols ORDER BY id;")?;
    let symbol_ids: Vec<i64> = stmt
        .query_map([], |row| row.get(0))?
        .filter_map(|r| r.ok())
        .collect();

    if symbol_ids.is_empty() {
        return Ok(());
    }

    let id_set: std::collections::HashSet<i64> = symbol_ids.iter().copied().collect();
    let mut adjacency: HashMap<i64, Vec<i64>> =
        symbol_ids.iter().map(|&id| (id, Vec::new())).collect();

    let placeholders: String = PAGERANK_RELATIONSHIPS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "SELECT source_id, target_id FROM edges \
         WHERE source_type = 'symbol' AND target_type = 'symbol' \
         AND relationship IN ({placeholders});"
    );
    let mut edge_stmt = conn.prepare(&sql)?;
    let params: Vec<&dyn rusqlite::types::ToSql> = PAGERANK_RELATIONSHIPS
        .iter()
        .map(|r| r as &dyn rusqlite::types::ToSql)
        .collect();
    let edges: Vec<(i64, i64)> = edge_stmt
        .query_map(params.as_slice(), |row| Ok((row.get(0)?, row.get(1)?)))?
        .filter_map(|r| r.ok())
        .collect();

    for (source, target) in edges {
        if id_set.contains(&source) && id_set.contains(&target) {
            adjacency.entry(source).or_default().push(target);
        }
    }

    let node_count = symbol_ids.len() as f64;
    let base_score = 1.0 / node_count;
    let mut scores: HashMap<i64, f64> = symbol_ids.iter().map(|&id| (id, base_score)).collect();

    let mut delta = 1.0;
    while delta > epsilon {
        let mut next_scores: HashMap<i64, f64> = symbol_ids
            .iter()
            .map(|&id| (id, (1.0 - damping) / node_count))
            .collect();

        let dangling_mass: f64 = adjacency
            .iter()
            .filter(|(_, targets)| targets.is_empty())
            .map(|(id, _)| scores[id])
            .sum();
        let dangling_contrib = damping * dangling_mass / node_count;

        for &id in &symbol_ids {
            *next_scores.get_mut(&id).unwrap() += dangling_contrib;
        }

        for (&source, targets) in &adjacency {
            if targets.is_empty() {
                continue;
            }
            let share = damping * scores[&source] / targets.len() as f64;
            for &target in targets {
                *next_scores.get_mut(&target).unwrap() += share;
            }
        }

        delta = symbol_ids
            .iter()
            .map(|id| (next_scores[id] - scores[id]).abs())
            .sum();
        scores = next_scores;
    }

    let mut update_stmt = conn.prepare("UPDATE symbols SET pagerank_score = ?1 WHERE id = ?2;")?;
    for &id in &symbol_ids {
        update_stmt.execute(rusqlite::params![scores[&id], id])?;
    }
    // Note: caller is responsible for commit

    Ok(())
}

#[pyfunction]
#[pyo3(signature = (db, damping=0.85, epsilon=1e-6))]
pub fn recompute_pagerank(
    db: &crate::store::database::Database,
    damping: f64,
    epsilon: f64,
) -> PyResult<()> {
    let conn = db.connect_internal()?;
    recompute_pagerank_impl(&conn, damping, epsilon)?;
    conn.execute_batch("COMMIT;").ok();
    Ok(())
}
