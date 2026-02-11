"""Federated query planner wrapper (Rust with Python-compatible API)."""

from __future__ import annotations

from _bombe_core import FederatedQueryPlanner as _RustPlanner
from _bombe_core import ShardQueryPlan


class FederatedQueryPlanner:
    """Wrapper around Rust FederatedQueryPlanner."""

    def __init__(self, catalog, router):
        inner_catalog = getattr(catalog, "_inner", catalog)
        inner_router = getattr(router, "_inner", router)
        self._inner = _RustPlanner(inner_catalog, inner_router)

    def plan_search(self, query, kind="any", limit=20):
        return self._inner.plan_search(query, kind, limit)

    def plan_references(self, symbol_name, direction, depth, source_repo_id=None):
        return self._inner.plan_references(symbol_name, direction, depth, source_repo_id)

    def plan_blast_radius(self, symbol_name, max_depth):
        return self._inner.plan_blast_radius(symbol_name, max_depth)

    def plan_context(self, query, entry_points):
        return self._inner.plan_context(query, entry_points)


__all__ = ["ShardQueryPlan", "FederatedQueryPlanner"]
