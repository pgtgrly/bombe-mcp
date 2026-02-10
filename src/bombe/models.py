"""Shared typed models used across indexing, storage, and query layers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DELTA_SCHEMA_VERSION = 1
ARTIFACT_SCHEMA_VERSION = 1
MCP_CONTRACT_VERSION = 1


def _signature_hash(signature: str | None) -> str:
    return hashlib.sha256((signature or "").encode("utf-8")).hexdigest()


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
class SymbolKey:
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature_hash: str

    @classmethod
    def from_symbol(cls, symbol: SymbolRecord) -> "SymbolKey":
        return cls.from_fields(
            qualified_name=symbol.qualified_name,
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            signature=symbol.signature,
        )

    @classmethod
    def from_fields(
        cls,
        qualified_name: str,
        file_path: str,
        start_line: int,
        end_line: int,
        signature: str | None,
    ) -> "SymbolKey":
        return cls(
            qualified_name=qualified_name,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            signature_hash=_signature_hash(signature),
        )

    def as_tuple(self) -> tuple[str, str, int, int, str]:
        return (
            self.qualified_name,
            self.file_path,
            self.start_line,
            self.end_line,
            self.signature_hash,
        )


@dataclass(frozen=True)
class EdgeKey:
    source: SymbolKey
    target: SymbolKey
    relationship: str
    line_number: int

    def as_tuple(
        self,
    ) -> tuple[tuple[str, str, int, int, str], tuple[str, str, int, int, str], str, int]:
        return (
            self.source.as_tuple(),
            self.target.as_tuple(),
            self.relationship,
            self.line_number,
        )


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
class EdgeContractRecord:
    source: SymbolKey
    target: SymbolKey
    relationship: str
    line_number: int
    confidence: float = 1.0
    provenance: str = "local"

    def key(self) -> EdgeKey:
        return EdgeKey(
            source=self.source,
            target=self.target,
            relationship=self.relationship,
            line_number=self.line_number,
        )

    def as_tuple(
        self,
    ) -> tuple[tuple[str, str, int, int, str], tuple[str, str, int, int, str], str, int]:
        return self.key().as_tuple()


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
class WorkspaceRoot:
    id: str
    path: str
    db_path: str
    enabled: bool = True


@dataclass(frozen=True)
class WorkspaceConfig:
    name: str
    version: int
    roots: list[WorkspaceRoot] = field(default_factory=list)


@dataclass(frozen=True)
class FileDelta:
    status: str
    path: str
    old_path: str | None = None
    content_hash: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class DeltaHeader:
    repo_id: str
    parent_snapshot: str | None
    local_snapshot: str
    tool_version: str
    schema_version: int
    created_at_utc: str


@dataclass(frozen=True)
class QualityStats:
    ambiguity_rate: float = 0.0
    unresolved_imports: int = 0
    parse_failures: int = 0


@dataclass(frozen=True)
class IndexDelta:
    header: DeltaHeader
    file_changes: list[FileDelta] = field(default_factory=list)
    symbol_upserts: list[SymbolRecord] = field(default_factory=list)
    symbol_deletes: list[SymbolKey] = field(default_factory=list)
    edge_upserts: list[EdgeContractRecord] = field(default_factory=list)
    edge_deletes: list[EdgeContractRecord] = field(default_factory=list)
    quality_stats: QualityStats = field(default_factory=QualityStats)


@dataclass(frozen=True)
class ArtifactBundle:
    artifact_id: str
    repo_id: str
    snapshot_id: str
    parent_snapshot: str | None
    tool_version: str
    schema_version: int
    created_at_utc: str
    promoted_symbols: list[SymbolKey] = field(default_factory=list)
    promoted_edges: list[EdgeContractRecord] = field(default_factory=list)
    impact_priors: list[dict[str, Any]] = field(default_factory=list)
    flow_hints: list[dict[str, Any]] = field(default_factory=list)
    signature_algo: str | None = None
    signing_key_id: str | None = None
    checksum: str | None = None
    signature: str | None = None


@dataclass(frozen=True)
class IndexStats:
    files_seen: int
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    elapsed_ms: int
    run_id: str | None = None
    diagnostics_summary: dict[str, Any] = field(default_factory=dict)
    indexing_telemetry: dict[str, Any] = field(default_factory=dict)
    progress_snapshots: list[dict[str, Any]] = field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Phase 15: Cross-repo graphing and sharding models
# ---------------------------------------------------------------------------


def _repo_id_from_path(canonical_path: str) -> str:
    return hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class GlobalSymbolURI:
    """Globally unique symbol identifier across repositories."""

    repo_id: str
    qualified_name: str
    file_path: str

    @property
    def uri(self) -> str:
        return f"bombe://{self.repo_id}/{self.qualified_name}#{self.file_path}"

    @classmethod
    def from_uri(cls, uri: str) -> "GlobalSymbolURI":
        if not uri.startswith("bombe://"):
            raise ValueError(f"Invalid GlobalSymbolURI: {uri}")
        rest = uri[len("bombe://"):]
        slash_idx = rest.index("/")
        repo_id = rest[:slash_idx]
        remainder = rest[slash_idx + 1:]
        hash_idx = remainder.index("#")
        qualified_name = remainder[:hash_idx]
        file_path = remainder[hash_idx + 1:]
        return cls(repo_id=repo_id, qualified_name=qualified_name, file_path=file_path)

    @classmethod
    def from_symbol(cls, repo_id: str, symbol: SymbolRecord) -> "GlobalSymbolURI":
        return cls(
            repo_id=repo_id,
            qualified_name=symbol.qualified_name,
            file_path=symbol.file_path,
        )


@dataclass(frozen=True)
class ShardInfo:
    """Metadata about a single shard (repo database) in a shard group."""

    repo_id: str
    repo_path: str
    db_path: str
    enabled: bool = True
    last_indexed_at: str | None = None
    symbol_count: int = 0
    edge_count: int = 0


@dataclass(frozen=True)
class CrossRepoEdge:
    """An edge between symbols in different repositories."""

    source_uri: GlobalSymbolURI
    target_uri: GlobalSymbolURI
    relationship: str
    confidence: float = 1.0
    provenance: str = "import_resolution"


@dataclass(frozen=True)
class ShardGroupConfig:
    """Configuration for a group of repos that may reference each other."""

    name: str
    catalog_db_path: str
    shards: list[ShardInfo] = field(default_factory=list)
    version: int = 1


@dataclass(frozen=True)
class FederatedQueryResult:
    """Result from a query that spans multiple shards."""

    results: list[dict[str, Any]] = field(default_factory=list)
    shard_reports: list[dict[str, Any]] = field(default_factory=list)
    total_matches: int = 0
    shards_queried: int = 0
    shards_failed: int = 0
    elapsed_ms: int = 0
