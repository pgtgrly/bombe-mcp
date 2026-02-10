"""Federated query executor for cross-repo shard groups."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from bombe.models import (
    BlastRadiusRequest,
    CrossRepoEdge,
    FederatedQueryResult,
    ReferenceRequest,
    SymbolSearchRequest,
    SymbolSearchResponse,
)
from bombe.query.blast import get_blast_radius
from bombe.query.guards import MAX_FEDERATED_RESULTS
from bombe.query.references import get_references
from bombe.query.search import search_symbols
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.router import ShardRouter

from .planner import FederatedQueryPlanner


class FederatedQueryExecutor:
    """Executes federated query plans across shards."""

    def __init__(self, catalog: ShardCatalog, router: ShardRouter, planner: FederatedQueryPlanner) -> None:
        self._catalog = catalog
        self._router = router
        self._planner = planner
        self._logger = logging.getLogger(__name__)

    def execute_search(self, req: SymbolSearchRequest) -> FederatedQueryResult:
        """Execute a federated symbol search."""
        started = time.perf_counter()
        plan = self._planner.plan_search(req.query, req.kind, req.limit)
        all_results: list[dict[str, Any]] = []
        shard_reports: list[dict[str, Any]] = []
        shards_failed = 0

        for shard_id in plan.shard_ids:
            result, report = self._execute_on_shard(
                shard_id,
                lambda db, sid=shard_id: self._search_on_shard(db, req, sid),  # type: ignore[misc]
            )
            shard_reports.append(report)
            if result is None:
                shards_failed += 1
                continue
            all_results.extend(result.get("symbols", []))

        # Sort by importance score descending, then qualified_name
        all_results.sort(
            key=lambda r: (-float(r.get("importance_score", 0.0)), str(r.get("qualified_name", ""))),
        )
        paged = all_results[:MAX_FEDERATED_RESULTS][:req.limit]

        elapsed = int((time.perf_counter() - started) * 1000)
        return FederatedQueryResult(
            results=paged,
            shard_reports=shard_reports,
            total_matches=len(all_results),
            shards_queried=len(plan.shard_ids),
            shards_failed=shards_failed,
            elapsed_ms=elapsed,
        )

    def _search_on_shard(self, db: Database, req: SymbolSearchRequest, shard_id: str) -> dict[str, Any]:
        """Run search on a single shard and annotate results."""
        response: SymbolSearchResponse = search_symbols(db, req)
        # Annotate each symbol with shard info
        shard_info = self._catalog.get_shard(shard_id)
        for sym in response.symbols:
            if isinstance(sym, dict):
                sym["shard_repo_id"] = shard_id
                sym["shard_repo_path"] = shard_info.repo_path if shard_info else ""
        return {"symbols": response.symbols, "total_matches": response.total_matches}

    def execute_references(self, symbol_name: str, direction: str = "both", depth: int = 1, include_source: bool = False) -> FederatedQueryResult:
        """Execute a federated reference query."""
        started = time.perf_counter()
        plan = self._planner.plan_references(symbol_name, direction, depth)
        all_results: list[dict[str, Any]] = []
        shard_reports: list[dict[str, Any]] = []
        shards_failed = 0

        req = ReferenceRequest(symbol_name=symbol_name, direction=direction, depth=depth, include_source=include_source)

        for shard_id in plan.shard_ids:
            result, report = self._execute_on_shard(
                shard_id,
                lambda db, sid=shard_id: self._references_on_shard(db, req, sid),  # type: ignore[misc]
            )
            shard_reports.append(report)
            if result is None:
                shards_failed += 1
                continue
            all_results.append(result)

        # Build merged result with cross-repo edges
        merged = self._merge_reference_results(all_results, plan.cross_repo_edges)

        elapsed = int((time.perf_counter() - started) * 1000)
        return FederatedQueryResult(
            results=[merged],
            shard_reports=shard_reports,
            total_matches=len(merged.get("callers", [])) + len(merged.get("callees", [])),
            shards_queried=len(plan.shard_ids),
            shards_failed=shards_failed,
            elapsed_ms=elapsed,
        )

    def _references_on_shard(self, db: Database, req: ReferenceRequest, shard_id: str) -> dict[str, Any]:
        """Run reference query on a single shard."""
        try:
            response = get_references(db, req)
            payload: dict[str, Any] = response.payload if hasattr(response, "payload") else response  # type: ignore[assignment]
            payload["shard_repo_id"] = shard_id
            return payload
        except (ValueError, KeyError):
            # Symbol not found in this shard — expected for federated queries
            return {"callers": [], "callees": [], "shard_repo_id": shard_id, "symbol_not_found": True}

    def execute_blast_radius(self, symbol_name: str, change_type: str = "behavior", max_depth: int = 3) -> FederatedQueryResult:
        """Execute a federated blast radius query."""
        started = time.perf_counter()
        plan = self._planner.plan_blast_radius(symbol_name, max_depth)
        all_results: list[dict[str, Any]] = []
        shard_reports: list[dict[str, Any]] = []
        shards_failed = 0

        req = BlastRadiusRequest(symbol_name=symbol_name, change_type=change_type, max_depth=max_depth)

        for shard_id in plan.shard_ids:
            result, report = self._execute_on_shard(
                shard_id,
                lambda db, sid=shard_id: self._blast_on_shard(db, req, sid),  # type: ignore[misc]
            )
            shard_reports.append(report)
            if result is None:
                shards_failed += 1
                continue
            all_results.append(result)

        elapsed = int((time.perf_counter() - started) * 1000)
        return FederatedQueryResult(
            results=all_results,
            shard_reports=shard_reports,
            total_matches=sum(
                r.get("impact", {}).get("total_affected_symbols", 0) for r in all_results if isinstance(r, dict)
            ),
            shards_queried=len(plan.shard_ids),
            shards_failed=shards_failed,
            elapsed_ms=elapsed,
        )

    def _blast_on_shard(self, db: Database, req: BlastRadiusRequest, shard_id: str) -> dict[str, Any]:
        """Run blast radius on a single shard."""
        try:
            response = get_blast_radius(db, req)
            payload: dict[str, Any] = response.payload if hasattr(response, "payload") else response  # type: ignore[assignment]
            payload["shard_repo_id"] = shard_id
            return payload
        except (ValueError, KeyError):
            return {"impact": {"total_affected_symbols": 0}, "shard_repo_id": shard_id, "symbol_not_found": True}

    def _execute_on_shard(
        self,
        shard_id: str,
        operation: Callable[[Database], dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Execute an operation on a single shard with error handling."""
        report: dict[str, Any] = {
            "shard_id": shard_id,
            "status": "ok",
            "latency_ms": 0,
            "error": None,
        }
        started = time.perf_counter()
        try:
            db = self._router.get_shard_db(shard_id)
            if db is None:
                report["status"] = "unavailable"
                report["error"] = "shard database not accessible"
                return None, report
            result = operation(db)
            report["latency_ms"] = int((time.perf_counter() - started) * 1000)
            return result, report
        except Exception as exc:
            report["status"] = "error"
            report["error"] = str(exc)
            report["latency_ms"] = int((time.perf_counter() - started) * 1000)
            self._logger.warning("Shard query failed for %s: %s", shard_id, exc)
            return None, report

    def _merge_reference_results(
        self,
        shard_results: list[dict[str, Any]],
        cross_repo_edges: list[CrossRepoEdge],
    ) -> dict[str, Any]:
        """Merge local and cross-repo reference results."""
        merged: dict[str, Any] = {
            "callers": [],
            "callees": [],
            "implementors": [],
            "supers": [],
            "cross_repo_callers": [],
            "cross_repo_callees": [],
        }
        for result in shard_results:
            if not isinstance(result, dict):
                continue
            if result.get("symbol_not_found"):
                continue
            for key in ("callers", "callees", "implementors", "supers"):
                items = result.get(key, [])
                if isinstance(items, list):
                    shard_id = result.get("shard_repo_id", "")
                    for item in items:
                        if isinstance(item, dict):
                            item["shard_repo_id"] = shard_id
                    merged[key].extend(items)

        # Add cross-repo edges as separate entries
        for edge in cross_repo_edges:
            entry = {
                "source_repo_id": edge.source_uri.repo_id,
                "source_qualified_name": edge.source_uri.qualified_name,
                "source_file_path": edge.source_uri.file_path,
                "target_repo_id": edge.target_uri.repo_id,
                "target_qualified_name": edge.target_uri.qualified_name,
                "target_file_path": edge.target_uri.file_path,
                "relationship": edge.relationship,
                "confidence": edge.confidence,
                "provenance": edge.provenance,
            }
            if edge.relationship == "IMPORTS":
                merged["cross_repo_callees"].append(entry)
            else:
                merged["cross_repo_callers"].append(entry)

        return merged
