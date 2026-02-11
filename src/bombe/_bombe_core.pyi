"""Type stubs for the ``_bombe_core`` Rust extension module.

This file is auto-generated to match the PyO3 ``#[pymodule]`` registration in
``crates/bombe-core/src/lib.rs`` and every ``#[pyclass]`` / ``#[pyfunction]``
reachable from it.
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Module-level constants (models)
# ---------------------------------------------------------------------------

DELTA_SCHEMA_VERSION: int
ARTIFACT_SCHEMA_VERSION: int
MCP_CONTRACT_VERSION: int

# ---------------------------------------------------------------------------
# Module-level constants (query guards)
# ---------------------------------------------------------------------------

MAX_QUERY_LENGTH: int
MAX_SEARCH_LIMIT: int
MAX_REFERENCE_DEPTH: int
MAX_CONTEXT_EXPANSION_DEPTH: int
MAX_CONTEXT_SEEDS: int
MAX_CONTEXT_TOKEN_BUDGET: int
MIN_CONTEXT_TOKEN_BUDGET: int
MAX_GRAPH_VISITED: int
MAX_GRAPH_EDGES: int
MAX_BLAST_DEPTH: int
MAX_ENTRY_POINTS: int
MAX_FEDERATED_RESULTS: int
MAX_SHARDS_PER_QUERY: int
MAX_CROSS_REPO_EDGES_PER_QUERY: int

# ===========================================================================
# Model helper functions
# ===========================================================================

def _signature_hash(signature: str | None = None) -> str:
    """Compute a SHA-256 hex digest of the given signature string (or \"\" if None)."""
    ...

def _repo_id_from_path(canonical_path: str) -> str:
    """Derive a short repo identifier (first 16 hex chars of SHA-256) from a canonical path."""
    ...

# ===========================================================================
# Guard helper functions
# ===========================================================================

def clamp_int(value: int, minimum: int, maximum: int) -> int: ...
def clamp_depth(value: int, maximum: int) -> int: ...
def clamp_budget(value: int, minimum: int, maximum: int) -> int: ...
def clamp_limit(value: int, maximum: int) -> int: ...
def truncate_query(query: str) -> str: ...
def adaptive_graph_cap(total_symbols: int, base_cap: int, floor: int | None = None) -> int: ...

# ===========================================================================
# Tokenizer
# ===========================================================================

def estimate_tokens(text: str, model: str | None = None) -> int: ...

# ===========================================================================
# Hybrid scoring
# ===========================================================================

def hybrid_search_enabled() -> bool: ...
def semantic_vector_enabled() -> bool: ...
def lexical_score(query: str, name: str, qualified_name: str) -> float: ...
def structural_score(pagerank: float, callers: int, callees: int) -> float: ...
def semantic_score(
    query: str,
    signature: str | None = None,
    docstring: str | None = None,
) -> float: ...
def rank_symbol(
    *,
    query: str,
    name: str,
    qualified_name: str,
    signature: str | None = None,
    docstring: str | None = None,
    pagerank: float,
    callers: int,
    callees: int,
) -> float: ...

# ===========================================================================
# Query engine functions
# ===========================================================================

def search_symbols(
    db: Database,
    query: str,
    kind: str = "any",
    file_pattern: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search symbols in the database. Returns ``{"symbols": [...], "total_matches": int}``."""
    ...

def get_references(
    db: Database,
    symbol_name: str,
    direction: str = "both",
    depth: int = 1,
    include_source: bool = False,
) -> dict[str, Any]:
    """Traverse callers/callees/implementors/supers. Returns a dict payload."""
    ...

def get_context(
    db: Database,
    query: str,
    entry_points: list[str] = ...,
    token_budget: int = 8000,
    include_signatures_only: bool = False,
    expansion_depth: int = 2,
) -> dict[str, Any]:
    """Context assembly: seeded BFS + personalized PageRank + token-budget pruning."""
    ...

def get_blast_radius(
    db: Database,
    symbol_name: str,
    change_type: str,
    max_depth: int,
) -> dict[str, Any]:
    """Blast-radius impact analysis. Returns a dict with target, change_type, impact."""
    ...

def trace_data_flow(
    db: Database,
    symbol_name: str,
    direction: str = "both",
    max_depth: int = 3,
) -> dict[str, Any]:
    """Data-flow tracing over the call graph. Returns a dict with nodes and paths."""
    ...

def change_impact(
    db: Database,
    symbol_name: str,
    change_type: str = "behavior",
    max_depth: int = 3,
) -> dict[str, Any]:
    """Change-impact analysis with graph-aware dependents."""
    ...

def get_structure(
    db: Database,
    path: str = ".",
    token_budget: int = 4000,
    include_signatures: bool = True,
) -> str:
    """Generate a repository structure map as a string."""
    ...

# ===========================================================================
# Sharding resolver functions
# ===========================================================================

def compute_repo_id(path: str) -> str:
    """Compute a deterministic repo_id (first 16 hex of SHA-256) from a canonical path."""
    ...

def resolve_cross_repo_imports(
    catalog: ShardCatalog,
    repo_id: str,
    db: Database,
) -> list[dict[str, Any]]:
    """Resolve external deps against the catalog's exported symbol cache."""
    ...

def post_index_cross_repo_sync(
    catalog: ShardCatalog,
    repo_path: str,
    db: Database,
) -> dict[str, Any]:
    """Post-indexing step: sync exported symbols and resolve cross-repo imports."""
    ...

# ===========================================================================
# Indexer functions
# ===========================================================================

def detect_language(path: str) -> str | None:
    """Detect the programming language from the file extension."""
    ...

def compute_content_hash(path: str) -> str:
    """Compute a SHA-256 hex digest of the file contents."""
    ...

def tree_sitter_capability_report() -> dict[str, Any]:
    """Report on available tree-sitter language grammars."""
    ...

def recompute_pagerank(
    db: Database,
    damping: float = 0.85,
    epsilon: float = 1e-6,
) -> None:
    """Recompute PageRank scores for all symbols in the database."""
    ...

def rust_full_index(
    repo_root: str,
    db_path: str,
    workers: int = 4,
) -> dict[str, Any]:
    """Full indexing pipeline (Rust/Rayon). Returns stats dict."""
    ...

# ===========================================================================
# Model classes (frozen, get_all)
# ===========================================================================

class FileRecord:
    """A record representing a single indexed file."""

    path: str
    language: str
    content_hash: str
    size_bytes: int | None

    def __new__(
        cls,
        path: str,
        language: str,
        content_hash: str,
        size_bytes: int | None = None,
    ) -> FileRecord: ...
    def __repr__(self) -> str: ...


class ParameterRecord:
    """A record representing a single parameter of a symbol (function/method)."""

    name: str
    position: int
    # The Rust field is ``type_`` but exposed to Python as ``type`` via
    # ``#[pyo3(name = "type")]``.  The constructor still uses ``type_``.
    type: str | None
    default_value: str | None

    def __new__(
        cls,
        name: str,
        position: int,
        type_: str | None = None,
        default_value: str | None = None,
    ) -> ParameterRecord: ...
    def __repr__(self) -> str: ...


class SymbolRecord:
    """A record representing a code symbol (function, class, method, etc.)."""

    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    return_type: str | None
    visibility: str | None
    is_async: bool
    is_static: bool
    parent_symbol_id: int | None
    docstring: str | None
    pagerank_score: float
    parameters: list[ParameterRecord]

    def __new__(
        cls,
        name: str,
        qualified_name: str,
        kind: str,
        file_path: str,
        start_line: int,
        end_line: int,
        signature: str | None = None,
        return_type: str | None = None,
        visibility: str | None = None,
        is_async: bool = False,
        is_static: bool = False,
        parent_symbol_id: int | None = None,
        docstring: str | None = None,
        pagerank_score: float = 0.0,
        parameters: list[ParameterRecord] = ...,
    ) -> SymbolRecord: ...
    def __repr__(self) -> str: ...


class SymbolKey:
    """Unique identity key for a symbol (qualified_name + file + line range + sig hash)."""

    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature_hash: str

    def __new__(
        cls,
        qualified_name: str,
        file_path: str,
        start_line: int,
        end_line: int,
        signature_hash: str,
    ) -> SymbolKey: ...

    @classmethod
    def from_symbol(cls, symbol: SymbolRecord) -> SymbolKey:
        """Build a ``SymbolKey`` from a ``SymbolRecord``."""
        ...

    @classmethod
    def from_fields(
        cls,
        qualified_name: str,
        file_path: str,
        start_line: int,
        end_line: int,
        signature: str | None = None,
    ) -> SymbolKey:
        """Build a ``SymbolKey`` from raw field values (hashing the signature)."""
        ...

    def as_tuple(self) -> tuple[str, str, int, int, str]:
        """Return the key as a Python tuple."""
        ...

    def __repr__(self) -> str: ...


class EdgeKey:
    """Unique identity key for an edge between two symbols."""

    source: SymbolKey
    target: SymbolKey
    relationship: str
    line_number: int

    def __new__(
        cls,
        source: SymbolKey,
        target: SymbolKey,
        relationship: str,
        line_number: int,
    ) -> EdgeKey: ...

    def as_tuple(self) -> tuple[tuple[str, str, int, int, str], tuple[str, str, int, int, str], str, int]:
        """Return ``(source.as_tuple(), target.as_tuple(), relationship, line_number)``."""
        ...

    def __repr__(self) -> str: ...


class EdgeRecord:
    """A stored edge row with numeric source/target ids."""

    source_id: int
    target_id: int
    source_type: str
    target_type: str
    relationship: str
    file_path: str | None
    line_number: int | None
    confidence: float

    def __new__(
        cls,
        source_id: int,
        target_id: int,
        source_type: str,
        target_type: str,
        relationship: str,
        file_path: str | None = None,
        line_number: int | None = None,
        confidence: float = 1.0,
    ) -> EdgeRecord: ...
    def __repr__(self) -> str: ...


class EdgeContractRecord:
    """A contract-level edge carrying full ``SymbolKey`` endpoints."""

    source: SymbolKey
    target: SymbolKey
    relationship: str
    line_number: int
    confidence: float
    provenance: str

    def __new__(
        cls,
        source: SymbolKey,
        target: SymbolKey,
        relationship: str,
        line_number: int,
        confidence: float = 1.0,
        provenance: str = "local",
    ) -> EdgeContractRecord: ...

    def key(self) -> EdgeKey:
        """Derive the ``EdgeKey`` for this record."""
        ...

    def as_tuple(self) -> tuple[tuple[str, str, int, int, str], tuple[str, str, int, int, str], str, int]:
        """Return the tuple representation of the underlying ``EdgeKey``."""
        ...

    def __repr__(self) -> str: ...


class ExternalDepRecord:
    """An external (unresolvable) dependency reference."""

    file_path: str
    import_statement: str
    module_name: str
    line_number: int | None

    def __new__(
        cls,
        file_path: str,
        import_statement: str,
        module_name: str,
        line_number: int | None = None,
    ) -> ExternalDepRecord: ...
    def __repr__(self) -> str: ...


class ImportRecord:
    """A resolved import statement with its constituent imported names."""

    source_file_path: str
    import_statement: str
    module_name: str
    imported_names: list[str]
    line_number: int | None

    def __new__(
        cls,
        source_file_path: str,
        import_statement: str,
        module_name: str,
        imported_names: list[str] = ...,
        line_number: int | None = None,
    ) -> ImportRecord: ...
    def __repr__(self) -> str: ...


class ParsedUnit:
    """A parsed source file with its tree-sitter AST."""

    path: str
    language: str
    source: str
    tree: Any

    def __new__(
        cls,
        path: str,
        language: str,
        source: str,
        tree: Any,
    ) -> ParsedUnit: ...
    def __repr__(self) -> str: ...


class FileChange:
    """A file-level change detected by git-diff or the watcher."""

    status: str
    path: str
    old_path: str | None

    def __new__(
        cls,
        status: str,
        path: str,
        old_path: str | None = None,
    ) -> FileChange: ...
    def __repr__(self) -> str: ...


class WorkspaceRoot:
    """A single root entry in a multi-root workspace configuration."""

    id: str
    path: str
    db_path: str
    enabled: bool

    def __new__(
        cls,
        id: str,
        path: str,
        db_path: str,
        enabled: bool = True,
    ) -> WorkspaceRoot: ...
    def __repr__(self) -> str: ...


class WorkspaceConfig:
    """Top-level workspace configuration referencing multiple roots."""

    name: str
    version: int
    roots: list[WorkspaceRoot]

    def __new__(
        cls,
        name: str,
        version: int,
        roots: list[WorkspaceRoot] = ...,
    ) -> WorkspaceConfig: ...
    def __repr__(self) -> str: ...


class FileDelta:
    """A file-level delta entry within an ``IndexDelta``."""

    status: str
    path: str
    old_path: str | None
    content_hash: str | None
    size_bytes: int | None

    def __new__(
        cls,
        status: str,
        path: str,
        old_path: str | None = None,
        content_hash: str | None = None,
        size_bytes: int | None = None,
    ) -> FileDelta: ...
    def __repr__(self) -> str: ...


class DeltaHeader:
    """Metadata header for an ``IndexDelta`` payload."""

    repo_id: str
    parent_snapshot: str | None
    local_snapshot: str
    tool_version: str
    schema_version: int
    created_at_utc: str

    def __new__(
        cls,
        repo_id: str,
        parent_snapshot: str | None,
        local_snapshot: str,
        tool_version: str,
        schema_version: int,
        created_at_utc: str,
    ) -> DeltaHeader: ...
    def __repr__(self) -> str: ...


class QualityStats:
    """Quality statistics produced alongside an index delta."""

    ambiguity_rate: float
    unresolved_imports: int
    parse_failures: int

    def __new__(
        cls,
        ambiguity_rate: float = 0.0,
        unresolved_imports: int = 0,
        parse_failures: int = 0,
    ) -> QualityStats: ...
    def __repr__(self) -> str: ...


class IndexDelta:
    """An incremental index delta describing changes since the last snapshot."""

    header: DeltaHeader
    file_changes: list[FileDelta]
    symbol_upserts: list[SymbolRecord]
    symbol_deletes: list[SymbolKey]
    edge_upserts: list[EdgeContractRecord]
    edge_deletes: list[EdgeContractRecord]
    quality_stats: QualityStats

    def __new__(
        cls,
        header: DeltaHeader,
        file_changes: list[FileDelta] = ...,
        symbol_upserts: list[SymbolRecord] = ...,
        symbol_deletes: list[SymbolKey] = ...,
        edge_upserts: list[EdgeContractRecord] = ...,
        edge_deletes: list[EdgeContractRecord] = ...,
        quality_stats: QualityStats = ...,
    ) -> IndexDelta: ...
    def __repr__(self) -> str: ...


class ArtifactBundle:
    """A promoted artifact bundle for sync/export."""

    artifact_id: str
    repo_id: str
    snapshot_id: str
    parent_snapshot: str | None
    tool_version: str
    schema_version: int
    created_at_utc: str
    promoted_symbols: list[SymbolKey]
    promoted_edges: list[EdgeContractRecord]
    impact_priors: Any
    flow_hints: Any
    signature_algo: str | None
    signing_key_id: str | None
    checksum: str | None
    signature: str | None

    def __new__(
        cls,
        artifact_id: str,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
        tool_version: str,
        schema_version: int,
        created_at_utc: str,
        promoted_symbols: list[SymbolKey] = ...,
        promoted_edges: list[EdgeContractRecord] = ...,
        impact_priors: Any = None,
        flow_hints: Any = None,
        signature_algo: str | None = None,
        signing_key_id: str | None = None,
        checksum: str | None = None,
        signature: str | None = None,
    ) -> ArtifactBundle: ...
    def __repr__(self) -> str: ...


class IndexStats:
    """Summary statistics from an indexing run."""

    files_seen: int
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    elapsed_ms: int
    run_id: str | None
    diagnostics_summary: Any
    indexing_telemetry: Any
    progress_snapshots: Any

    def __new__(
        cls,
        files_seen: int,
        files_indexed: int,
        symbols_indexed: int,
        edges_indexed: int,
        elapsed_ms: int,
        run_id: str | None = None,
        diagnostics_summary: Any = None,
        indexing_telemetry: Any = None,
        progress_snapshots: Any = None,
    ) -> IndexStats: ...
    def __repr__(self) -> str: ...


class SymbolSearchRequest:
    """Parameters for a symbol-search query."""

    query: str
    kind: str
    file_pattern: str | None
    limit: int

    def __new__(
        cls,
        query: str,
        kind: str = "any",
        file_pattern: str | None = None,
        limit: int = 20,
    ) -> SymbolSearchRequest: ...
    def __repr__(self) -> str: ...


class ReferenceRequest:
    """Parameters for a reference (callers/callees) query."""

    symbol_name: str
    direction: str
    depth: int
    include_source: bool

    def __new__(
        cls,
        symbol_name: str,
        direction: str = "both",
        depth: int = 1,
        include_source: bool = False,
    ) -> ReferenceRequest: ...
    def __repr__(self) -> str: ...


class ContextRequest:
    """Parameters for a context-assembly query."""

    query: str
    entry_points: list[str]
    token_budget: int
    include_signatures_only: bool
    expansion_depth: int

    def __new__(
        cls,
        query: str,
        entry_points: list[str] = ...,
        token_budget: int = 8000,
        include_signatures_only: bool = False,
        expansion_depth: int = 2,
    ) -> ContextRequest: ...
    def __repr__(self) -> str: ...


class StructureRequest:
    """Parameters for a structure (file/directory overview) query."""

    path: str
    token_budget: int
    include_signatures: bool

    def __new__(
        cls,
        path: str = ".",
        token_budget: int = 4000,
        include_signatures: bool = True,
    ) -> StructureRequest: ...
    def __repr__(self) -> str: ...


class BlastRadiusRequest:
    """Parameters for a blast-radius (impact analysis) query."""

    symbol_name: str
    change_type: str
    max_depth: int

    def __new__(
        cls,
        symbol_name: str,
        change_type: str = "behavior",
        max_depth: int = 3,
    ) -> BlastRadiusRequest: ...
    def __repr__(self) -> str: ...


class SymbolSearchResponse:
    """Response payload from a symbol-search query."""

    symbols: Any
    total_matches: int

    def __new__(cls, symbols: Any, total_matches: int) -> SymbolSearchResponse: ...
    def __repr__(self) -> str: ...


class ReferenceResponse:
    """Response payload from a reference query."""

    payload: Any

    def __new__(cls, payload: Any) -> ReferenceResponse: ...
    def __repr__(self) -> str: ...


class ContextResponse:
    """Response payload from a context-assembly query."""

    payload: Any

    def __new__(cls, payload: Any) -> ContextResponse: ...
    def __repr__(self) -> str: ...


class BlastRadiusResponse:
    """Response payload from a blast-radius query."""

    payload: Any

    def __new__(cls, payload: Any) -> BlastRadiusResponse: ...
    def __repr__(self) -> str: ...


# ===========================================================================
# Phase 15: Cross-repo graphing and sharding models
# ===========================================================================

class GlobalSymbolURI:
    """Globally unique symbol identifier across repositories."""

    repo_id: str
    qualified_name: str
    file_path: str

    def __new__(
        cls,
        repo_id: str,
        qualified_name: str,
        file_path: str,
    ) -> GlobalSymbolURI: ...

    @property
    def uri(self) -> str:
        """The canonical URI string: ``bombe://<repo_id>/<qualified_name>#<file_path>``."""
        ...

    @classmethod
    def from_uri(cls, uri: str) -> GlobalSymbolURI:
        """Parse a ``bombe://`` URI string into a ``GlobalSymbolURI``."""
        ...

    @classmethod
    def from_symbol(cls, repo_id: str, symbol: SymbolRecord) -> GlobalSymbolURI:
        """Build a ``GlobalSymbolURI`` from a repo id and a ``SymbolRecord``."""
        ...

    def __repr__(self) -> str: ...


class ShardInfo:
    """Metadata about a single shard (repo database) in a shard group."""

    repo_id: str
    repo_path: str
    db_path: str
    enabled: bool
    last_indexed_at: str | None
    symbol_count: int
    edge_count: int

    def __new__(
        cls,
        repo_id: str,
        repo_path: str,
        db_path: str,
        enabled: bool = True,
        last_indexed_at: str | None = None,
        symbol_count: int = 0,
        edge_count: int = 0,
    ) -> ShardInfo: ...
    def __repr__(self) -> str: ...


class CrossRepoEdge:
    """An edge between symbols in different repositories."""

    source_uri: GlobalSymbolURI
    target_uri: GlobalSymbolURI
    relationship: str
    confidence: float
    provenance: str

    def __new__(
        cls,
        source_uri: GlobalSymbolURI,
        target_uri: GlobalSymbolURI,
        relationship: str,
        confidence: float = 1.0,
        provenance: str = "import_resolution",
    ) -> CrossRepoEdge: ...
    def __repr__(self) -> str: ...


class ShardGroupConfig:
    """Configuration for a group of repos that may reference each other."""

    name: str
    catalog_db_path: str
    shards: list[ShardInfo]
    version: int

    def __new__(
        cls,
        name: str,
        catalog_db_path: str,
        shards: list[ShardInfo] = ...,
        version: int = 1,
    ) -> ShardGroupConfig: ...
    def __repr__(self) -> str: ...


class FederatedQueryResult:
    """Result from a query that spans multiple shards."""

    results: Any
    shard_reports: Any
    total_matches: int
    shards_queried: int
    shards_failed: int
    elapsed_ms: int

    def __new__(
        cls,
        results: Any = None,
        shard_reports: Any = None,
        total_matches: int = 0,
        shards_queried: int = 0,
        shards_failed: int = 0,
        elapsed_ms: int = 0,
    ) -> FederatedQueryResult: ...
    def __repr__(self) -> str: ...


# ===========================================================================
# Store: Database
# ===========================================================================

class Database:
    """SQLite graph store for Bombe.

    Every public method opens its own connection so the caller never needs to
    manage connection lifetime.
    """

    def __new__(cls, db_path: str) -> Database: ...

    # -- Schema / meta -------------------------------------------------------

    def init_schema(self) -> None:
        """Initialise the database schema (WAL, tables, indexes, FTS, migrations)."""
        ...

    def query(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an arbitrary SQL statement and return a list of Python dicts."""
        ...

    def get_repo_meta(self, key: str) -> str | None:
        """Get a single repo_meta value by key, or ``None``."""
        ...

    def set_repo_meta(self, key: str, value: str) -> None:
        """Upsert a single repo_meta key/value pair."""
        ...

    def get_cache_epoch(self) -> int:
        """Return the current cache epoch (initialising to 1 if absent)."""
        ...

    def bump_cache_epoch(self) -> int:
        """Atomically increment the cache epoch and return the new value."""
        ...

    # -- File / symbol CRUD --------------------------------------------------

    def upsert_files(self, records: list[FileRecord]) -> None:
        """Upsert a batch of file records into the ``files`` table."""
        ...

    def replace_file_symbols(self, file_path: str, symbols: list[SymbolRecord]) -> None:
        """Replace all symbols (and their parameters + FTS entries) for a file."""
        ...

    def replace_file_edges(self, file_path: str, edges: list[EdgeRecord]) -> None:
        """Replace all edges for a given file path."""
        ...

    def replace_external_deps(self, file_path: str, deps: list[ExternalDepRecord]) -> None:
        """Replace all external dependency records for a given file path."""
        ...

    def delete_file_graph(self, file_path: str) -> None:
        """Delete all graph data for a given file path."""
        ...

    def rename_file(self, old_path: str, new_path: str) -> None:
        """Rename a file in the index, moving all associated data."""
        ...

    # -- Backup --------------------------------------------------------------

    def backup_to(self, destination: str) -> str:
        """Create a backup of the database. Returns the resolved path."""
        ...

    def restore_from(self, source: str) -> None:
        """Restore the database from a backup file."""
        ...

    # -- Sync queue ----------------------------------------------------------

    def enqueue_sync_delta(
        self,
        repo_id: str,
        local_snapshot: str,
        payload_json: str,
    ) -> int:
        """Enqueue a new sync delta and return its row id."""
        ...

    def list_pending_sync_deltas(
        self,
        repo_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List pending (queued or retry) sync deltas for a repo."""
        ...

    def mark_sync_delta_status(
        self,
        queue_id: int,
        status: str,
        last_error: str | None = None,
    ) -> None:
        """Mark a sync delta with a new status and optionally record an error."""
        ...

    def normalize_sync_queue_statuses(self) -> int:
        """Normalise sync queue entries with unknown statuses back to 'retry'."""
        ...

    # -- Artifacts -----------------------------------------------------------

    def set_artifact_pin(
        self,
        repo_id: str,
        snapshot_id: str,
        artifact_id: str,
    ) -> None:
        """Pin an artifact to a (repo_id, snapshot_id) pair."""
        ...

    def get_artifact_pin(
        self,
        repo_id: str,
        snapshot_id: str,
    ) -> str | None:
        """Get the artifact id pinned to a (repo_id, snapshot_id) pair."""
        ...

    def quarantine_artifact(self, artifact_id: str, reason: str) -> None:
        """Quarantine an artifact, recording the reason."""
        ...

    def is_artifact_quarantined(self, artifact_id: str) -> bool:
        """Check whether an artifact has been quarantined."""
        ...

    def list_quarantined_artifacts(
        self,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List quarantined artifacts, most recent first."""
        ...

    # -- Circuit breakers ----------------------------------------------------

    def set_circuit_breaker_state(
        self,
        repo_id: str,
        state: str,
        failure_count: int,
        opened_at_utc: str | None = None,
    ) -> None:
        """Set (upsert) the circuit breaker state for a repo."""
        ...

    def get_circuit_breaker_state(
        self,
        repo_id: str,
    ) -> dict[str, Any] | None:
        """Get the circuit breaker state for a repo, or None."""
        ...

    # -- Events / metrics ----------------------------------------------------

    def record_sync_event(
        self,
        repo_id: str,
        level: str,
        event_type: str,
        detail: Any | None = None,
    ) -> None:
        """Record a sync event."""
        ...

    def record_tool_metric(
        self,
        tool_name: str,
        latency_ms: float,
        success: bool,
        mode: str,
        repo_id: str | None = None,
        result_size: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record a tool metric observation."""
        ...

    def recent_tool_metrics(
        self,
        tool_name: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve recent tool metrics for a given tool."""
        ...

    # -- Diagnostics ---------------------------------------------------------

    def record_indexing_diagnostic(
        self,
        run_id: str,
        stage: str,
        category: str,
        message: str,
        hint: str | None = None,
        file_path: str | None = None,
        language: str | None = None,
        severity: str | None = None,
    ) -> None:
        """Record an indexing diagnostic entry."""
        ...

    def list_indexing_diagnostics(
        self,
        limit: int | None = None,
        offset: int | None = None,
        run_id: str | None = None,
        stage: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        """List indexing diagnostics with optional filters."""
        ...

    def summarize_indexing_diagnostics(
        self,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a summary dict of indexing diagnostics."""
        ...

    def clear_indexing_diagnostics(
        self,
        run_id: str | None = None,
    ) -> int:
        """Delete indexing diagnostics. Returns the number of rows deleted."""
        ...

    # -- Signing keys --------------------------------------------------------

    def set_trusted_signing_key(
        self,
        repo_id: str,
        key_id: str,
        algorithm: str,
        public_key: str,
        purpose: str | None = None,
        active: bool | None = None,
    ) -> None:
        """Upsert a trusted signing key for a repo."""
        ...

    def get_trusted_signing_key(
        self,
        repo_id: str,
        key_id: str,
    ) -> dict[str, Any] | None:
        """Get a single trusted signing key, or None."""
        ...

    def list_trusted_signing_keys(
        self,
        repo_id: str,
        active_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List trusted signing keys for a repo."""
        ...


# ===========================================================================
# Store: ShardCatalog
# ===========================================================================

class ShardCatalog:
    """Manages a SQLite catalog database for cross-repo sharding."""

    def __new__(cls, catalog_db_path: str) -> ShardCatalog: ...

    def init_schema(self) -> None:
        """Initialise the catalog schema (WAL, tables, indexes, migrations)."""
        ...

    def query(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an arbitrary SQL statement and return a list of Python dicts."""
        ...

    # -- Shard management ----------------------------------------------------

    def register_shard(
        self,
        repo_id: str,
        repo_path: str,
        db_path: str,
    ) -> None:
        """Register a shard by repo_id, repo_path, and db_path."""
        ...

    def unregister_shard(self, repo_id: str) -> None:
        """Unregister a shard and delete its associated data."""
        ...

    def list_shards(
        self,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        """List all shards, optionally filtered to enabled only."""
        ...

    def get_shard(
        self,
        repo_id: str,
    ) -> dict[str, Any] | None:
        """Return shard info by repo_id, or None."""
        ...

    def update_shard_stats(
        self,
        repo_id: str,
        symbol_count: int,
        edge_count: int,
    ) -> None:
        """Update symbol_count, edge_count, last_indexed_at for a shard."""
        ...

    # -- Exported symbol cache -----------------------------------------------

    def refresh_exported_symbols(
        self,
        repo_id: str,
        db: Database,
    ) -> int:
        """Refresh exported symbols from a shard Database. Returns count."""
        ...

    def search_exported_symbols(
        self,
        name: str,
        kind: str = "any",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search exported symbols by name LIKE pattern."""
        ...

    def resolve_external_import(
        self,
        module_name: str,
        language: str,
    ) -> list[dict[str, Any]]:
        """Find exported symbols matching module_name for cross-repo resolution."""
        ...

    # -- Cross-repo edge management ------------------------------------------

    def upsert_cross_repo_edges(
        self,
        edges: list[dict[str, Any]],
    ) -> int:
        """Upsert cross-repo edges from a list of dicts. Returns count."""
        ...

    def get_cross_repo_edges_from(
        self,
        repo_id: str,
        symbol_name: str,
    ) -> list[dict[str, Any]]:
        """Get outgoing cross-repo edges from a symbol."""
        ...

    def get_cross_repo_edges_to(
        self,
        repo_id: str,
        symbol_name: str,
    ) -> list[dict[str, Any]]:
        """Get incoming cross-repo edges to a symbol."""
        ...

    def delete_cross_repo_edges_for_repo(self, repo_id: str) -> int:
        """Delete all cross-repo edges involving a repo. Returns count."""
        ...

    def get_shard_db_path(self, repo_id: str) -> str | None:
        """Return the db_path for a shard, or None."""
        ...


# ===========================================================================
# Store: ShardRouter
# ===========================================================================

class ShardRouter:
    """Routes queries to appropriate shards and manages shard connections."""

    def __new__(
        cls,
        catalog: ShardCatalog,
        max_connections: int = 8,
    ) -> ShardRouter: ...

    def get_shard_db(self, repo_id: str) -> Database | None:
        """Return a Database for the given shard, with connection pooling."""
        ...

    def route_symbol_query(self, symbol_name: str) -> list[str]:
        """Determine which shard repo_ids may contain the named symbol."""
        ...

    def route_reference_query(
        self,
        symbol_name: str,
        source_repo_id: str | None = None,
    ) -> list[str]:
        """Determine shards for a reference/caller/callee query."""
        ...

    def all_shard_ids(self) -> list[str]:
        """Return all enabled shard repo_ids."""
        ...

    def shard_health(self) -> list[dict[str, Any]]:
        """Return health status for each enabled shard."""
        ...

    def close_all(self) -> None:
        """Release all pooled connections (clear the pool)."""
        ...


# ===========================================================================
# Query: QueryPlanner (LRU cache)
# ===========================================================================

class QueryPlanner:
    """Query planner with lightweight in-memory response caching."""

    def __new__(
        cls,
        max_entries: int = 512,
        ttl_seconds: float = 15.0,
    ) -> QueryPlanner: ...

    def get_or_compute(
        self,
        tool_name: str,
        payload: dict[str, Any],
        compute: Callable[[], Any],
        version_token: str | None = None,
    ) -> tuple[Any, str]:
        """Look up or compute a result. Returns ``(result, mode)``."""
        ...

    def get_or_compute_with_trace(
        self,
        tool_name: str,
        payload: dict[str, Any],
        compute: Callable[[], Any],
        version_token: str | None = None,
    ) -> tuple[Any, str, dict[str, Any]]:
        """Look up or compute with timing trace. Returns ``(result, mode, trace)``."""
        ...

    def stats(self) -> dict[str, int]:
        """Return cache statistics ``{"entries": ..., "max_entries": ...}``."""
        ...


# ===========================================================================
# Query: Federated planner and executor
# ===========================================================================

class ShardQueryPlan:
    """A plan for executing a query across shards."""

    shard_ids: list[str]
    cross_repo_edges: list[Any]
    fan_out_strategy: str
    merge_strategy: str

    def __new__(
        cls,
        shard_ids: list[str] = ...,
        cross_repo_edges: list[Any] = ...,
        fan_out_strategy: str = "all",
        merge_strategy: str = "score_sort",
    ) -> ShardQueryPlan: ...


class FederatedQueryPlanner:
    """Plans queries that span multiple shards."""

    def __new__(cls, catalog: Any, router: Any) -> FederatedQueryPlanner: ...

    def plan_search(
        self,
        query: str,
        kind: str = "any",
        limit: int = 20,
    ) -> ShardQueryPlan:
        """Plan a federated symbol search."""
        ...

    def plan_references(
        self,
        symbol_name: str,
        direction: str,
        depth: int,
        source_repo_id: str | None = None,
    ) -> ShardQueryPlan:
        """Plan a federated reference query."""
        ...

    def plan_blast_radius(
        self,
        symbol_name: str,
        max_depth: int,
    ) -> ShardQueryPlan:
        """Plan a federated blast-radius query."""
        ...

    def plan_context(
        self,
        query: str,
        entry_points: list[str],
    ) -> ShardQueryPlan:
        """Plan a federated context query."""
        ...


class FederatedQueryExecutor:
    """Executes queries across multiple shards."""

    def __new__(
        cls,
        catalog: Any,
        router: Any,
        planner: Any,
    ) -> FederatedQueryExecutor: ...

    def execute_search(
        self,
        query: str,
        kind: str,
        file_pattern: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Execute a federated symbol search."""
        ...

    def execute_references(
        self,
        symbol_name: str,
        direction: str,
        depth: int,
        include_source: bool,
    ) -> dict[str, Any]:
        """Execute a federated reference query."""
        ...

    def execute_blast_radius(
        self,
        symbol_name: str,
        change_type: str,
        max_depth: int,
    ) -> dict[str, Any]:
        """Execute a federated blast-radius query."""
        ...
