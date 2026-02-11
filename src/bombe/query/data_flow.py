"""Data flow tracing backend for callgraph traversal (Rust with Python-compatible wrapper)."""

from _bombe_core import trace_data_flow as _rust_trace_data_flow
from bombe.query._error_handling import is_not_found

_EMPTY_DATA_FLOW = {
    "target": {},
    "direction": "both",
    "max_depth": 0,
    "summary": "",
    "nodes": [],
    "paths": [],
}


def trace_data_flow(db, symbol_name, direction="both", max_depth=3):
    """Trace data flow for a symbol, returning empty result on missing symbol."""
    try:
        return _rust_trace_data_flow(db, symbol_name, direction, max_depth)
    except ValueError as exc:
        if is_not_found(exc):
            return dict(_EMPTY_DATA_FLOW)
        raise


__all__ = ["trace_data_flow"]
