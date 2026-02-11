"""Context assembly backend (Rust with Python-compatible wrapper)."""

import types

from _bombe_core import get_context as _rust_get_context
from bombe.models import ContextRequest


def get_context(
    db,
    request_or_query,
    entry_points=None,
    token_budget=8000,
    include_signatures_only=False,
    expansion_depth=2,
):
    """Get context, accepting either a ContextRequest or individual args."""
    if isinstance(request_or_query, ContextRequest):
        result = _rust_get_context(
            db,
            request_or_query.query,
            request_or_query.entry_points or [],
            request_or_query.token_budget,
            request_or_query.include_signatures_only,
            request_or_query.expansion_depth,
        )
    else:
        result = _rust_get_context(
            db,
            request_or_query,
            entry_points or [],
            token_budget,
            include_signatures_only,
            expansion_depth,
        )
    # Rust returns the response dict; wrap as .payload for Python callers.
    if isinstance(result, dict):
        return types.SimpleNamespace(payload=result)
    return result


__all__ = ["get_context"]
