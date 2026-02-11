//! Hybrid scoring helpers for symbol retrieval.

use pyo3::prelude::*;
use regex::Regex;
use std::collections::HashSet;
use std::sync::LazyLock;

static TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_]+").unwrap());

fn tokens(value: &str) -> HashSet<String> {
    TOKEN_RE
        .find_iter(value)
        .map(|m| m.as_str().to_lowercase())
        .collect()
}

#[pyfunction]
pub fn hybrid_search_enabled() -> bool {
    match std::env::var("BOMBE_HYBRID_SEARCH") {
        Ok(val) => {
            let v = val.trim().to_lowercase();
            !matches!(v.as_str(), "0" | "false" | "no" | "off")
        }
        Err(_) => true,
    }
}

#[pyfunction]
pub fn semantic_vector_enabled() -> bool {
    match std::env::var("BOMBE_HYBRID_VECTOR") {
        Ok(val) => {
            let v = val.trim().to_lowercase();
            matches!(v.as_str(), "1" | "true" | "yes" | "on")
        }
        Err(_) => false,
    }
}

#[pyfunction]
pub fn lexical_score(query: &str, name: &str, qualified_name: &str) -> f64 {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return 0.0;
    }
    let n = name.to_lowercase();
    let qn = qualified_name.to_lowercase();
    if q == n || q == qn {
        return 1.0;
    }
    if n.contains(&q) {
        return 0.9;
    }
    if qn.contains(&q) {
        return 0.8;
    }
    let query_tokens = tokens(query);
    if query_tokens.is_empty() {
        return 0.0;
    }
    let target_tokens = tokens(&format!("{name} {qualified_name}"));
    if target_tokens.is_empty() {
        return 0.0;
    }
    let overlap = query_tokens.intersection(&target_tokens).count();
    overlap as f64 / query_tokens.len().max(1) as f64
}

#[pyfunction]
pub fn structural_score(pagerank: f64, callers: i64, callees: i64) -> f64 {
    let pagerank_component = pagerank.max(0.0);
    let traffic_component = ((callers.max(0) + callees.max(0)) as f64 + 1.0).ln();
    pagerank_component + (traffic_component * 0.1)
}

#[pyfunction]
#[pyo3(signature = (query, signature=None, docstring=None))]
pub fn semantic_score(query: &str, signature: Option<&str>, docstring: Option<&str>) -> f64 {
    if !semantic_vector_enabled() {
        return 0.0;
    }
    let query_tokens = tokens(query);
    if query_tokens.is_empty() {
        return 0.0;
    }
    let corpus = format!("{} {}", signature.unwrap_or(""), docstring.unwrap_or(""));
    let corpus_tokens = tokens(&corpus);
    if corpus_tokens.is_empty() {
        return 0.0;
    }
    let overlap = query_tokens.intersection(&corpus_tokens).count();
    overlap as f64 / query_tokens.len().max(1) as f64
}

#[pyfunction]
#[pyo3(signature = (*, query, name, qualified_name, signature=None, docstring=None, pagerank, callers, callees))]
#[allow(clippy::too_many_arguments)]
pub fn rank_symbol(
    query: &str,
    name: &str,
    qualified_name: &str,
    signature: Option<&str>,
    docstring: Option<&str>,
    pagerank: f64,
    callers: i64,
    callees: i64,
) -> f64 {
    let lex = lexical_score(query, name, qualified_name);
    let struc = structural_score(pagerank, callers, callees);
    let sem = semantic_score(query, signature, docstring);
    if !hybrid_search_enabled() {
        return struc;
    }
    (lex * 0.55) + (struc * 0.35) + (sem * 0.1)
}
