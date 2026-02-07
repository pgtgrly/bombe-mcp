"""Shared typed models used across indexing, storage, and query layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileRecord:
    path: str
    language: str
    content_hash: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class ParameterRecord:
    name: str
    position: int
    type: str | None = None
    default_value: str | None = None


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    return_type: str | None = None
    visibility: str | None = None
    is_async: bool = False
    is_static: bool = False
    parent_symbol_id: int | None = None
    docstring: str | None = None
    pagerank_score: float = 0.0
    parameters: list[ParameterRecord] = field(default_factory=list)


@dataclass(frozen=True)
class EdgeRecord:
    source_id: int
    target_id: int
    source_type: str
    target_type: str
    relationship: str
    file_path: str | None = None
    line_number: int | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class ExternalDepRecord:
    file_path: str
    import_statement: str
    module_name: str
    line_number: int | None = None


@dataclass(frozen=True)
class ImportRecord:
    source_file_path: str
    import_statement: str
    module_name: str
    imported_names: list[str] = field(default_factory=list)
    line_number: int | None = None


@dataclass(frozen=True)
class ParsedUnit:
    path: Path
    language: str
    source: str
    tree: Any


@dataclass(frozen=True)
class FileChange:
    status: str
    path: str
    old_path: str | None = None


@dataclass(frozen=True)
class IndexStats:
    files_seen: int
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    elapsed_ms: int


@dataclass(frozen=True)
class SymbolSearchRequest:
    query: str
    kind: str = "any"
    file_pattern: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class ReferenceRequest:
    symbol_name: str
    direction: str = "both"
    depth: int = 1
    include_source: bool = False


@dataclass(frozen=True)
class ContextRequest:
    query: str
    entry_points: list[str] = field(default_factory=list)
    token_budget: int = 8000
    include_signatures_only: bool = False
    expansion_depth: int = 2


@dataclass(frozen=True)
class StructureRequest:
    path: str = "."
    token_budget: int = 4000
    include_signatures: bool = True


@dataclass(frozen=True)
class BlastRadiusRequest:
    symbol_name: str
    change_type: str = "behavior"
    max_depth: int = 3


@dataclass(frozen=True)
class SymbolSearchResponse:
    symbols: list[dict[str, Any]]
    total_matches: int


@dataclass(frozen=True)
class ReferenceResponse:
    payload: dict[str, Any]


@dataclass(frozen=True)
class ContextResponse:
    payload: dict[str, Any]


@dataclass(frozen=True)
class BlastRadiusResponse:
    payload: dict[str, Any]
