# Bombe-MCP: Incremental Python-to-Rust Migration Plan

## Context

The `VALID_ITEMS_MASTER_PLAN.md` (lines 54-67) identifies a full Rust rewrite as a strategic alternative with high feasibility, 10-50x faster indexing via Rayon parallelism, single static binary deployment, and async Tokio concurrency for 100s of agents. Rather than a clean-break rewrite, the user chose an **incremental migration**: rewrite CPU-bound modules in Rust, expose via PyO3, keep Python for I/O-bound orchestration.

The guiding principle: **put each language where it excels**. Rust owns computation (parsing, graph traversal, SQLite, parallel indexing). Python owns protocol handling (MCP/STDIO, CLI, sync, plugins, configuration). Phase 15 sharding is included.

---

## Architecture Overview

```
bombe-mcp/
├── Cargo.toml                       # Workspace root
├── crates/
│   └── bombe-core/                  # Rust library (~6,600 LOC)
│       ├── Cargo.toml
│       ├── build.rs                 # tree-sitter grammar compilation
│       ├── queries/                 # tree-sitter .scm query files per language
│       └── src/
│           ├── lib.rs               # PyO3 #[pymodule] definition
│           ├── errors.rs            # BombeError → PyResult mapping
│           ├── models.rs            # Core types with #[pyclass]
│           ├── store/
│           │   ├── database.rs      # rusqlite layer (port of 1,242 LOC)
│           │   ├── schema.rs        # DDL + migrations v1→v7
│           │   └── sharding/
│           │       ├── catalog.rs   # Cross-repo catalog
│           │       ├── router.rs    # Shard routing + connection pool
│           │       └── resolver.rs  # Cross-repo import resolution
│           ├── indexer/
│           │   ├── pipeline.rs      # Rayon-parallel indexing
│           │   ├── parser.rs        # tree-sitter native (all languages)
│           │   ├── symbols.rs       # Unified tree-sitter query extraction
│           │   ├── callgraph.rs     # Call graph building
│           │   ├── imports.rs       # Per-language import resolution
│           │   ├── filesystem.rs    # walkdir + ignore (from ripgrep author)
│           │   └── pagerank.rs      # PageRank algorithm
│           └── query/
│               ├── context.rs       # Token-budgeted BFS + personalized PageRank
│               ├── references.rs    # Caller/callee BFS traversal
│               ├── search.rs        # Hybrid symbol search (lexical+structural+semantic)
│               ├── blast.rs         # Blast radius BFS
│               ├── data_flow.rs     # Data flow tracing
│               ├── change_impact.rs # Change impact estimation
│               ├── guards.rs        # Shared constants
│               ├── planner.rs       # LRU+TTL query cache
│               └── federated/
│                   ├── planner.rs   # Federated query plans
│                   └── executor.rs  # Multi-shard execution
├── src/bombe/                       # Python thin wrappers (~6,000 LOC)
│   ├── _bombe_core.pyi             # Type stubs for Rust module
│   ├── _backend.py                 # Backend selector (Rust vs pure-Python)
│   ├── server.py                   # CLI + MCP (calls Rust core)
│   ├── tools/definitions.py        # Tool schemas (delegates to Rust query engines)
│   ├── models.py                   # Re-exports from Rust #[pyclass] types
│   ├── sync/                       # Stays pure Python (I/O-bound)
│   ├── plugins/                    # Stays pure Python (dynamic loading)
│   ├── workspace.py                # Stays pure Python
│   ├── watcher/                    # Stays pure Python (git shell-out)
│   ├── control_plane/              # Stays pure Python (HTTP)
│   ├── ui_api/                     # Stays pure Python
│   └── lsp/                        # Stays pure Python
└── pyproject.toml                   # maturin build backend
```

### Data Flow After Migration

```
AI Agent → MCP/STDIO → server.py (Python) → tools/definitions.py (Python)
                                                      │
                                              _bombe_core (Rust/PyO3)
                                             ╱          │           ╲
                                  indexer (Rayon)   query engines   store (rusqlite)
                                  tree-sitter        BFS/PPR        SQLite
```

---

## What Moves to Rust vs Stays in Python

### Rust (~6,600 LOC) — CPU-bound, parallelizable, memory-sensitive

