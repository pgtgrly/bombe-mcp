"""Shared guardrails for query payload bounds and traversal limits."""

from __future__ import annotations

MAX_QUERY_LENGTH = 512
MAX_SEARCH_LIMIT = 100
MAX_REFERENCE_DEPTH = 6
MAX_FLOW_DEPTH = 6
MAX_IMPACT_DEPTH = 6
MAX_CONTEXT_TOKEN_BUDGET = 32000
MIN_CONTEXT_TOKEN_BUDGET = 1
MAX_CONTEXT_EXPANSION_DEPTH = 4
MAX_STRUCTURE_TOKEN_BUDGET = 32000
MIN_STRUCTURE_TOKEN_BUDGET = 1
MAX_GRAPH_VISITED = 2000
MAX_GRAPH_EDGES = 5000
MAX_CONTEXT_SEEDS = 32

# Federated query guards
MAX_SHARDS_PER_QUERY = 16
MAX_CROSS_REPO_EDGES_PER_QUERY = 200
MAX_FEDERATED_RESULTS = 500
FEDERATED_SHARD_TIMEOUT_MS = 5000
MAX_EXPORTED_SYMBOLS_REFRESH = 50000


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def clamp_depth(value: int, *, maximum: int) -> int:
    return clamp_int(value, 1, maximum)


def clamp_budget(value: int, *, minimum: int, maximum: int) -> int:
    return clamp_int(value, minimum, maximum)


def clamp_limit(value: int, *, maximum: int) -> int:
    return clamp_int(value, 1, maximum)


def truncate_query(query: str) -> str:
    stripped = query.strip()
    return stripped[:MAX_QUERY_LENGTH]


def adaptive_graph_cap(total_symbols: int, base_cap: int, floor: int = 200) -> int:
    bounded_total = max(0, int(total_symbols))
    estimated = max(floor, int(max(1, bounded_total) * 0.2))
    return clamp_int(estimated, floor, base_cap)
