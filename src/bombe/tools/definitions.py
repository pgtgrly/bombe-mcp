"""MCP tool definitions and handler wiring."""

from __future__ import annotations

import logging
import time
from pathlib import Path
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
from bombe.plugins import PluginManager
from bombe.query.planner import QueryPlanner
from bombe.query.references import get_references
from bombe.query.search import search_symbols
from bombe.query.structure import get_structure
from bombe.store.database import Database
from bombe.workspace import enabled_workspace_roots, load_workspace_config


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
            "offset": {"type": "integer", "default": 0},
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
    "get_indexing_diagnostics": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "stage": {"type": "string"},
            "severity": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
            "offset": {"type": "integer", "default": 0},
            "include_summary": {"type": "boolean", "default": True},
        },
    },
    "get_server_status": {
        "type": "object",
        "properties": {
            "diagnostics_limit": {"type": "integer", "default": 20},
            "metrics_limit": {"type": "integer", "default": 20},
        },
    },
    "estimate_context_size": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "entry_points": {"type": "array", "items": {"type": "string"}},
            "token_budget": {"type": "integer", "default": 8000},
            "expansion_depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
        },
        "required": ["query"],
    },
    "get_context_summary": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "entry_points": {"type": "array", "items": {"type": "string"}},
            "token_budget": {"type": "integer", "default": 4000},
            "expansion_depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
        },
        "required": ["query"],
    },
    "get_entry_points": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 20},
            "include_tests": {"type": "boolean", "default": False},
        },
    },
    "get_hot_paths": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 20},
            "include_tests": {"type": "boolean", "default": False},
        },
    },
    "get_orphan_symbols": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 50},
            "include_tests": {"type": "boolean", "default": False},
        },
    },
    "search_workspace_symbols": {
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
            "offset": {"type": "integer", "default": 0},
            "roots": {"type": "array", "items": {"type": "string"}},
            "include_disabled": {"type": "boolean", "default": False},
        },
        "required": ["query"],
    },
    "get_workspace_status": {
        "type": "object",
        "properties": {
            "roots": {"type": "array", "items": {"type": "string"}},
            "include_disabled": {"type": "boolean", "default": False},
            "diagnostics_limit": {"type": "integer", "default": 20},
        },
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


def _tool_metrics_summary(db: Database, limit: int = 200) -> dict[str, Any]:
    rows = db.query(
        """
        SELECT latency_ms, success, mode
        FROM tool_metrics
        ORDER BY id DESC
        LIMIT ?;
        """,
        (max(1, limit),),
    )
    latencies = sorted(float(row["latency_ms"]) for row in rows)
    total_calls = len(rows)
    success_calls = sum(1 for row in rows if int(row["success"]) == 1)
    failure_calls = total_calls - success_calls
    by_mode: dict[str, int] = {}
    for row in rows:
        mode = str(row["mode"])
        by_mode[mode] = by_mode.get(mode, 0) + 1
    p50_latency_ms = 0.0
    p95_latency_ms = 0.0
    if latencies:
        p50_index = min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.5)))
        p95_index = min(len(latencies) - 1, int(round((len(latencies) - 1) * 0.95)))
        p50_latency_ms = round(latencies[p50_index], 3)
        p95_latency_ms = round(latencies[p95_index], 3)
    return {
        "window_size": total_calls,
        "total_calls": total_calls,
        "success_calls": success_calls,
        "failure_calls": failure_calls,
        "success_rate": round(success_calls / max(1, total_calls), 4),
        "by_mode": by_mode,
        "p50_latency_ms": p50_latency_ms,
        "p95_latency_ms": p95_latency_ms,
    }


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
    plugin_manager: PluginManager | None = None,
) -> ToolHandler:
    def wrapped(payload: dict[str, Any]) -> dict[str, Any] | str:
        started = time.perf_counter()
        mode = "local"
        plan_trace: dict[str, float | str] | None = None
        effective_payload = dict(payload)
        if plugin_manager is not None:
            maybe_payload = plugin_manager.before_query(tool_name, effective_payload)
            if isinstance(maybe_payload, dict):
                effective_payload = maybe_payload
        try:
            if planner is not None:
                result, mode, plan_trace = planner.get_or_compute_with_trace(
                    tool_name=tool_name,
                    payload=effective_payload,
                    compute=lambda: handler(effective_payload),
                    version_token=_cache_version_token(db),
                )
            else:
                result = handler(effective_payload)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if bool(effective_payload.get("include_plan", False)) and isinstance(result, dict):
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
            if plugin_manager is not None:
                plugin_manager.after_query(tool_name, effective_payload, result, error=None)
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
            if plugin_manager is not None:
                plugin_manager.after_query(tool_name, effective_payload, None, error=str(exc))
            logging.getLogger(__name__).exception("Tool handler failed: %s", tool_name)
            raise

    return wrapped