| Module | LOC | Why Rust |
|--------|-----|----------|
| `indexer/pipeline.py` | 655 | Rayon work-stealing replaces ProcessPoolExecutor; 5-10x speedup |
| `indexer/symbols.py` | 646 | Unified tree-sitter queries for ALL languages (replaces ast+regex split) |
| `indexer/callgraph.py` | 593 | CPU-bound 7-level cascading resolution |
| `indexer/imports.py` | 177 | String manipulation + hash map lookup |
| `indexer/parser.py` | 130 | tree-sitter IS a C/Rust library; eliminates FFI layer |
| `indexer/filesystem.py` | 155 | `walkdir` + `ignore` crates (ripgrep author); native .gitignore |
| `indexer/pagerank.py` | 69 | Pure adjacency-list math |
| `indexer/semantic.py` | 138 | Receiver type hint loading |
| `store/database.py` | 1,242 | `rusqlite`: no GIL contention, zero-copy rows, prepared statements |
| `store/sharding/catalog.py` | 458 | Same as database.py |
| `store/sharding/router.py` | 234 | Thread-safe pool with `parking_lot::Mutex` |
| `store/sharding/resolver.py` | 250 | CPU-bound cross-repo matching |
| `query/context.py` | 534 | BFS + personalized PageRank (20 iterations) + token budgeting |
| `query/references.py` | 215 | Graph BFS traversal |
| `query/search.py` | 175 | Hybrid scoring computation |
| `query/blast.py` | 108 | Reverse-edge BFS |
| `query/data_flow.py` | 178 | Bidirectional BFS |
| `query/change_impact.py` | 163 | BFS + type dependency |
| `query/planner.py` | 110 | Concurrent LRU+TTL cache |
| `query/guards.py` | 51 | Shared constants |
| `query/federated/planner.py` | 93 | Query plan generation |
| `query/federated/executor.py` | 246 | Multi-shard parallel execution |

### Python (~6,000 LOC) — I/O-bound, protocol, dynamic

| Module | LOC | Why Python |
|--------|-----|------------|
| `server.py` | 1,753 | MCP FastMCP over STDIO; argparse CLI; I/O-bound |
| `tools/definitions.py` | 1,507 | Tool JSON schemas; handler wiring → thin delegation to Rust |
| `sync/*` | 1,263 | HTTP push/pull; network I/O; circuit breaker orchestration |
| `models.py` | 414 | Re-export bridge from Rust `#[pyclass]` types |
| `workspace.py` | 204 | JSON config parsing |
| `plugins/manager.py` | 174 | Dynamic loading via importlib; duck typing |
| `control_plane/server.py` | 330 | HTTP endpoints |
| `watcher/git_diff.py` | 173 | Shell-out to `git diff` |
| `ui_api/inspector.py` | 213 | Web UI bundle serving |
| `lsp/bridge.py` | 102 | LSP integration |

---

## Rust Ecosystem Dependencies

```toml
[dependencies]
# Python bindings
pyo3 = { version = "0.23", features = ["extension-module"] }

# Parsing (tree-sitter is natively Rust/C)
tree-sitter = "0.24"
tree-sitter-python = "0.23"
tree-sitter-java = "0.23"
tree-sitter-typescript = "0.23"
tree-sitter-go = "0.23"

# Storage
rusqlite = { version = "0.32", features = ["bundled", "backup"] }

# Parallelism
rayon = "1.10"

# Serialization
serde = { version = "1", features = ["derive"] }
serde_json = "1"

# File system
walkdir = "2"
ignore = "0.4"           # .gitignore-aware walker (ripgrep author)

# Hashing, regex, sync
sha2 = "0.10"
regex = "1.11"
parking_lot = "0.12"     # Faster Mutex than std
thiserror = "2"
tracing = "0.1"
tracing-subscriber = "0.3"
```

Note: The official `rmcp` MCP SDK (v0.15.0, 3.4M downloads) exists for Rust, but we do NOT need it — Python keeps the MCP server layer. Rust is a library only.

---

## Implementation Phases

### Phase A: Foundation — Cargo + PyO3 + Models

**Goal**: Build scaffolding. Rust crate compiles, installs into venv, Python can import it.

**A.1** Create Cargo workspace (`Cargo.toml` at root, `crates/bombe-core/Cargo.toml`)

