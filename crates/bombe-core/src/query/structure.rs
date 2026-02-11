//! Repository structure map generation backend.

use std::collections::BTreeMap;

use pyo3::prelude::*;
use rusqlite::Connection;

use crate::errors::BombeResult;

fn approx_tokens(text: &str) -> i64 {
    if text.is_empty() {
        return 0;
    }
    (text.len() as f64 / 3.5).max(1.0) as i64
}

pub fn get_structure_impl(
    conn: &Connection,
    path: &str,
    token_budget: i64,
    include_signatures: bool,
) -> BombeResult<String> {
    let path_like = if path.is_empty() || path == "." {
        "%".to_string()
    } else {
        let trimmed = path.trim_end_matches('/');
        if path.ends_with('%') {
            path.to_string()
        } else {
            format!("{trimmed}/%")
        }
    };

    let mut stmt = conn.prepare(
        "SELECT file_path, name, kind, signature, pagerank_score \
         FROM symbols WHERE file_path LIKE ?1 \
         ORDER BY pagerank_score DESC, file_path ASC, start_line ASC;",
    )?;

    let rows: Vec<(String, String, String, Option<String>, f64)> = stmt
        .query_map(rusqlite::params![path_like], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get::<_, f64>(4).unwrap_or(0.0),
            ))
        })?
        .filter_map(|r| r.ok())
        .collect();

    let mut grouped: BTreeMap<String, Vec<(String, String, String, f64)>> = BTreeMap::new();
    for (file_path, name, kind, signature, pagerank) in rows {
        grouped.entry(file_path).or_default().push((
            name,
            kind,
            signature.unwrap_or_default(),
            pagerank,
        ));
    }

    let mut lines: Vec<String> = Vec::new();
    let mut rank = 0;
    for (file_path, symbols) in &grouped {
        lines.push(file_path.clone());
        for (name, kind, signature, _score) in symbols {
            rank += 1;
            let marker = if rank <= 10 { "[TOP] " } else { "" };
            let detail = if include_signatures && !signature.is_empty() {
                signature.clone()
            } else {
                format!("{kind} {name}")
            };
            lines.push(format!("  {marker}{detail}  [rank:{rank}]"));
        }
    }

    let mut output_lines: Vec<String> = Vec::new();
    let mut used_tokens = 0i64;
    for line in &lines {
        let line_tokens = approx_tokens(line);
        if used_tokens + line_tokens > token_budget {
            break;
        }
        output_lines.push(line.clone());
        used_tokens += line_tokens;
    }

    Ok(output_lines.join("\n"))
}

#[pyfunction]
#[pyo3(signature = (db, path=".", token_budget=4000, include_signatures=true))]
pub fn get_structure(
    db: &crate::store::database::Database,
    path: &str,
    token_budget: i64,
    include_signatures: bool,
) -> PyResult<String> {
    let conn = db.connect_internal()?;
    Ok(get_structure_impl(
        &conn,
        path,
        token_budget,
        include_signatures,
    )?)
}
