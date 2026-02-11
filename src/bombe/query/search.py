"""Symbol search query backend (Rust with Python-compatible wrapper)."""

import types

from _bombe_core import search_symbols as _rust_search_symbols
from bombe.models import SymbolSearchRequest


def search_symbols(db, request_or_query, kind="any", file_pattern=None, limit=20):
    """Search symbols, accepting either a SymbolSearchRequest or individual args."""
    if isinstance(request_or_query, SymbolSearchRequest):
        result = _rust_search_symbols(
            db,
            request_or_query.query,
            request_or_query.kind,
            request_or_query.file_pattern,
            request_or_query.limit,
        )
    else:
        result = _rust_search_symbols(db, request_or_query, kind, file_pattern, limit)
    # Rust returns a dict; wrap for attribute access (e.g. response.symbols).
    if isinstance(result, dict):
        return types.SimpleNamespace(**result)
    return result


__all__ = ["search_symbols"]
