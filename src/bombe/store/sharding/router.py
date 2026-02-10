"""Shard router: routes queries to appropriate shards and manages connection pooling."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from bombe.query.guards import MAX_SHARDS_PER_QUERY
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog


class ShardRouter:
    """Routes queries to appropriate shards and manages shard connections."""

    def __init__(self, catalog: ShardCatalog, max_connections: int = 8) -> None:
        self._catalog = catalog
        self._max_connections = max_connections
        self._connection_pool: dict[str, Database] = {}
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Connection pooling
    # ------------------------------------------------------------------

    def get_shard_db(self, repo_id: str) -> Database | None:
        """Return a Database for the given shard, with connection pooling.

        Checks if shard exists in catalog, then returns cached or new Database.
        If shard not found or db_path doesn't exist, returns None.
        Evicts oldest connection if pool exceeds max_connections.
        """
        with self._lock:
            # 1. Check the pool first
            if repo_id in self._connection_pool:
                return self._connection_pool[repo_id]

            # 2. Look up the shard in the catalog
            shard = self._catalog.get_shard(repo_id)
            if shard is None:
                self._logger.debug("Shard not found in catalog: %s", repo_id)
                return None

            # 3. Verify the db_path exists
            db_path = Path(shard.db_path)
            if not db_path.exists():
                self._logger.debug(
                    "Shard db_path does not exist: %s (repo_id=%s)",
                    shard.db_path,
                    repo_id,
                )
                return None

            # 4. Create Database, init schema, cache it
            db = Database(db_path)
            db.init_schema()

            # 5. Evict oldest entry if pool is full
            if len(self._connection_pool) >= self._max_connections:
                oldest_key = next(iter(self._connection_pool))
                self._logger.debug(
                    "Evicting pooled connection for repo_id=%s to make room",
                    oldest_key,
                )
                del self._connection_pool[oldest_key]

            self._connection_pool[repo_id] = db
            return db

    # ------------------------------------------------------------------
    # Query routing
    # ------------------------------------------------------------------

    def route_symbol_query(self, symbol_name: str) -> list[str]:
        """Determine which shard repo_ids may contain the named symbol.

        Uses exported_symbols cache: search for symbol_name in exported_symbols.
        Returns list of unique repo_ids where the symbol was found.
        Falls back to all_shard_ids() if no cache hits.
        Capped to MAX_SHARDS_PER_QUERY.
        """
        try:
            hits = self._catalog.search_exported_symbols(symbol_name, kind="any", limit=100)
        except Exception:
            self._logger.debug("Exported symbol search failed for %s", symbol_name)
            return self.all_shard_ids()

        matched_repo_ids: list[str] = []
        seen: set[str] = set()
        for hit in hits:
            rid = str(hit["repo_id"])
            if rid not in seen:
                matched_repo_ids.append(rid)
                seen.add(rid)

        if not matched_repo_ids:
            return self.all_shard_ids()

        return matched_repo_ids[:MAX_SHARDS_PER_QUERY]

    def route_reference_query(
        self,
        symbol_name: str,
        source_repo_id: str | None = None,
    ) -> list[str]:
        """Determine shards for a reference/caller/callee query.

        1. Start with source_repo_id if provided
        2. Add shards found via route_symbol_query
        3. Add shards connected via cross_repo_edges (both from and to)
        4. Deduplicate and cap to MAX_SHARDS_PER_QUERY
        """
        result: list[str] = []
        seen: set[str] = set()

        # 1. Start with source_repo_id if provided
        if source_repo_id is not None:
            result.append(source_repo_id)
            seen.add(source_repo_id)

        # 2. Add shards found via route_symbol_query
        for repo_id in self.route_symbol_query(symbol_name):
            if repo_id not in seen:
                result.append(repo_id)
                seen.add(repo_id)

        # 3. Add shards connected via cross_repo_edges (both from and to)
        for rid in list(seen):
            try:
                outgoing = self._catalog.get_cross_repo_edges_from(rid, symbol_name)
                for edge in outgoing:
                    to_repo = edge.target_uri.repo_id
                    if to_repo not in seen:
                        result.append(to_repo)
                        seen.add(to_repo)
            except Exception:
                self._logger.debug("Failed to get outgoing edges for %s/%s", rid, symbol_name)
            try:
                incoming = self._catalog.get_cross_repo_edges_to(rid, symbol_name)
                for edge in incoming:
                    from_repo = edge.source_uri.repo_id
                    if from_repo not in seen:
                        result.append(from_repo)
                        seen.add(from_repo)
            except Exception:
                self._logger.debug("Failed to get incoming edges for %s/%s", rid, symbol_name)

        # 4. Cap to MAX_SHARDS_PER_QUERY
        return result[:MAX_SHARDS_PER_QUERY]

    def all_shard_ids(self) -> list[str]:
        """Return all enabled shard repo_ids, capped to MAX_SHARDS_PER_QUERY."""
        shards = self._catalog.list_shards(enabled_only=True)
        return [s.repo_id for s in shards][:MAX_SHARDS_PER_QUERY]

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    def shard_health(self) -> list[dict[str, Any]]:
        """Return health status for each enabled shard.

        For each shard: check if db_path exists, try to connect and query
        SELECT COUNT(*) FROM symbols, report status (ok/unavailable/error).
        Include repo_id, repo_path, db_path, status, symbol_count, error (if any).
        """
        all_ids = self.all_shard_ids()
        reports: list[dict[str, Any]] = []

        for repo_id in all_ids:
            shard = self._catalog.get_shard(repo_id)
            if shard is None:
                reports.append(
                    {
                        "repo_id": repo_id,
                        "repo_path": None,
                        "db_path": None,
                        "status": "unavailable",
                        "symbol_count": 0,
                        "error": "shard not found in catalog",
                    }
                )
                continue

            report: dict[str, Any] = {
                "repo_id": shard.repo_id,
                "repo_path": shard.repo_path,
                "db_path": shard.db_path,
                "status": "unavailable",
                "symbol_count": 0,
                "error": None,
            }

            db_path = Path(shard.db_path)
            if not db_path.exists():
                report["error"] = f"db_path does not exist: {shard.db_path}"
                reports.append(report)
                continue

            try:
                db = self.get_shard_db(repo_id)
                if db is None:
                    report["error"] = "failed to obtain database connection"
                    reports.append(report)
                    continue

                rows = db.query("SELECT COUNT(*) AS cnt FROM symbols;")
                count = int(rows[0]["cnt"]) if rows else 0
                report["status"] = "ok"
                report["symbol_count"] = count
            except Exception as exc:  # noqa: BLE001
                report["status"] = "error"
                report["error"] = str(exc)
                self._logger.warning(
                    "Health check failed for shard %s: %s",
                    repo_id,
                    exc,
                )

            reports.append(report)

        return reports

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close_all(self) -> None:
        """Release all pooled connections (clear the pool dict)."""
        with self._lock:
            self._connection_pool.clear()