def _search_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    limit = clamp_limit(int(payload.get("limit", 20)), maximum=MAX_SEARCH_LIMIT)
    offset = max(0, int(payload.get("offset", 0)))
    request_limit = clamp_limit(limit + offset, maximum=MAX_SEARCH_LIMIT)
    response = search_symbols(
        db,
        SymbolSearchRequest(
            query=truncate_query(str(payload["query"])),
            kind=str(payload.get("kind", "any")),
            file_pattern=payload.get("file_pattern"),
            limit=request_limit,
        ),
    )
    paged_symbols = response.symbols[offset: offset + limit]
    base: dict[str, Any] = {"symbols": paged_symbols, "total_matches": response.total_matches}
    if "offset" in payload or offset > 0:
        next_offset = offset + len(paged_symbols)
        base["pagination"] = {
            "offset": offset,
            "limit": limit,
            "returned": len(paged_symbols),
            "next_offset": next_offset if next_offset < int(response.total_matches) else None,
        }
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


def _indexing_diagnostics_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, int(payload.get("limit", 50)))
    offset = max(0, int(payload.get("offset", 0)))
    run_id_raw = payload.get("run_id")
    stage_raw = payload.get("stage")
    severity_raw = payload.get("severity")
    run_id = str(run_id_raw) if run_id_raw else None
    stage = str(stage_raw) if stage_raw else None
    severity = str(severity_raw) if severity_raw else None
    diagnostics = db.list_indexing_diagnostics(
        limit=limit,
        offset=offset,
        run_id=run_id,
        stage=stage,
        severity=severity,
    )
    summary = db.summarize_indexing_diagnostics(run_id=run_id)
    total = int(summary.get("total", 0))
    response = {
        "diagnostics": diagnostics,
        "count": len(diagnostics),
        "filters": {
            "run_id": run_id,
            "stage": stage,
            "severity": severity,
            "limit": limit,
            "offset": offset,
        },
    }
    if "offset" in payload or offset > 0:
        next_offset = offset + len(diagnostics)
        response["pagination"] = {
            "offset": offset,
            "limit": limit,
            "returned": len(diagnostics),
            "next_offset": next_offset if next_offset < total else None,
            "total": total,
        }
    if bool(payload.get("include_summary", True)):
        response["summary"] = summary
    return response


