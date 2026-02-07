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
            "handler": lambda payload: _search_handler(db, payload),
        },
        "get_references": {
            "description": "Find callers/callees for a symbol.",
            "handler": lambda payload: _references_handler(db, payload),
        },
        "get_context": {
            "description": "Assemble query-specific context within a token budget.",
            "handler": lambda payload: _context_handler(db, payload),
        },
        "get_structure": {
            "description": "Return ranked repository structure map.",
            "handler": lambda payload: _structure_handler(db, payload),
        },
        "get_blast_radius": {
            "description": "Analyze impact of changing a symbol.",
            "handler": lambda payload: _blast_handler(db, payload),
        },
    }


def register_tools(server: Any, db: Database, repo_root: str) -> None:
    registry = build_tool_registry(db, repo_root)
    if hasattr(server, "register_tool"):
        for tool_name, tool in registry.items():
            server.register_tool(tool_name, tool["description"], tool["handler"])
        return
    setattr(server, "bombe_tools", registry)
