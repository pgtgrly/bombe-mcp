"""Change impact analysis backend with graph-aware dependents (Rust with Python-compatible wrapper)."""

from _bombe_core import change_impact as _rust_change_impact
from bombe.query._error_handling import is_not_found

_EMPTY_CHANGE_IMPACT = {
    "target": {},
    "change_type": "behavior",
    "max_depth": 0,
    "summary": "",
    "impact": {
        "direct_callers": [],
        "transitive_callers": [],
        "type_dependents": [],
        "affected_files": [],
        "total_affected_symbols": 0,
        "risk_level": "none",
    },
}


def change_impact(db, symbol_name, change_type="behavior", max_depth=3):
    """Analyze change impact for a symbol, returning empty result on missing symbol."""
    try:
        return _rust_change_impact(db, symbol_name, change_type, max_depth)
    except ValueError as exc:
        if is_not_found(exc):
            return dict(_EMPTY_CHANGE_IMPACT)
        raise


__all__ = ["change_impact"]