def _server_status_handler(
    db: Database,
    repo_root: str,
    payload: dict[str, Any],
    planner: QueryPlanner | None = None,
    plugin_manager: PluginManager | None = None,
) -> dict[str, Any]:
    diagnostics_limit = max(1, int(payload.get("diagnostics_limit", 20)))
    metrics_limit = max(1, int(payload.get("metrics_limit", 20)))
    file_rows = db.query("SELECT COUNT(*) AS count FROM files;")
    symbol_rows = db.query("SELECT COUNT(*) AS count FROM symbols;")
    edge_rows = db.query("SELECT COUNT(*) AS count FROM edges;")
    queued_rows = db.query(
        "SELECT COUNT(*) AS count FROM sync_queue WHERE status IN ('queued', 'retry');"
    )
    diagnostics_summary = db.summarize_indexing_diagnostics()
    metrics_summary = _tool_metrics_summary(db, limit=max(metrics_limit, 50))
    return {
        "repo_root": repo_root,
        "db_path": db.db_path.as_posix(),
        "counts": {
            "files": int(file_rows[0]["count"]) if file_rows else 0,
            "symbols": int(symbol_rows[0]["count"]) if symbol_rows else 0,
            "edges": int(edge_rows[0]["count"]) if edge_rows else 0,
            "sync_queue_pending": int(queued_rows[0]["count"]) if queued_rows else 0,
            "indexing_diagnostics_total": int(diagnostics_summary.get("total", 0)),
            "indexing_diagnostics_errors": int(
                diagnostics_summary.get("by_severity", {}).get("error", 0)
            ),
        },
        "indexing_diagnostics_summary": diagnostics_summary,
        "recent_indexing_diagnostics": db.list_indexing_diagnostics(limit=diagnostics_limit),
        "tool_metrics_summary": metrics_summary,
        "planner_cache": planner.stats() if planner is not None else {"entries": 0, "max_entries": 0},
        "plugin_manager": plugin_manager.stats() if plugin_manager is not None else {"plugins_loaded": 0},
        "recent_tool_metrics": db.query(
            """
            SELECT tool_name, latency_ms, success, mode, result_size, error_message, created_at
            FROM tool_metrics
            ORDER BY id DESC
            LIMIT ?;
            """,
            (metrics_limit,),
        ),
    }


def _estimate_context_size_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    entry_points = list(payload.get("entry_points", []))[:MAX_CONTEXT_SEEDS]
    token_budget = clamp_budget(
        int(payload.get("token_budget", 8000)),
        minimum=MIN_CONTEXT_TOKEN_BUDGET,
        maximum=MAX_CONTEXT_TOKEN_BUDGET,
    )
    response = get_context(
        db,
        ContextRequest(
            query=truncate_query(str(payload["query"])),
            entry_points=entry_points,
            token_budget=token_budget,
            include_signatures_only=True,
            expansion_depth=clamp_depth(
                int(payload.get("expansion_depth", 2)),
                maximum=MAX_CONTEXT_EXPANSION_DEPTH,
            ),
        ),
    )
    bundle = response.payload.get("context_bundle", {})
    estimated_tokens = int(bundle.get("tokens_used", 0)) if isinstance(bundle, dict) else 0
    symbols_estimated = int(bundle.get("symbols_included", 0)) if isinstance(bundle, dict) else 0
    return {
        "query": str(payload["query"]),
        "estimated_tokens": estimated_tokens,
        "token_budget": token_budget,
        "fits_budget": estimated_tokens <= token_budget,
        "symbols_estimated": symbols_estimated,
        "estimation_mode": "signature_only_topology",
    }


