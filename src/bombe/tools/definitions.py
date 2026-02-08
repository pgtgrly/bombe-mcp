"""MCP tool definitions and handler wiring."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from bombe.models import (
    BlastRadiusRequest,
    ContextRequest,
    ReferenceRequest,
    StructureRequest,
    SymbolSearchRequest,
)
from bombe.query.guards import (
    MAX_CONTEXT_EXPANSION_DEPTH,
    MAX_CONTEXT_SEEDS,
    MAX_CONTEXT_TOKEN_BUDGET,
    MAX_FLOW_DEPTH,
    MAX_IMPACT_DEPTH,
    MAX_REFERENCE_DEPTH,
    MAX_SEARCH_LIMIT,
    MAX_STRUCTURE_TOKEN_BUDGET,
    MIN_CONTEXT_TOKEN_BUDGET,
    MIN_STRUCTURE_TOKEN_BUDGET,
    clamp_budget,
    clamp_depth,
    clamp_limit,
    truncate_query,
)
from bombe.query.blast import get_blast_radius
from bombe.query.change_impact import change_impact
from bombe.query.context import get_context
from bombe.query.data_flow import trace_data_flow
from bombe.query.references import get_references
from bombe.query.search import search_symbols
from bombe.query.structure import get_structure
from bombe.store.database import Database


ToolHandler = Callable[[dict[str, Any]], dict[str, Any] | str]

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_symbols": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["function", "class", "method", "interface", "constant", "any"],
                "default": "any",
            },
            "file_pattern": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["query"],
    },
    "get_references": {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string"},
            "direction": {
                "type": "string",
                "enum": ["callers", "callees", "both", "implementors", "supers"],
                "default": "both",
            },
            "depth": {"type": "integer", "default": 1, "minimum": 1, "maximum": 5},
            "include_source": {"type": "boolean", "default": False},
        },
        "required": ["symbol_name"],
    },
    "get_context": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "entry_points": {"type": "array", "items": {"type": "string"}},
            "token_budget": {"type": "integer", "default": 8000},
            "include_signatures_only": {"type": "boolean", "default": False},
            "expansion_depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
        },
        "required": ["query"],
    },
    "get_structure": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "token_budget": {"type": "integer", "default": 4000},
            "include_signatures": {"type": "boolean", "default": True},
        },
    },
    "get_blast_radius": {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string"},
            "change_type": {
                "type": "string",
                "enum": ["signature", "behavior", "delete"],
                "default": "behavior",
            },
            "max_depth": {"type": "integer", "default": 3},
        },
        "required": ["symbol_name"],
    },
    "trace_data_flow": {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string"},
            "direction": {
                "type": "string",
                "enum": ["upstream", "downstream", "both"],
                "default": "both",
            },
            "max_depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 6},
        },
        "required": ["symbol_name"],
    },
    "change_impact": {
        "type": "object",
        "properties": {
            "symbol_name": {"type": "string"},
            "change_type": {
                "type": "string",
                "enum": ["signature", "behavior", "delete"],
                "default": "behavior",
            },
            "max_depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 6},
        },
        "required": ["symbol_name"],
    },
}


def _result_size(payload: dict[str, Any] | str) -> int | None:
    if isinstance(payload, str):
        return len(payload)
    size = 0
    for value in payload.values():
        if isinstance(value, list):
            size += len(value)
        elif isinstance(value, dict):
            size += len(value.keys())
        elif value is not None:
            size += 1
    return size


def _safe_record_tool_metric(db: Database, **metric_kwargs: Any) -> None:
    try:
        db.record_tool_metric(**metric_kwargs)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to persist tool metric for %s: %s",
            metric_kwargs.get("tool_name", "unknown_tool"),
            str(exc),
        )


def _instrument_handler(
    db: Database,
    tool_name: str,
    handler: ToolHandler,
) -> ToolHandler:
    def wrapped(payload: dict[str, Any]) -> dict[str, Any] | str:
        started = time.perf_counter()
        try:
            result = handler(payload)
            latency_ms = (time.perf_counter() - started) * 1000.0
            _safe_record_tool_metric(
                db,
                tool_name=tool_name,
                latency_ms=latency_ms,
                success=True,
                mode="local",
                repo_id=None,
                result_size=_result_size(result),
                error_message=None,
            )
            return result
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            _safe_record_tool_metric(
                db,
                tool_name=tool_name,
                latency_ms=latency_ms,
                success=False,
                mode="local",
                repo_id=None,
                result_size=None,
                error_message=str(exc),
            )
            logging.getLogger(__name__).exception("Tool handler failed: %s", tool_name)
            raise

    return wrapped


def _search_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = search_symbols(
        db,
        SymbolSearchRequest(
            query=truncate_query(str(payload["query"])),
            kind=str(payload.get("kind", "any")),
            file_pattern=payload.get("file_pattern"),
            limit=clamp_limit(int(payload.get("limit", 20)), maximum=MAX_SEARCH_LIMIT),
        ),
    )
    return {"symbols": response.symbols, "total_matches": response.total_matches}


def _references_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = get_references(
        db,
        ReferenceRequest(
            symbol_name=truncate_query(str(payload["symbol_name"])),
            direction=str(payload.get("direction", "both")),
            depth=clamp_depth(int(payload.get("depth", 1)), maximum=MAX_REFERENCE_DEPTH),
            include_source=bool(payload.get("include_source", False)),
        ),
    )
    return response.payload


def _context_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    entry_points = list(payload.get("entry_points", []))[:MAX_CONTEXT_SEEDS]
    response = get_context(
        db,
        ContextRequest(
            query=truncate_query(str(payload["query"])),
            entry_points=entry_points,
            token_budget=clamp_budget(
                int(payload.get("token_budget", 8000)),
                minimum=MIN_CONTEXT_TOKEN_BUDGET,
                maximum=MAX_CONTEXT_TOKEN_BUDGET,
            ),
            include_signatures_only=bool(payload.get("include_signatures_only", False)),
            expansion_depth=clamp_depth(
                int(payload.get("expansion_depth", 2)),
                maximum=MAX_CONTEXT_EXPANSION_DEPTH,
            ),
        ),
    )
    return response.payload


def _structure_handler(db: Database, payload: dict[str, Any]) -> str:
    return get_structure(
        db,
        StructureRequest(
            path=str(payload.get("path", ".")),
            token_budget=clamp_budget(
                int(payload.get("token_budget", 4000)),
                minimum=MIN_STRUCTURE_TOKEN_BUDGET,
                maximum=MAX_STRUCTURE_TOKEN_BUDGET,
            ),
            include_signatures=bool(payload.get("include_signatures", True)),
        ),
    )


def _blast_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = get_blast_radius(
        db,
        BlastRadiusRequest(
            symbol_name=truncate_query(str(payload["symbol_name"])),
            change_type=str(payload.get("change_type", "behavior")),
            max_depth=clamp_depth(int(payload.get("max_depth", 3)), maximum=MAX_IMPACT_DEPTH),
        ),
    )
    return response.payload


def _data_flow_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    return trace_data_flow(
        db,
        symbol_name=truncate_query(str(payload["symbol_name"])),
        direction=str(payload.get("direction", "both")),
        max_depth=clamp_depth(int(payload.get("max_depth", 3)), maximum=MAX_FLOW_DEPTH),
    )


def _change_impact_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    return change_impact(
        db,
        symbol_name=truncate_query(str(payload["symbol_name"])),
        change_type=str(payload.get("change_type", "behavior")),
        max_depth=clamp_depth(int(payload.get("max_depth", 3)), maximum=MAX_IMPACT_DEPTH),
    )


def build_tool_registry(db: Database, repo_root: str) -> dict[str, dict[str, Any]]:
    del repo_root
    search_handler = _instrument_handler(db, "search_symbols", lambda payload: _search_handler(db, payload))
    references_handler = _instrument_handler(
        db, "get_references", lambda payload: _references_handler(db, payload)
    )
    context_handler = _instrument_handler(db, "get_context", lambda payload: _context_handler(db, payload))
    structure_handler = _instrument_handler(
        db, "get_structure", lambda payload: _structure_handler(db, payload)
    )
    blast_handler = _instrument_handler(
        db, "get_blast_radius", lambda payload: _blast_handler(db, payload)
    )
    data_flow_handler = _instrument_handler(
        db, "trace_data_flow", lambda payload: _data_flow_handler(db, payload)
    )
    impact_handler = _instrument_handler(
        db, "change_impact", lambda payload: _change_impact_handler(db, payload)
    )
    return {
        "search_symbols": {
            "description": "Search for symbols by name, kind, and file path.",
            "input_schema": TOOL_SCHEMAS["search_symbols"],
            "handler": search_handler,
        },
        "get_references": {
            "description": "Find callers/callees for a symbol.",
            "input_schema": TOOL_SCHEMAS["get_references"],
            "handler": references_handler,
        },
        "get_context": {
            "description": "Assemble query-specific context within a token budget.",
            "input_schema": TOOL_SCHEMAS["get_context"],
            "handler": context_handler,
        },
        "get_structure": {
            "description": "Return ranked repository structure map.",
            "input_schema": TOOL_SCHEMAS["get_structure"],
            "handler": structure_handler,
        },
        "get_blast_radius": {
            "description": "Analyze impact of changing a symbol.",
            "input_schema": TOOL_SCHEMAS["get_blast_radius"],
            "handler": blast_handler,
        },
        "trace_data_flow": {
            "description": "Trace upstream and downstream callgraph data flow for a symbol.",
            "input_schema": TOOL_SCHEMAS["trace_data_flow"],
            "handler": data_flow_handler,
        },
        "change_impact": {
            "description": "Estimate change impact with callgraph and type-dependency analysis.",
            "input_schema": TOOL_SCHEMAS["change_impact"],
            "handler": impact_handler,
        },
    }


def register_tools(server: Any, db: Database, repo_root: str) -> None:
    registry = build_tool_registry(db, repo_root)
    if hasattr(server, "register_tool"):
        for tool_name, tool in registry.items():
            try:
                server.register_tool(
                    tool_name, tool["description"], tool["input_schema"], tool["handler"]
                )
            except TypeError:
                server.register_tool(tool_name, tool["description"], tool["handler"])
        return
    if hasattr(server, "add_tool"):
        for tool_name, tool in registry.items():
            server.add_tool(
                name=tool_name,
                description=tool["description"],
                input_schema=tool["input_schema"],
                handler=tool["handler"],
            )
        return
    setattr(server, "bombe_tools", registry)
