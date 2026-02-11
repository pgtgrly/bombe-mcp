//! Bombe core library — Rust backend for the Bombe code retrieval MCP server.
//!
//! This crate provides high-performance implementations of the indexer, query
//! engines, store layer, and sharding/federation components.  It is compiled as
//! a Python extension module (`_bombe_core`) via PyO3 and can be used as a
//! drop-in replacement for the pure-Python implementations.

pub mod errors;
pub mod indexer;
pub mod models;
pub mod query;
pub mod store;

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

// ---------------------------------------------------------------------------
// Top-level Python module: _bombe_core
// ---------------------------------------------------------------------------

#[pymodule]
fn _bombe_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // -- Models (constants, helper functions, 33 dataclass pyclasses) --------
    models::register_models(m)?;

    // -- Store layer --------------------------------------------------------
    m.add_class::<store::database::Database>()?;

    // -- Sharding -----------------------------------------------------------
    m.add_class::<store::sharding::catalog::ShardCatalog>()?;
    m.add_class::<store::sharding::router::ShardRouter>()?;

    // -- Query: guards (constants + clamping functions) ----------------------
    m.add("MAX_QUERY_LENGTH", query::guards::MAX_QUERY_LENGTH)?;
    m.add("MAX_SEARCH_LIMIT", query::guards::MAX_SEARCH_LIMIT)?;
    m.add("MAX_REFERENCE_DEPTH", query::guards::MAX_REFERENCE_DEPTH)?;
    m.add(
        "MAX_CONTEXT_EXPANSION_DEPTH",
        query::guards::MAX_CONTEXT_EXPANSION_DEPTH,
    )?;
    m.add("MAX_CONTEXT_SEEDS", query::guards::MAX_CONTEXT_SEEDS)?;
    m.add(
        "MAX_CONTEXT_TOKEN_BUDGET",
        query::guards::MAX_CONTEXT_TOKEN_BUDGET,
    )?;
    m.add(
        "MIN_CONTEXT_TOKEN_BUDGET",
        query::guards::MIN_CONTEXT_TOKEN_BUDGET,
    )?;
    m.add("MAX_GRAPH_VISITED", query::guards::MAX_GRAPH_VISITED)?;
    m.add("MAX_GRAPH_EDGES", query::guards::MAX_GRAPH_EDGES)?;
    m.add("MAX_BLAST_DEPTH", query::guards::MAX_BLAST_DEPTH)?;
    m.add("MAX_ENTRY_POINTS", query::guards::MAX_ENTRY_POINTS)?;
    m.add(
        "MAX_FEDERATED_RESULTS",
        query::guards::MAX_FEDERATED_RESULTS,
    )?;
    m.add("MAX_SHARDS_PER_QUERY", query::guards::MAX_SHARDS_PER_QUERY)?;
    m.add(
        "MAX_CROSS_REPO_EDGES_PER_QUERY",
        query::guards::MAX_CROSS_REPO_EDGES_PER_QUERY,
    )?;

    m.add_function(wrap_pyfunction!(query::guards::clamp_int, m)?)?;
    m.add_function(wrap_pyfunction!(query::guards::clamp_depth, m)?)?;
    m.add_function(wrap_pyfunction!(query::guards::clamp_budget, m)?)?;
    m.add_function(wrap_pyfunction!(query::guards::clamp_limit, m)?)?;
    m.add_function(wrap_pyfunction!(query::guards::truncate_query, m)?)?;
    m.add_function(wrap_pyfunction!(query::guards::adaptive_graph_cap, m)?)?;

    // -- Query: tokenizer ---------------------------------------------------
    m.add_function(wrap_pyfunction!(query::tokenizer::estimate_tokens, m)?)?;

    // -- Query: hybrid scoring ----------------------------------------------
    m.add_function(wrap_pyfunction!(query::hybrid::hybrid_search_enabled, m)?)?;
    m.add_function(wrap_pyfunction!(query::hybrid::semantic_vector_enabled, m)?)?;
    m.add_function(wrap_pyfunction!(query::hybrid::lexical_score, m)?)?;
    m.add_function(wrap_pyfunction!(query::hybrid::structural_score, m)?)?;
    m.add_function(wrap_pyfunction!(query::hybrid::semantic_score, m)?)?;
    m.add_function(wrap_pyfunction!(query::hybrid::rank_symbol, m)?)?;

    // -- Query: main engines ------------------------------------------------
    m.add_function(wrap_pyfunction!(query::search::search_symbols, m)?)?;
    m.add_function(wrap_pyfunction!(query::references::get_references, m)?)?;
    m.add_function(wrap_pyfunction!(query::context::get_context, m)?)?;
    m.add_function(wrap_pyfunction!(query::blast::get_blast_radius, m)?)?;
    m.add_function(wrap_pyfunction!(query::data_flow::trace_data_flow, m)?)?;
    m.add_function(wrap_pyfunction!(query::change_impact::change_impact, m)?)?;
    m.add_function(wrap_pyfunction!(query::structure::get_structure, m)?)?;

    // -- Query: planner (LRU cache) -----------------------------------------
    m.add_class::<query::planner::QueryPlanner>()?;

    // -- Query: federated ---------------------------------------------------
    m.add_class::<query::federated::planner::ShardQueryPlan>()?;
    m.add_class::<query::federated::planner::FederatedQueryPlanner>()?;
    m.add_class::<query::federated::executor::FederatedQueryExecutor>()?;

    // -- Sharding: resolver functions ----------------------------------------
    m.add_function(wrap_pyfunction!(
        store::sharding::resolver::compute_repo_id,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        store::sharding::resolver::resolve_cross_repo_imports,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        store::sharding::resolver::post_index_cross_repo_sync,
        m
    )?)?;

    // -- Indexer: functions --------------------------------------------------
    m.add_function(wrap_pyfunction!(indexer::filesystem::detect_language, m)?)?;
    m.add_function(wrap_pyfunction!(
        indexer::filesystem::compute_content_hash,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        indexer::parser::tree_sitter_capability_report,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(indexer::pagerank::recompute_pagerank, m)?)?;
    m.add_function(wrap_pyfunction!(indexer::pipeline::rust_full_index, m)?)?;

    Ok(())
}