def _context_summary_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    entry_points = list(payload.get("entry_points", []))[:MAX_CONTEXT_SEEDS]
    token_budget = clamp_budget(
        int(payload.get("token_budget", 4000)),
        minimum=MIN_CONTEXT_TOKEN_BUDGET,
        maximum=MAX_CONTEXT_TOKEN_BUDGET,
    )
    response = get_context(
        db,
        ContextRequest(
            query=truncate_query(str(payload["query"])),
            entry_points=entry_points,
            token_budget=token_budget,
            include_signatures_only=True,
            expansion_depth=clamp_depth(
                int(payload.get("expansion_depth", 2)),
                maximum=MAX_CONTEXT_EXPANSION_DEPTH,
            ),
        ),
    )
    bundle = response.payload.get("context_bundle", {})
    files = bundle.get("files", []) if isinstance(bundle, dict) else []
    module_summaries: list[dict[str, Any]] = []
    for file_entry in files if isinstance(files, list) else []:
        if not isinstance(file_entry, dict):
            continue
        path = str(file_entry.get("path", ""))
        symbols = file_entry.get("symbols", [])
        if not isinstance(symbols, list):
            symbols = []
        kinds: dict[str, int] = {}
        top_symbols: list[str] = []
        for symbol in symbols:
            if not isinstance(symbol, dict):
                continue
            kind = str(symbol.get("kind", "unknown"))
            kinds[kind] = kinds.get(kind, 0) + 1
            name = str(symbol.get("name", ""))
            if name and len(top_symbols) < 8:
                top_symbols.append(name)
        module_summaries.append(
            {
                "path": path,
                "symbol_count": len(symbols),
                "kinds": kinds,
                "top_symbols": top_symbols,
            }
        )
    module_summaries.sort(key=lambda item: item["path"])
    return {
        "query": str(payload["query"]),
        "summary": bundle.get("summary", "") if isinstance(bundle, dict) else "",
        "selection_strategy": (
            bundle.get("selection_strategy", "seeded_topology_then_rank")
            if isinstance(bundle, dict)
            else "seeded_topology_then_rank"
        ),
        "relationship_map": bundle.get("relationship_map", "") if isinstance(bundle, dict) else "",
        "module_summaries": module_summaries,
        "tokens_used": int(bundle.get("tokens_used", 0)) if isinstance(bundle, dict) else 0,
        "token_budget": token_budget,
        "symbols_included": int(bundle.get("symbols_included", 0)) if isinstance(bundle, dict) else 0,
    }


def _symbol_graph_rows(db: Database) -> list[dict[str, Any]]:
    return db.query(
        """
        SELECT
            s.id,
            s.name,
            s.qualified_name,
            s.kind,
            s.file_path,
            s.start_line,
            s.end_line,
            s.pagerank_score,
            (
                SELECT COUNT(*)
                FROM edges e
                WHERE e.target_type = 'symbol' AND e.target_id = s.id
            ) AS inbound_count,
            (
                SELECT COUNT(*)
                FROM edges e
                WHERE e.source_type = 'symbol' AND e.source_id = s.id
            ) AS outbound_count
        FROM symbols s
        ORDER BY s.qualified_name ASC;
        """
    )


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    return "/test" in lowered or "\\test" in lowered or lowered.endswith("_test.py")


def _entry_point_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    limit = clamp_limit(int(payload.get("limit", 20)), maximum=MAX_SEARCH_LIMIT)
    include_tests = bool(payload.get("include_tests", False))
    rows = _symbol_graph_rows(db)
    scored: list[dict[str, Any]] = []
    for row in rows:
        file_path = str(row["file_path"])
        if not include_tests and _is_test_path(file_path):
            continue
        file_name = Path(file_path).name.lower()
        role_bonus = 0.0
        if file_name.startswith(("main", "app", "server", "cli", "index")):
            role_bonus = 0.35
        inbound = int(row["inbound_count"] or 0)
        outbound = int(row["outbound_count"] or 0)
        pagerank = float(row["pagerank_score"] or 0.0)
        score = round((pagerank * 0.6) + (inbound * 0.25) + (outbound * 0.1) + role_bonus, 6)
        scored.append(
            {
                "name": str(row["name"]),
                "qualified_name": str(row["qualified_name"]),
                "kind": str(row["kind"]),
                "file_path": file_path,
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "pagerank_score": pagerank,
                "inbound_count": inbound,
                "outbound_count": outbound,
                "entry_score": score,
            }
        )
    scored.sort(
        key=lambda row: (-float(row["entry_score"]), str(row["qualified_name"]), str(row["file_path"]))
    )
    return {"entry_points": scored[:limit], "total_candidates": len(scored)}


