"""ShardCatalog wrapper providing Python-compatible API over Rust core."""

from __future__ import annotations

import sqlite3

from _bombe_core import ShardCatalog as _RustShardCatalog
from bombe.models import CrossRepoEdge, GlobalSymbolURI, ShardInfo


class ShardCatalog:
    """Wrapper around Rust ShardCatalog with Python-compatible API."""

    def __init__(self, catalog_db_path):
        self._inner = _RustShardCatalog(catalog_db_path)
        self._db_path = str(catalog_db_path)

    def init_schema(self):
        return self._inner.init_schema()

    def register_shard(self, shard_or_repo_id, repo_path=None, db_path=None):
        if isinstance(shard_or_repo_id, ShardInfo):
            return self._inner.register_shard(
                shard_or_repo_id.repo_id,
                shard_or_repo_id.repo_path,
                shard_or_repo_id.db_path,
            )
        return self._inner.register_shard(shard_or_repo_id, repo_path, db_path)

    def unregister_shard(self, repo_id):
        return self._inner.unregister_shard(repo_id)

    def list_shards(self, enabled_only=True):
        results = self._inner.list_shards(enabled_only)
        return [_dict_to_shard_info(r) for r in results]

    def get_shard(self, repo_id):
        result = self._inner.get_shard(repo_id)
        if result is None:
            return None
        return _dict_to_shard_info(result)

    def update_shard_stats(self, repo_id, symbol_count, edge_count):
        return self._inner.update_shard_stats(repo_id, symbol_count, edge_count)

    def upsert_cross_repo_edges(self, edges):
        edge_dicts = [_cross_repo_edge_to_dict(e) for e in edges]
        return self._inner.upsert_cross_repo_edges(edge_dicts)

    def get_cross_repo_edges_from(self, repo_id, symbol_name):
        results = self._inner.get_cross_repo_edges_from(repo_id, symbol_name)
        return [_dict_to_cross_repo_edge(r) for r in results]

    def get_cross_repo_edges_to(self, repo_id, symbol_name):
        results = self._inner.get_cross_repo_edges_to(repo_id, symbol_name)
        return [_dict_to_cross_repo_edge(r) for r in results]

    def delete_cross_repo_edges_for_repo(self, repo_id):
        return self._inner.delete_cross_repo_edges_for_repo(repo_id)

    def refresh_exported_symbols(self, repo_id, db):
        return self._inner.refresh_exported_symbols(repo_id, db)

    def search_exported_symbols(self, name, kind="any", limit=20):
        return self._inner.search_exported_symbols(name, kind, limit)

    def resolve_external_import(self, module_name, language):
        return self._inner.resolve_external_import(module_name, language)

    def query(self, sql, params=None):
        """Run a SQL query on the catalog database (for testing/diagnostics)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql, params or ())
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()


def _dict_to_shard_info(d):
    return ShardInfo(
        repo_id=str(d.get("repo_id", "") if isinstance(d, dict) else getattr(d, "repo_id", "")),
        repo_path=str(d.get("repo_path", "") if isinstance(d, dict) else getattr(d, "repo_path", "")),
        db_path=str(d.get("db_path", "") if isinstance(d, dict) else getattr(d, "db_path", "")),
        enabled=bool(d.get("enabled", True) if isinstance(d, dict) else getattr(d, "enabled", True)),
        last_indexed_at=(d.get("last_indexed_at") if isinstance(d, dict) else getattr(d, "last_indexed_at", None)),
        symbol_count=(d.get("symbol_count") if isinstance(d, dict) else getattr(d, "symbol_count", None)),
        edge_count=(d.get("edge_count") if isinstance(d, dict) else getattr(d, "edge_count", None)),
    )


def _cross_repo_edge_to_dict(edge):
    if isinstance(edge, dict):
        return edge
    return {
        "source_repo_id": edge.source_uri.repo_id,
        "source_qualified_name": edge.source_uri.qualified_name,
        "source_file_path": edge.source_uri.file_path,
        "target_repo_id": edge.target_uri.repo_id,
        "target_qualified_name": edge.target_uri.qualified_name,
        "target_file_path": edge.target_uri.file_path,
        "relationship": edge.relationship,
        "confidence": getattr(edge, "confidence", 1.0),
        "provenance": getattr(edge, "provenance", "import_resolution"),
    }


def _dict_to_cross_repo_edge(d):
    return CrossRepoEdge(
        source_uri=GlobalSymbolURI(
            repo_id=str(d.get("source_repo_id", "")),
            qualified_name=str(d.get("source_qualified_name", "")),
            file_path=str(d.get("source_file_path", "")),
        ),
        target_uri=GlobalSymbolURI(
            repo_id=str(d.get("target_repo_id", "")),
            qualified_name=str(d.get("target_qualified_name", "")),
            file_path=str(d.get("target_file_path", "")),
        ),
        relationship=str(d.get("relationship", "")),
        confidence=float(d.get("confidence", 1.0)),
    )


__all__ = ["ShardCatalog"]
