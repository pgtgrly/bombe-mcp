"""Blast radius impact analysis backend (Rust with Python-compatible wrapper)."""

import types

from _bombe_core import get_blast_radius as _rust_get_blast_radius
from bombe.models import BlastRadiusRequest
from bombe.query._error_handling import is_not_found

_EMPTY_BLAST = {
    "target": {},
    "change_type": "behavior",
    "impact": {
        "direct_callers": [],
        "transitive_callers": [],
        "affected_files": [],
        "total_affected_symbols": 0,
        "total_affected_files": 0,
        "risk_assessment": "none",
    },
}


def get_blast_radius(db, request_or_name, change_type=None, max_depth=None):
    """Get blast radius, accepting either a BlastRadiusRequest or individual args."""
    try:
        if isinstance(request_or_name, BlastRadiusRequest):
            result = _rust_get_blast_radius(
                db,
                request_or_name.symbol_name,
                request_or_name.change_type,
                request_or_name.max_depth,
            )
        else:
            result = _rust_get_blast_radius(db, request_or_name, change_type, max_depth)
    except ValueError as exc:
        if is_not_found(exc):
            return types.SimpleNamespace(payload=dict(_EMPTY_BLAST))
        raise
    # Rust returns the response dict; wrap as .payload for Python callers.
    if isinstance(result, dict):
        return types.SimpleNamespace(payload=result)
    return result


__all__ = ["get_blast_radius"]
