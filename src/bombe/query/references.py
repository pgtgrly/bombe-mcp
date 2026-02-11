"""Reference traversal backend (Rust with Python-compatible wrapper)."""

import types

from _bombe_core import get_references as _rust_get_references
from bombe.models import ReferenceRequest
from bombe.query._error_handling import is_not_found

_EMPTY_RESPONSE = {
    "target_symbol": None,
    "callers": [],
    "callees": [],
    "implementors": [],
    "supers": [],
}


def _transform_ref_item(item):
    """Transform Rust reference item to Python API format."""
    if not isinstance(item, dict):
        return item
    return {
        "name": item.get("name", ""),
        "file_path": item.get("file_path", ""),
        "line": item.get("line_number", 0),
        "depth": item.get("depth", 1),
        "reference_reason": item.get("relationship", ""),
    }


def _transform_response(result):
    """Transform Rust reference response dict to Python API format."""
    if not isinstance(result, dict):
        return result
    for key in ("callers", "callees", "implementors", "supers"):
        if key in result and isinstance(result[key], list):
            result[key] = [_transform_ref_item(item) for item in result[key]]
    return result


def get_references(db, request_or_name, direction="both", depth=1, include_source=False):
    """Get references, accepting either a ReferenceRequest or individual args."""
    try:
        if isinstance(request_or_name, ReferenceRequest):
            result = _rust_get_references(
                db,
                request_or_name.symbol_name,
                request_or_name.direction,
                request_or_name.depth,
                request_or_name.include_source,
            )
        else:
            result = _rust_get_references(db, request_or_name, direction, depth, include_source)
    except ValueError as exc:
        if is_not_found(exc):
            return types.SimpleNamespace(payload=dict(_EMPTY_RESPONSE))
        raise
    # Rust returns the response dict; transform field names and wrap as .payload.
    if isinstance(result, dict):
        return types.SimpleNamespace(payload=_transform_response(result))
    return result


__all__ = ["get_references"]
