"""Shared guardrails for query payload bounds and traversal limits (re-exported from Rust)."""

from _bombe_core import (
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    MAX_REFERENCE_DEPTH,
    MAX_CONTEXT_TOKEN_BUDGET,
    MIN_CONTEXT_TOKEN_BUDGET,
    MAX_CONTEXT_EXPANSION_DEPTH,
    MAX_GRAPH_VISITED,
    MAX_GRAPH_EDGES,
    MAX_CONTEXT_SEEDS,
    MAX_ENTRY_POINTS,
    MAX_BLAST_DEPTH,
    MAX_SHARDS_PER_QUERY,
    MAX_CROSS_REPO_EDGES_PER_QUERY,
    MAX_FEDERATED_RESULTS,
    clamp_int,
    clamp_depth,
    clamp_budget,
    clamp_limit,
    truncate_query,
    adaptive_graph_cap,
)

# Python-only constants not in _bombe_core but used by tools/definitions.py
MAX_FLOW_DEPTH = 6
MAX_IMPACT_DEPTH = 6
MAX_STRUCTURE_TOKEN_BUDGET = 32000
MIN_STRUCTURE_TOKEN_BUDGET = 1
FEDERATED_SHARD_TIMEOUT_MS = 5000
MAX_EXPORTED_SYMBOLS_REFRESH = 50000

__all__ = [
    "MAX_QUERY_LENGTH",
    "MAX_SEARCH_LIMIT",
    "MAX_REFERENCE_DEPTH",
    "MAX_CONTEXT_TOKEN_BUDGET",
    "MIN_CONTEXT_TOKEN_BUDGET",
    "MAX_CONTEXT_EXPANSION_DEPTH",
    "MAX_GRAPH_VISITED",
    "MAX_GRAPH_EDGES",
    "MAX_CONTEXT_SEEDS",
    "MAX_ENTRY_POINTS",
    "MAX_BLAST_DEPTH",
    "MAX_SHARDS_PER_QUERY",
    "MAX_CROSS_REPO_EDGES_PER_QUERY",
    "MAX_FEDERATED_RESULTS",
    "clamp_int",
    "clamp_depth",
    "clamp_budget",
    "clamp_limit",
    "truncate_query",
    "adaptive_graph_cap",
    "MAX_FLOW_DEPTH",
    "MAX_IMPACT_DEPTH",
    "MAX_STRUCTURE_TOKEN_BUDGET",
    "MIN_STRUCTURE_TOKEN_BUDGET",
    "FEDERATED_SHARD_TIMEOUT_MS",
    "MAX_EXPORTED_SYMBOLS_REFRESH",
]
