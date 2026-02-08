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
from bombe.query.planner import QueryPlanner
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
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
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
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
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
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
        },
        "required": ["query"],
    },
    "get_structure": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "token_budget": {"type": "integer", "default": 4000},
            "include_signatures": {"type": "boolean", "default": True},
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
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
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
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
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
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
            "include_explanations": {"type": "boolean", "default": False},
            "include_plan": {"type": "boolean", "default": False},
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


def _cache_version_token(db: Database) -> str:
    try:
        return str(db.get_cache_epoch())
    except Exception:
        return "1"


def _with_explanations(
    tool_name: str,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    if not bool(payload.get("include_explanations", False)):
        return response
    explanation: dict[str, Any]
    if tool_name == "search_symbols":
        symbols = response.get("symbols", [])
        strategy_counts: dict[str, int] = {}
        for symbol in symbols if isinstance(symbols, list) else []:
            if not isinstance(symbol, dict):
                continue
            strategy = str(symbol.get("match_strategy", "unknown"))
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
        explanation = {
            "query": str(payload.get("query", "")),
            "total_matches": int(response.get("total_matches", 0)),
            "match_strategies": strategy_counts,
        }
    elif tool_name == "get_references":
        explanation = {
            "symbol": str(payload.get("symbol_name", "")),
            "direction": str(payload.get("direction", "both")),
            "depth": int(payload.get("depth", 1)),
            "counts": {
                "callers": len(response.get("callers", [])),
                "callees": len(response.get("callees", [])),
                "implementors": len(response.get("implementors", [])),
                "supers": len(response.get("supers", [])),
            },
        }
    elif tool_name == "get_context":
        bundle = response.get("context_bundle", {})
        metrics = bundle.get("quality_metrics", {}) if isinstance(bundle, dict) else {}
        explanation = {
            "query": str(response.get("query", "")),
            "selection_strategy": (
                str(bundle.get("selection_strategy", "unknown"))
                if isinstance(bundle, dict)
                else "unknown"
            ),
            "quality_metrics": metrics,
        }
    elif tool_name == "get_blast_radius":
        impact = response.get("impact", {})
        explanation = {
            "symbol": str(response.get("target", {}).get("name", "")) if isinstance(response.get("target"), dict) else "",
            "change_type": str(response.get("change_type", "")),
            "total_affected_symbols": int(impact.get("total_affected_symbols", 0)) if isinstance(impact, dict) else 0,
            "total_affected_files": int(impact.get("total_affected_files", 0)) if isinstance(impact, dict) else 0,
        }
    elif tool_name == "trace_data_flow":
        explanation = {
            "direction": str(response.get("direction", "both")),
            "max_depth": int(response.get("max_depth", 0)),
            "node_count": len(response.get("nodes", [])),
            "path_count": len(response.get("paths", [])),
        }
    else:
        impact = response.get("impact", {})
        explanation = {
            "change_type": str(response.get("change_type", "behavior")),
            "max_depth": int(response.get("max_depth", 0)),
            "risk_level": str(impact.get("risk_level", "")) if isinstance(impact, dict) else "",
            "total_affected_symbols": int(impact.get("total_affected_symbols", 0)) if isinstance(impact, dict) else 0,
        }
    return {**response, "explanations": explanation}


def _instrument_handler(
    db: Database,
    tool_name: str,
    handler: ToolHandler,
    planner: QueryPlanner | None = None,
) -> ToolHandler:
    def wrapped(payload: dict[str, Any]) -> dict[str, Any] | str:
        started = time.perf_counter()
        mode = "local"
        plan_trace: dict[str, float | str] | None = None
        try:
            if planner is not None:
                result, mode, plan_trace = planner.get_or_compute_with_trace(
                    tool_name=tool_name,
                    payload=payload,
                    compute=lambda: handler(payload),
                    version_token=_cache_version_token(db),
                )
            else:
                result = handler(payload)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if bool(payload.get("include_plan", False)) and isinstance(result, dict):
                trace_payload: dict[str, float | str] = {
                    "cache_mode": mode,
                }
                if plan_trace is not None:
                    trace_payload.update(plan_trace)
                result = {**result, "planner_trace": trace_payload}
            _safe_record_tool_metric(
                db,
                tool_name=tool_name,
                latency_ms=latency_ms,
                success=True,
                mode=mode,
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
                mode=mode,
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
    base = {"symbols": response.symbols, "total_matches": response.total_matches}
    return _with_explanations("search_symbols", payload, base)


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
    return _with_explanations("get_references", payload, response.payload)


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
    return _with_explanations("get_context", payload, response.payload)


def _structure_handler(db: Database, payload: dict[str, Any]) -> str:
    structure = get_structure(
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
    if not bool(payload.get("include_explanations", False)):
        return structure
    line_count = len(structure.splitlines()) if structure else 0
    prefix = (
        f"# structure_explanations path={payload.get('path', '.')} "
        f"lines={line_count} token_budget={payload.get('token_budget', 4000)}"
    )
    return f"{prefix}\n{structure}" if structure else prefix


def _blast_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = get_blast_radius(
        db,
        BlastRadiusRequest(
            symbol_name=truncate_query(str(payload["symbol_name"])),
            change_type=str(payload.get("change_type", "behavior")),
            max_depth=clamp_depth(int(payload.get("max_depth", 3)), maximum=MAX_IMPACT_DEPTH),
        ),
    )
    return _with_explanations("get_blast_radius", payload, response.payload)


def _data_flow_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    base = trace_data_flow(
        db,
        symbol_name=truncate_query(str(payload["symbol_name"])),
        direction=str(payload.get("direction", "both")),
        max_depth=clamp_depth(int(payload.get("max_depth", 3)), maximum=MAX_FLOW_DEPTH),
    )
    return _with_explanations("trace_data_flow", payload, base)


def _change_impact_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    base = change_impact(
        db,
        symbol_name=truncate_query(str(payload["symbol_name"])),
        change_type=str(payload.get("change_type", "behavior")),
        max_depth=clamp_depth(int(payload.get("max_depth", 3)), maximum=MAX_IMPACT_DEPTH),
    )
    return _with_explanations("change_impact", payload, base)


def build_tool_registry(db: Database, repo_root: str) -> dict[str, dict[str, Any]]:
    del repo_root
    planner = QueryPlanner(max_entries=1024, ttl_seconds=30.0)
    search_handler = _instrument_handler(
        db,
        "search_symbols",
        lambda payload: _search_handler(db, payload),
        planner=planner,
    )
    references_handler = _instrument_handler(
        db,
        "get_references",
        lambda payload: _references_handler(db, payload),
        planner=planner,
    )
    context_handler = _instrument_handler(
        db,
        "get_context",
        lambda payload: _context_handler(db, payload),
        planner=planner,
    )
    structure_handler = _instrument_handler(
        db,
        "get_structure",
        lambda payload: _structure_handler(db, payload),
        planner=planner,
    )
    blast_handler = _instrument_handler(
        db,
        "get_blast_radius",
        lambda payload: _blast_handler(db, payload),
        planner=planner,
    )
    data_flow_handler = _instrument_handler(
        db,
        "trace_data_flow",
        lambda payload: _data_flow_handler(db, payload),
        planner=planner,
    )
    impact_handler = _instrument_handler(
        db,
        "change_impact",
        lambda payload: _change_impact_handler(db, payload),
        planner=planner,
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