def _hot_paths_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    limit = clamp_limit(int(payload.get("limit", 20)), maximum=MAX_SEARCH_LIMIT)
    include_tests = bool(payload.get("include_tests", False))
    rows = _symbol_graph_rows(db)
    scored: list[dict[str, Any]] = []
    for row in rows:
        file_path = str(row["file_path"])
        if not include_tests and _is_test_path(file_path):
            continue
        inbound = int(row["inbound_count"] or 0)
        outbound = int(row["outbound_count"] or 0)
        pagerank = float(row["pagerank_score"] or 0.0)
        hot_score = round((inbound * 2.0) + (outbound * 1.0) + (pagerank * 10.0), 6)
        scored.append(
            {
                "name": str(row["name"]),
                "qualified_name": str(row["qualified_name"]),
                "kind": str(row["kind"]),
                "file_path": file_path,
                "inbound_count": inbound,
                "outbound_count": outbound,
                "pagerank_score": pagerank,
                "hot_score": hot_score,
            }
        )
    scored.sort(key=lambda row: (-float(row["hot_score"]), str(row["qualified_name"])))
    return {"hot_paths": scored[:limit], "total_candidates": len(scored)}


def _orphan_symbols_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, int(payload.get("limit", 50)))
    include_tests = bool(payload.get("include_tests", False))
    rows = _symbol_graph_rows(db)
    orphaned: list[dict[str, Any]] = []
    for row in rows:
        file_path = str(row["file_path"])
        if not include_tests and _is_test_path(file_path):
            continue
        inbound = int(row["inbound_count"] or 0)
        if inbound != 0:
            continue
        orphaned.append(
            {
                "name": str(row["name"]),
                "qualified_name": str(row["qualified_name"]),
                "kind": str(row["kind"]),
                "file_path": file_path,
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "reason": "no_inbound_edges",
            }
        )
    orphaned.sort(key=lambda row: (str(row["file_path"]), str(row["qualified_name"])))
    return {"orphan_symbols": orphaned[:limit], "total_orphans": len(orphaned)}


