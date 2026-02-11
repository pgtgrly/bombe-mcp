"""ShardRouter wrapper providing Python-compatible API over Rust core."""

from __future__ import annotations

from _bombe_core import ShardRouter as _RustShardRouter


class ShardRouter:
    """Wrapper around Rust ShardRouter with Python-compatible API."""

    def __init__(self, catalog, max_connections=8):
        # Unwrap Python ShardCatalog wrapper to get inner Rust object
        inner_catalog = getattr(catalog, "_inner", catalog)
        self._inner = _RustShardRouter(inner_catalog, max_connections)
        self._connection_pool = {}

    def get_shard_db(self, repo_id):
        result = self._inner.get_shard_db(repo_id)
        if result is not None:
            self._connection_pool[repo_id] = result
        return result

    def route_symbol_query(self, symbol_name):
        return self._inner.route_symbol_query(symbol_name)

    def route_reference_query(self, symbol_name, source_repo_id=None):
        return self._inner.route_reference_query(symbol_name, source_repo_id)

    def all_shard_ids(self):
        return self._inner.all_shard_ids()

    def shard_health(self):
        return self._inner.shard_health()

    def close_all(self):
        self._inner.close_all()
        self._connection_pool.clear()


__all__ = ["ShardRouter"]