**A.2** PyO3 module scaffolding (`lib.rs` with `#[pymodule]`)

**A.3** Error types (`errors.rs`): `BombeError` enum with `Database`, `Index`, `Query`, `Parse`, `Io` variants. Implements `From<BombeError> for PyErr`.

**A.4** Port all 25 dataclasses from `src/bombe/models.py` to Rust `#[pyclass(frozen)]` structs:
- `FileRecord`, `ParameterRecord`, `SymbolRecord` (15 fields), `SymbolKey`, `EdgeKey`, `EdgeRecord`, `ExternalDepRecord`, `ImportRecord`, `ParsedUnit`, `FileChange`, `IndexStats`
- Request/Response types: `SymbolSearchRequest`, `ReferenceRequest`, `ContextRequest`, `BlastRadiusRequest`, `StructureRequest` + their responses
- Phase 15 types: `GlobalSymbolURI`, `ShardInfo`, `CrossRepoEdge`, `ShardGroupConfig`, `FederatedQueryResult`

**A.5** maturin build integration — update `pyproject.toml`:
```toml
[build-system]
requires = ["maturin>=1.4,<2"]
build-backend = "maturin"

[tool.maturin]
features = ["pyo3/extension-module"]
python-source = "src"
module-name = "bombe._bombe_core"
```

**A.6** Backend selector (`src/bombe/_backend.py`):
```python
import os
_USE_PYTHON = os.getenv("BOMBE_USE_PYTHON_CORE", "").lower() in {"1", "true", "yes"}
try:
    if not _USE_PYTHON:
        from bombe import _bombe_core  # noqa: F401
        BACKEND = "rust"
    else:
        BACKEND = "python"
except ImportError:
    BACKEND = "python"
```

**A.7** Type stubs (`src/bombe/_bombe_core.pyi`)

**Verification**: `cargo build`, `maturin develop`, `python -c "from bombe._bombe_core import SymbolRecord"`, all existing Python tests pass unchanged (fallback to Python backend).

---

### Phase B: Store Layer — Database + Schema + Migrations

**Goal**: Port `store/database.py` (1,242 LOC) to Rust via `rusqlite`.

**B.1** Schema DDL in Rust (`schema.rs`): Port all 14 CREATE TABLE + 17 CREATE INDEX + 2 FTS5 statements as `const &str` arrays.

**B.2** Migration framework: Port `_migrate_schema` and `_migrate_to_v1` through `_migrate_to_v7` with savepoint-based rollback pattern:
```rust
conn.execute("SAVEPOINT bombe_migrate")?;
match migrate_step(conn, version) {
    Ok(()) => conn.execute("RELEASE SAVEPOINT bombe_migrate")?,
    Err(e) => { conn.execute("ROLLBACK TO SAVEPOINT bombe_migrate")?; ... }
}
```

**B.3** `Database` struct with `#[pyclass]` + `#[pymethods]` exposing all 40+ public methods:
- `init_schema`, `query`, `upsert_files`, `replace_file_symbols`, `replace_file_edges`, `replace_external_deps`, `delete_file_graph`, `rename_file`
- Sync/metrics: `enqueue_sync_delta`, `record_tool_metric`, `get_cache_epoch`, etc.
- Diagnostics: `record_indexing_diagnostic`, `list_indexing_diagnostics`, `summarize_indexing_diagnostics`
- Backup/restore: `backup_to`, `restore_from`

**B.4** Bulk operation optimization: Single transaction per batch, prepared statement reuse, `rusqlite::Statement::execute_batch`. Expected 3-5x speedup on bulk upserts.

**B.5** Design decision — **connection management**: Do NOT expose raw connections to Python. Each Rust method opens/closes its own connection (matching the current `with closing(self.connect()) as conn:` pattern). For migration compat, Python wrapper keeps `connect()` returning `sqlite3.Connection`.

**B.6** Python thin wrapper: `src/bombe/store/database.py` delegates to Rust or falls back to pure Python based on `BACKEND`.

**Verification**: `cargo test`, `test_database.py` passes against Rust backend, all 183+ Python tests pass.

---

### Phase C: Indexer — Parser + Symbols + Call Graph + Pipeline

