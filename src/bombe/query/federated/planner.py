"""Federated query planner for cross-repo shard groups."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from bombe.models import CrossRepoEdge
from bombe.query.guards import MAX_SHARDS_PER_QUERY, MAX_CROSS_REPO_EDGES_PER_QUERY
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.router import ShardRouter


@dataclass(frozen=True)
class ShardQueryPlan:
    """A plan for executing a query across shards."""
    shard_ids: list[str] = field(default_factory=list)
    cross_repo_edges: list[CrossRepoEdge] = field(default_factory=list)
    fan_out_strategy: str = "all"  # "all", "routed", "single"
    merge_strategy: str = "score_sort"  # "score_sort", "depth_merge", "union"


class FederatedQueryPlanner:
    """Plans queries across a shard group."""

    def __init__(self, catalog: ShardCatalog, router: ShardRouter) -> None:
        self._catalog = catalog
        self._router = router
        self._logger = logging.getLogger(__name__)

    def plan_search(self, query: str, kind: str = "any", limit: int = 20) -> ShardQueryPlan:
        """Plan: fan out to all shards, merge by score."""
        shard_ids = self._router.all_shard_ids()
        return ShardQueryPlan(
            shard_ids=shard_ids,
            fan_out_strategy="all",
            merge_strategy="score_sort",
        )

    def plan_references(self, symbol_name: str, direction: str, depth: int, source_repo_id: str | None = None) -> ShardQueryPlan:
        """Plan: start with routed shard, expand via cross-repo edges."""
        shard_ids = self._router.route_reference_query(symbol_name, source_repo_id)
        # Collect cross-repo edges relevant to this symbol
        cross_edges: list[CrossRepoEdge] = []
        for sid in shard_ids:
            if direction in ("callers", "both"):
                cross_edges.extend(self._catalog.get_cross_repo_edges_to(sid, symbol_name))
            if direction in ("callees", "both"):
                cross_edges.extend(self._catalog.get_cross_repo_edges_from(sid, symbol_name))
        # Deduplicate and cap
        seen: set[str] = set()
        unique_edges: list[CrossRepoEdge] = []
        for e in cross_edges:
            key = f"{e.source_uri.uri}|{e.target_uri.uri}|{e.relationship}"
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)
        unique_edges = unique_edges[:MAX_CROSS_REPO_EDGES_PER_QUERY]
        # Add any target shard_ids from edges not already in the list
        shard_set = set(shard_ids)
        for e in unique_edges:
            for rid in (e.source_uri.repo_id, e.target_uri.repo_id):
                if rid not in shard_set:
                    shard_ids.append(rid)
                    shard_set.add(rid)
        shard_ids = shard_ids[:MAX_SHARDS_PER_QUERY]
        return ShardQueryPlan(
            shard_ids=shard_ids,
            cross_repo_edges=unique_edges,
            fan_out_strategy="routed",
            merge_strategy="depth_merge",
        )

    def plan_blast_radius(self, symbol_name: str, max_depth: int) -> ShardQueryPlan:
        """Plan: routed shard + cross-repo callers."""
        # Similar to references with direction="callers"
        return self.plan_references(symbol_name, direction="callers", depth=max_depth)

    def plan_context(self, query: str, entry_points: list[str]) -> ShardQueryPlan:
        """Plan: resolve entry points to shards, then expand."""
        shard_set: set[str] = set()
        shard_ids: list[str] = []
        for ep in entry_points:
            for sid in self._router.route_symbol_query(ep):
                if sid not in shard_set:
                    shard_ids.append(sid)
                    shard_set.add(sid)
        if not shard_ids:
            shard_ids = self._router.all_shard_ids()
        return ShardQueryPlan(
            shard_ids=shard_ids[:MAX_SHARDS_PER_QUERY],
            fan_out_strategy="routed",
            merge_strategy="score_sort",
        )
