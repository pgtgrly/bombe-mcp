"""MCP tool definitions and handler wiring."""

from __future__ import annotations

from typing import Any, Callable

from bombe.models import (
    BlastRadiusRequest,
    ContextRequest,
    ReferenceRequest,
    StructureRequest,
    SymbolSearchRequest,
)
from bombe.query.blast import get_blast_radius
from bombe.query.context import get_context
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
}


def _search_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = search_symbols(
        db,
        SymbolSearchRequest(
            query=str(payload["query"]),
            kind=str(payload.get("kind", "any")),
            file_pattern=payload.get("file_pattern"),
            limit=int(payload.get("limit", 20)),
        ),
    )
    return {"symbols": response.symbols, "total_matches": response.total_matches}


def _references_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = get_references(
        db,
        ReferenceRequest(
            symbol_name=str(payload["symbol_name"]),
            direction=str(payload.get("direction", "both")),
            depth=int(payload.get("depth", 1)),
            include_source=bool(payload.get("include_source", False)),
        ),
    )
    return response.payload


def _context_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = get_context(
        db,
        ContextRequest(
            query=str(payload["query"]),
            entry_points=list(payload.get("entry_points", [])),
            token_budget=int(payload.get("token_budget", 8000)),
            include_signatures_only=bool(payload.get("include_signatures_only", False)),
            expansion_depth=int(payload.get("expansion_depth", 2)),
        ),
    )
    return response.payload


def _structure_handler(db: Database, payload: dict[str, Any]) -> str:
    return get_structure(
        db,
        StructureRequest(
            path=str(payload.get("path", ".")),
            token_budget=int(payload.get("token_budget", 4000)),
            include_signatures=bool(payload.get("include_signatures", True)),
        ),
    )


def _blast_handler(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    response = get_blast_radius(
        db,
        BlastRadiusRequest(
            symbol_name=str(payload["symbol_name"]),
            change_type=str(payload.get("change_type", "behavior")),
            max_depth=int(payload.get("max_depth", 3)),
        ),
    )
    return response.payload


def build_tool_registry(db: Database, repo_root: str) -> dict[str, dict[str, Any]]:
    del repo_root
    return {
        "search_symbols": {
            "description": "Search for symbols by name, kind, and file path.",
            "input_schema": TOOL_SCHEMAS["search_symbols"],
            "handler": lambda payload: _search_handler(db, payload),
        },
        "get_references": {
            "description": "Find callers/callees for a symbol.",
            "input_schema": TOOL_SCHEMAS["get_references"],
            "handler": lambda payload: _references_handler(db, payload),
        },
        "get_context": {
            "description": "Assemble query-specific context within a token budget.",
            "input_schema": TOOL_SCHEMAS["get_context"],
            "handler": lambda payload: _context_handler(db, payload),
        },
        "get_structure": {
            "description": "Return ranked repository structure map.",
            "input_schema": TOOL_SCHEMAS["get_structure"],
            "handler": lambda payload: _structure_handler(db, payload),
        },
        "get_blast_radius": {
            "description": "Analyze impact of changing a symbol.",
            "input_schema": TOOL_SCHEMAS["get_blast_radius"],
            "handler": lambda payload: _blast_handler(db, payload),
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