**Goal**: Port the entire indexer (2,496 LOC) to Rust. **Key architectural change**: ALL languages use tree-sitter queries for symbol extraction — no more Python `ast` module or regex-based extraction.

**C.1** `parser.rs` — Native tree-sitter parsing for all 6 languages. `Parser::new()` + `set_language()` + `parse()`. No FFI overhead (tree-sitter IS Rust/C).

**C.2** Tree-sitter query files (`queries/{python,java,typescript,go}/*.scm`) — Declarative S-expression patterns replace:
- Python: `ast.walk()` + isinstance checks (symbols.py lines 85-168)
- Java: 7 regex patterns `JAVA_*_RE` (symbols.py lines 171-207)
- TypeScript: 7 regex patterns `TS_*_RE`
- Go: 7 regex patterns `GO_*_RE`

Example for Python function extraction:
```scheme
(function_definition
  name: (identifier) @function.name
  parameters: (parameters) @function.params
  return_type: (type)? @function.return_type) @function.def
```

This is more correct (handles edge cases regex misses) and faster (C-native query engine).

**C.3** `symbols.rs` — Single `extract_symbols()` function loads the appropriate `.scm` query file per language, runs `tree_sitter::QueryCursor::matches()`, maps captures to `SymbolRecord` + `ImportRecord`.

**C.4** `callgraph.rs` — Tree-sitter queries for call site extraction (all languages), then the 7-level cascading resolution logic (`_resolve_targets`) ports directly as pure computation.

**C.5** `imports.rs` — Four per-language resolvers (`_resolve_python`, `_resolve_java`, `_resolve_typescript`, `_resolve_go`). String manipulation + HashMap lookup.

**C.6** `filesystem.rs` — Replace `os.walk()` + `fnmatch` with `ignore::WalkBuilder` (natively handles .gitignore, .bombeignore, hidden files).

**C.7** `pagerank.rs` — Power iteration on `HashMap<u32, Vec<u32>>` adjacency list. Trivial port.

**C.8** `pipeline.rs` — Replace `ProcessPoolExecutor` with Rayon:
```rust
let results: Vec<ExtractionResult> = files.par_iter()
    .map(|file| extract_file_worker(repo_root, file))
    .collect();
```
GIL released via `py.allow_threads()` during the entire parallel extraction.

**Verification**: `cargo test`, Python tests `test_parser.py`, `test_symbols.py`, `test_callgraph.py`, `test_imports.py`, `test_indexer.py`, `test_incremental.py`, `test_multilang_regression.py` pass. Golden output comparison: index test fixtures with both backends, diff symbol/edge sets.

---

### Phase D: Query Engines

**Goal**: Port all query engines (1,718 LOC + 299 LOC supporting modules) to Rust.

**D.1** `guards.rs` — Constants as `pub const`. Also exposed to Python via `#[pyfunction]`.

**D.2** `context.rs` (534 LOC) — The most complex engine:
- `_pick_seeds`: FTS5 MATCH + LIKE fallback SQL queries
- `_expand`: BFS to depth N, capped at `MAX_GRAPH_VISITED`
- `_personalized_pagerank`: 20 iterations of PPR with seed-biased restart
- `_topology_order`: BFS adjacency ordering
- Secret redaction: 4 regex patterns via `lazy_static!`
- Token budgeting: greedy inclusion with signature-only fallback

**D.3** `references.rs` (215 LOC) — BFS with 5 direction variants.

**D.4** `search.rs` (175 LOC) — FTS5 + LIKE dual-strategy. Hybrid scoring: `lexical * 0.55 + structural * 0.35 + semantic * 0.1`.

**D.5** `blast.rs` (108 LOC) — Reverse-edge BFS with risk assessment.

**D.6** `data_flow.rs` (178 LOC) — Bidirectional BFS.

**D.7** `change_impact.rs` (163 LOC) — BFS + type dependency lookup.

**D.8** `planner.rs` (110 LOC) — LRU+TTL cache with `parking_lot::Mutex`:
```rust
pub struct QueryPlanner {
    cache: Mutex<IndexMap<String, CacheEntry>>,
    max_entries: usize,
    ttl: Duration,
}
```

**D.9** Python thin wrappers: Each `src/bombe/query/*.py` delegates to Rust.

