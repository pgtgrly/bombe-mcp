"""Federated query executor wrapper (Rust with Python-compatible API)."""

from __future__ import annotations

import types

from _bombe_core import FederatedQueryExecutor as _RustExecutor
from bombe.models import SymbolSearchRequest


class FederatedQueryExecutor:
    """Wrapper around Rust FederatedQueryExecutor."""

    def __init__(self, catalog, router, planner):
        inner_catalog = getattr(catalog, "_inner", catalog)
        inner_router = getattr(router, "_inner", router)
        inner_planner = getattr(planner, "_inner", planner)
        self._inner = _RustExecutor(inner_catalog, inner_router, inner_planner)

    def execute_search(self, request_or_query, kind="any", file_pattern=None, limit=20):
        if isinstance(request_or_query, SymbolSearchRequest):
            result = self._inner.execute_search(
                request_or_query.query,
                request_or_query.kind,
                request_or_query.file_pattern,
                request_or_query.limit,
            )
        else:
            result = self._inner.execute_search(request_or_query, kind, file_pattern, limit)
        if isinstance(result, dict):
            return types.SimpleNamespace(**result)
        return result

    def execute_references(self, symbol_name, direction, depth, include_source=False):
        result = self._inner.execute_references(symbol_name, direction, depth, include_source)
        if isinstance(result, dict):
            return types.SimpleNamespace(**result)
        return result

    def execute_blast_radius(self, symbol_name, change_type="behavior", max_depth=3):
        result = self._inner.execute_blast_radius(symbol_name, change_type, max_depth)
        if isinstance(result, dict):
            return types.SimpleNamespace(**result)
        return result


__all__ = ["FederatedQueryExecutor"]
