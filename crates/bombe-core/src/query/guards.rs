//! Shared guardrails for query payload bounds and traversal limits.

use pyo3::prelude::*;

// Core query guards
pub const MAX_QUERY_LENGTH: usize = 512;
pub const MAX_SEARCH_LIMIT: i64 = 100;
pub const MAX_REFERENCE_DEPTH: i64 = 6;
pub const MAX_FLOW_DEPTH: i64 = 6;
pub const MAX_IMPACT_DEPTH: i64 = 6;
pub const MAX_CONTEXT_TOKEN_BUDGET: i64 = 32000;
pub const MIN_CONTEXT_TOKEN_BUDGET: i64 = 1;
pub const MAX_CONTEXT_EXPANSION_DEPTH: i64 = 4;
pub const MAX_STRUCTURE_TOKEN_BUDGET: i64 = 32000;
pub const MIN_STRUCTURE_TOKEN_BUDGET: i64 = 1;
pub const MAX_GRAPH_VISITED: i64 = 2000;
pub const MAX_GRAPH_EDGES: i64 = 5000;
pub const MAX_CONTEXT_SEEDS: usize = 32;
pub const MAX_ENTRY_POINTS: i64 = 32;
pub const MAX_BLAST_DEPTH: i64 = 6;

// Federated query guards
pub const MAX_SHARDS_PER_QUERY: i64 = 16;
pub const MAX_CROSS_REPO_EDGES_PER_QUERY: i64 = 200;
pub const MAX_FEDERATED_RESULTS: i64 = 500;
pub const FEDERATED_SHARD_TIMEOUT_MS: i64 = 5000;
pub const MAX_EXPORTED_SYMBOLS_REFRESH: i64 = 50000;

#[pyfunction]
pub fn clamp_int(value: i64, minimum: i64, maximum: i64) -> i64 {
    value.max(minimum).min(maximum)
}

#[pyfunction]
pub fn clamp_depth(value: i64, maximum: i64) -> i64 {
    clamp_int(value, 1, maximum)
}

#[pyfunction]
pub fn clamp_budget(value: i64, minimum: i64, maximum: i64) -> i64 {
    clamp_int(value, minimum, maximum)
}

#[pyfunction]
pub fn clamp_limit(value: i64, maximum: i64) -> i64 {
    clamp_int(value, 1, maximum)
}

#[pyfunction]
pub fn truncate_query(query: &str) -> String {
    let stripped = query.trim();
    if stripped.len() <= MAX_QUERY_LENGTH {
        stripped.to_string()
    } else {
        stripped[..MAX_QUERY_LENGTH].to_string()
    }
}

#[pyfunction]
#[pyo3(signature = (total_symbols, base_cap, floor=None))]
pub fn adaptive_graph_cap(total_symbols: i64, base_cap: i64, floor: Option<i64>) -> i64 {
    let floor = floor.unwrap_or(200);
    let bounded_total = total_symbols.max(0);
    let estimated = floor.max((bounded_total.max(1) as f64 * 0.2) as i64);
    clamp_int(estimated, floor, base_cap)
}