def _resolve_workspace_roots(repo_root: str, payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    config = load_workspace_config(Path(repo_root))
    include_disabled = bool(payload.get("include_disabled", False))
    selected = config.roots if include_disabled else enabled_workspace_roots(config)
    requested_roots_raw = payload.get("roots", [])
    requested_roots = {
        str(item).strip()
        for item in requested_roots_raw
        if isinstance(item, str) and str(item).strip()
    }
    roots: list[dict[str, Any]] = []
    for root in selected:
        if requested_roots and root.id not in requested_roots and root.path not in requested_roots:
            continue
        roots.append(
            {
                "id": root.id,
                "path": root.path,
                "db_path": root.db_path,
                "enabled": bool(root.enabled),
            }
        )
    return config.name, roots


def _workspace_search_handler(db: Database, repo_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    del db
    limit = clamp_limit(int(payload.get("limit", 20)), maximum=MAX_SEARCH_LIMIT)
    offset = max(0, int(payload.get("offset", 0)))
    request_limit = clamp_limit(limit + offset, maximum=MAX_SEARCH_LIMIT)
    workspace_name, roots = _resolve_workspace_roots(repo_root, payload)
    results: list[dict[str, Any]] = []
    root_errors: list[dict[str, Any]] = []
    for root in roots:
        root_path = Path(str(root["path"]))
        db_path = Path(str(root["db_path"]))
        if not root_path.exists() or not root_path.is_dir():
            root_errors.append(
                {
                    "root_id": str(root["id"]),
                    "root_path": str(root["path"]),
                    "error": "missing_root",
                }
            )
            continue
        try:
            root_db = Database(db_path)
            root_db.init_schema()
            response = search_symbols(
                root_db,
                SymbolSearchRequest(
                    query=truncate_query(str(payload["query"])),
                    kind=str(payload.get("kind", "any")),
                    file_pattern=payload.get("file_pattern"),
                    limit=request_limit,
                ),
            )
        except Exception as exc:
            root_errors.append(
                {
                    "root_id": str(root["id"]),
                    "root_path": str(root["path"]),
                    "error": str(exc),
                }
            )
            continue
        for symbol in response.symbols:
            if not isinstance(symbol, dict):
                continue
            results.append(
                {
                    **symbol,
                    "workspace_root_id": str(root["id"]),
                    "workspace_root_path": str(root["path"]),
                }
            )
    results.sort(
        key=lambda item: (
            -float(item.get("importance_score", 0.0)),
            str(item.get("qualified_name", "")),
            str(item.get("workspace_root_path", "")),
        )
    )
    paged = results[offset: offset + limit]
    payload_out: dict[str, Any] = {
        "workspace_name": workspace_name,
        "symbols": paged,
        "total_matches": len(results),
        "roots_considered": len(roots),
        "root_errors": root_errors,
    }
    if "offset" in payload or offset > 0:
        next_offset = offset + len(paged)
        payload_out["pagination"] = {
            "offset": offset,
            "limit": limit,
            "returned": len(paged),
            "next_offset": next_offset if next_offset < len(results) else None,
        }
    return payload_out


def _workspace_status_handler(db: Database, repo_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    del db
    diagnostics_limit = max(1, int(payload.get("diagnostics_limit", 20)))
    workspace_name, roots = _resolve_workspace_roots(repo_root, payload)
    statuses: list[dict[str, Any]] = []
    totals = {
        "roots": 0,
        "roots_ok": 0,
        "files": 0,
        "symbols": 0,
        "edges": 0,
        "indexing_diagnostics_errors": 0,
    }
    for root in roots:
        root_path = Path(str(root["path"]))
        db_path = Path(str(root["db_path"]))
        status_item: dict[str, Any] = {
            "root_id": str(root["id"]),
            "root_path": str(root["path"]),
            "db_path": str(root["db_path"]),
            "enabled": bool(root["enabled"]),
        }
        totals["roots"] += 1
        if not root_path.exists() or not root_path.is_dir():
            status_item["status"] = "missing_root"
            statuses.append(status_item)
            continue
        try:
            root_db = Database(db_path)
            root_db.init_schema()
            file_rows = root_db.query("SELECT COUNT(*) AS count FROM files;")
            symbol_rows = root_db.query("SELECT COUNT(*) AS count FROM symbols;")
            edge_rows = root_db.query("SELECT COUNT(*) AS count FROM edges;")
            diagnostics = root_db.summarize_indexing_diagnostics()
            status_item["status"] = "ok"
            status_item["counts"] = {
                "files": int(file_rows[0]["count"]) if file_rows else 0,
                "symbols": int(symbol_rows[0]["count"]) if symbol_rows else 0,
                "edges": int(edge_rows[0]["count"]) if edge_rows else 0,
                "indexing_diagnostics_errors": int(
                    diagnostics.get("by_severity", {}).get("error", 0)
                ),
            }
            status_item["recent_indexing_diagnostics"] = root_db.list_indexing_diagnostics(
                limit=diagnostics_limit
            )
            totals["roots_ok"] += 1
            totals["files"] += int(status_item["counts"]["files"])
            totals["symbols"] += int(status_item["counts"]["symbols"])
            totals["edges"] += int(status_item["counts"]["edges"])
            totals["indexing_diagnostics_errors"] += int(
                status_item["counts"]["indexing_diagnostics_errors"]
            )
        except Exception as exc:
            status_item["status"] = "error"
            status_item["error"] = str(exc)
        statuses.append(status_item)
    return {
        "workspace_name": workspace_name,
        "roots": statuses,
        "totals": totals,
    }


def build_tool_registry(
    db: Database,
    repo_root: str,
    plugin_manager: PluginManager | None = None,
) -> dict[str, dict[str, Any]]:
    planner = QueryPlanner(max_entries=1024, ttl_seconds=30.0)
    search_handler = _instrument_handler(
        db,
        "search_symbols",
        lambda payload: _search_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    references_handler = _instrument_handler(
        db,
        "get_references",
        lambda payload: _references_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    context_handler = _instrument_handler(
        db,
        "get_context",
        lambda payload: _context_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    structure_handler = _instrument_handler(
        db,
        "get_structure",
        lambda payload: _structure_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    blast_handler = _instrument_handler(
        db,
        "get_blast_radius",
        lambda payload: _blast_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    data_flow_handler = _instrument_handler(
        db,
        "trace_data_flow",
        lambda payload: _data_flow_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    impact_handler = _instrument_handler(
        db,
        "change_impact",
        lambda payload: _change_impact_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    diagnostics_handler = _instrument_handler(
        db,
        "get_indexing_diagnostics",
        lambda payload: _indexing_diagnostics_handler(db, payload),
        planner=None,
        plugin_manager=plugin_manager,
    )
    status_handler = _instrument_handler(
        db,
        "get_server_status",
        lambda payload: _server_status_handler(
            db,
            repo_root,
            payload,
            planner=planner,
            plugin_manager=plugin_manager,
        ),
        planner=None,
        plugin_manager=plugin_manager,
    )
    estimate_context_handler = _instrument_handler(
        db,
        "estimate_context_size",
        lambda payload: _estimate_context_size_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    context_summary_handler = _instrument_handler(
        db,
        "get_context_summary",
        lambda payload: _context_summary_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    entry_points_handler = _instrument_handler(
        db,
        "get_entry_points",
        lambda payload: _entry_point_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    hot_paths_handler = _instrument_handler(
        db,
        "get_hot_paths",
        lambda payload: _hot_paths_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    orphan_symbols_handler = _instrument_handler(
        db,
        "get_orphan_symbols",
        lambda payload: _orphan_symbols_handler(db, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    workspace_search_handler = _instrument_handler(
        db,
        "search_workspace_symbols",
        lambda payload: _workspace_search_handler(db, repo_root, payload),
        planner=planner,
        plugin_manager=plugin_manager,
    )
    workspace_status_handler = _instrument_handler(
        db,
        "get_workspace_status",
        lambda payload: _workspace_status_handler(db, repo_root, payload),
        planner=None,
        plugin_manager=plugin_manager,
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
        "get_indexing_diagnostics": {
            "description": "List parse and indexing diagnostics with optional run/stage filters.",
            "input_schema": TOOL_SCHEMAS["get_indexing_diagnostics"],
            "handler": diagnostics_handler,
        },
        "get_server_status": {
            "description": "Return local index health, counters, and recent diagnostics.",
            "input_schema": TOOL_SCHEMAS["get_server_status"],
            "handler": status_handler,
        },
        "estimate_context_size": {
            "description": "Estimate context token usage before fetching full context payloads.",
            "input_schema": TOOL_SCHEMAS["estimate_context_size"],
            "handler": estimate_context_handler,
        },
        "get_context_summary": {
            "description": "Return module-level context summaries for a query.",
            "input_schema": TOOL_SCHEMAS["get_context_summary"],
            "handler": context_summary_handler,
        },
        "get_entry_points": {
            "description": "Return likely entry points for navigating the repository graph.",
            "input_schema": TOOL_SCHEMAS["get_entry_points"],
            "handler": entry_points_handler,
        },
        "get_hot_paths": {
            "description": "Return symbols with the highest callgraph traffic and centrality.",
            "input_schema": TOOL_SCHEMAS["get_hot_paths"],
            "handler": hot_paths_handler,
        },
        "get_orphan_symbols": {
            "description": "Return symbols with no inbound references.",
            "input_schema": TOOL_SCHEMAS["get_orphan_symbols"],
            "handler": orphan_symbols_handler,
        },
        "search_workspace_symbols": {
            "description": "Search symbols across all configured workspace roots.",
            "input_schema": TOOL_SCHEMAS["search_workspace_symbols"],
            "handler": workspace_search_handler,
        },
        "get_workspace_status": {
            "description": "Return aggregated status across configured workspace roots.",
            "input_schema": TOOL_SCHEMAS["get_workspace_status"],
            "handler": workspace_status_handler,
        },
    }


def register_tools(
    server: Any,
    db: Database,
    repo_root: str,
    plugin_manager: PluginManager | None = None,
) -> None:
    registry = build_tool_registry(db, repo_root, plugin_manager=plugin_manager)
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