**Verification**: `cargo test`, all `test_query_*.py` tests pass, `test_mcp_contract.py` validates tools callable.

---

### Phase E: Sharding + Federation

**Goal**: Port catalog (458 LOC), router (234 LOC), resolver (250 LOC), federated planner (93 LOC), and executor (246 LOC).

**E.1** `catalog.rs` — rusqlite with 4 tables, 4 indexes. Same pattern as Database.

**E.2** `router.rs` — `parking_lot::Mutex<HashMap<String, Database>>` connection pool.

**E.3** `resolver.rs` — `resolve_cross_repo_imports` + `post_index_cross_repo_sync`.

**E.4** `federated/planner.rs` — 4 planning methods.

**E.5** `federated/executor.rs` — Parallel shard execution via Rayon:
```rust
let results: Vec<_> = plan.shard_ids.par_iter()
    .map(|sid| execute_on_shard(sid, &operation))
    .collect();
```

**Verification**: `cargo test`, all `test_shard_*.py` and `test_federated_*.py` tests pass.

---

### Phase F: Integration + Benchmarks + CI

**F.1** Ensure all Python imports resolve to Rust-backed implementations by default.

**F.2** Full test suite: `maturin develop && PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` — all 183+ tests pass.

**F.3** Rust test suite: `cargo test --workspace`.

**F.4** Comparative benchmarks (criterion):

| Operation | Python-only | Rust-backed | Expected Speedup |
|-----------|-------------|-------------|------------------|
| Full index (10K files) | ~30s | ~3-6s | 5-10x |
| Symbol search | ~50ms | ~10-25ms | 2-5x |
| Context assembly (8K tokens) | ~200ms | ~40-100ms | 2-5x |
| PageRank (50K nodes) | ~2s | ~200ms | 10x |
| Memory (indexing) | ~800MB | ~300-500MB | 30-50% less |

**F.5** CI pipeline: `cargo clippy`, `cargo test`, `maturin develop`, Python tests, `ruff check`.

**F.6** Fallback validation: `BOMBE_USE_PYTHON_CORE=1` runs full Python test suite.

**F.7** Update `CLAUDE.md` with new commands (`maturin develop`, `cargo test`, `cargo clippy`).

---

## Cross-Cutting Design Decisions

1. **PyO3 class design**: `#[pyclass(frozen)]` mirrors frozen dataclasses. Import paths unchanged.

2. **Error handling**: `BombeError` enum → `PyResult` via `impl From<BombeError> for PyErr`.

3. **GIL management**: `py.allow_threads(|| ...)` during Rayon parallel indexing, PageRank iterations, BFS traversals.

4. **Data passing**: PyO3 automatic conversions for simple types. `Vec<SymbolRecord>` with `#[pyclass]` for bulk data — no serialization overhead.

5. **Tree-sitter queries**: `.scm` files embedded at compile time via `include_str!()`. Self-contained binary.

6. **Fallback mode**: `BOMBE_USE_PYTHON_CORE=1` env var reverts to pure Python. Useful for debugging, unsupported platforms, gradual rollout.

7. **Backward compatibility**: `from bombe.store.database import Database` continues to work. Python wrapper modules re-export Rust classes at same paths.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Tree-sitter queries produce different symbols than ast/regex | Golden output test: index fixtures with both backends, diff symbol/edge sets |
| rusqlite schema compat with existing `.bombe/bombe.db` | Port DDL character-for-character; test with existing DB files |
| PyO3 type conversion overhead on hot path | Profile with `py-spy`; batch conversions (pass Vec not individual items) |
| maturin build complexity in CI | Use `PyO3/maturin-action` GitHub Action; pin Rust toolchain |
| Python code using `db.connect()` directly | Keep Python `connect()` available during migration; remove after |
| Cross-platform compilation | `rusqlite` bundled feature compiles SQLite from source; tree-sitter grammars likewise |

---

## Verification Protocol (Every Phase)

1. `cargo build` — compilation succeeds
2. `cargo clippy -- -D warnings` — no lint warnings
3. `cargo test --workspace` — Rust unit tests pass
4. `maturin develop` — installs native module into venv
5. `PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` — all Python tests pass
6. `python3 -m ruff check src tests` — Python lint clean
7. `BOMBE_USE_PYTHON_CORE=1 PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` — fallback works
