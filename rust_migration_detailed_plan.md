# Bombe-MCP: Detailed Rust Migration Implementation Plan

## Context

The `rust_migration_plan.md` in the project root defines the high-level strategy: incrementally port CPU-bound Python modules (~6,600 LOC) to Rust via PyO3, keeping Python for I/O-bound orchestration (~6,000 LOC). This plan expands every phase into granular sub-tasks with exact function signatures, file paths, test mappings, and identifies which sub-tasks can be parallelized by concurrent sub-agents.

---

## Phase A: Foundation — Cargo + PyO3 + Models

**Goal**: Rust crate compiles, installs into venv via maturin, Python can `from bombe._bombe_core import SymbolRecord`. All existing tests pass via fallback.

### A.1: Cargo Workspace Setup
- Create `Cargo.toml` (workspace root) and `crates/bombe-core/Cargo.toml`
- All deps: pyo3 0.23, rusqlite 0.32 (bundled), tree-sitter 0.24, rayon 1.10, serde/serde_json, walkdir 2, ignore 0.4, sha2 0.10, regex 1.11, parking_lot 0.12, thiserror 2, tracing 0.1, indexmap 2
- `crate-type = ["cdylib"]`, `name = "_bombe_core"`

### A.2: PyO3 Module Scaffolding
- `crates/bombe-core/src/lib.rs` — `#[pymodule] fn _bombe_core` with model class registrations

### A.3: Error Types
- `crates/bombe-core/src/errors.rs` — `BombeError` enum (Database, Index, Query, Parse, Io) with `impl From<BombeError> for PyErr`

### A.4: Port All Dataclasses to `#[pyclass(frozen)]`
- `crates/bombe-core/src/models.rs`
- **Group 1** (Sub-agent 1): `FileRecord`(4f), `ParameterRecord`(4f), `SymbolRecord`(15f), `SymbolKey`(5f), `EdgeKey`(4f), `EdgeRecord`(8f), `EdgeContractRecord`(6f), `ExternalDepRecord`(4f), `ImportRecord`(5f), `ParsedUnit`(4f), `FileChange`(3f), `IndexStats`(8f)
- **Group 2** (Sub-agent 2): `SymbolSearchRequest`(4f), `ReferenceRequest`(4f), `ContextRequest`(5f), `StructureRequest`(3f), `BlastRadiusRequest`(3f), `SymbolSearchResponse`(2f), `ReferenceResponse`(1f), `ContextResponse`(1f), `BlastRadiusResponse`(1f)
- **Group 3** (Sub-agent 3): `GlobalSymbolURI`(3f + uri property + classmethods), `ShardInfo`(7f), `CrossRepoEdge`(5f), `ShardGroupConfig`(4f), `FederatedQueryResult`(6f), `WorkspaceRoot`(4f), `WorkspaceConfig`(3f), `FileDelta`(5f), `DeltaHeader`(6f), `QualityStats`(3f), `IndexDelta`(7f), `ArtifactBundle`(13f)
- Also: `_signature_hash()` and `_repo_id_from_path()` helpers (sha2)

### A.5: Maturin Build Integration
- Modify `pyproject.toml`: replace setuptools with maturin, add `[tool.maturin]` section with `module-name = "bombe._bombe_core"`

### A.6: Backend Selector
- Create `src/bombe/_backend.py` — tries `from bombe import _bombe_core`, sets `BACKEND = "rust"` or `"python"`. `BOMBE_USE_PYTHON_CORE=1` forces fallback.

### A.7: Type Stubs
- Create `src/bombe/_bombe_core.pyi` — stubs for every pyclass and pyfunction

### Parallelization
```
A.1 ──► A.2 ──► A.3 ◄── sequential foundation
                 │
    A.4 Group 1 ◄── Sub-agent 1 ─┐
    A.4 Group 2 ◄── Sub-agent 2  ├── parallel
    A.4 Group 3 ◄── Sub-agent 3 ─┘
    A.5, A.6, A.7 ◄── any agent (independent of A.4)
```
**Max parallel: 3 sub-agents** for A.4 model groups, plus A.5/A.6/A.7 independently.

### Tests
- `cargo build` + `maturin develop` + `python -c "from bombe._bombe_core import SymbolRecord"`
- All 183+ existing Python tests pass unchanged (fallback)

---

## Phase B: Store Layer — Database + Schema + Migrations

**Goal**: Port `store/database.py` (1,243 LOC, 44 methods) to Rust via rusqlite.

### B.1: Schema DDL
- `crates/bombe-core/src/store/schema.rs` — 14 CREATE TABLE + 18 CREATE INDEX + 2 FTS5 statements as `const &str` arrays. `SCHEMA_VERSION = 7`.

### B.2: Migration Framework
- Same file — port `migrate_to_v1` through `migrate_to_v7` with savepoint rollback pattern per step.

### B.3: Database Struct — 44 Methods
- `crates/bombe-core/src/store/database.rs`
- `#[pyclass(frozen)] pub struct Database { db_path: PathBuf }` — each method opens its own connection

**Sub-agent 1 — Schema/meta + diagnostics + backup** (16 methods):
- `init_schema`, `query`, `get_repo_meta`, `set_repo_meta`, `get_cache_epoch`, `bump_cache_epoch`
- `record_indexing_diagnostic`, `list_indexing_diagnostics`, `summarize_indexing_diagnostics`, `clear_indexing_diagnostics`
- `backup_to` (rusqlite backup API), `restore_from`
- Internal `connect() -> Connection`

**Sub-agent 2 — File/symbol CRUD + events + keys** (14 methods):
- `upsert_files`, `replace_file_symbols`, `replace_file_edges`, `replace_external_deps`, `delete_file_graph`, `rename_file`
- `record_sync_event`, `record_tool_metric`, `recent_tool_metrics`
- `set_trusted_signing_key`, `get_trusted_signing_key`, `list_trusted_signing_keys`

**Sub-agent 3 — Sync queue + artifacts + circuit breakers** (14 methods):
- `enqueue_sync_delta`, `list_pending_sync_deltas`, `mark_sync_delta_status`, `normalize_sync_queue_statuses`
- `set_artifact_pin`, `get_artifact_pin`, `quarantine_artifact`, `is_artifact_quarantined`, `list_quarantined_artifacts`
- `set_circuit_breaker_state`, `get_circuit_breaker_state`

### B.4: Python Thin Wrapper
- Modify `src/bombe/store/database.py` — add backend delegation at top, existing code stays as fallback.

### Parallelization
```
B.1 ──► B.2 ──► B.3 (3 sub-agents in parallel for method groups)
                B.4 (parallel with B.3)
```
**Max parallel: 3 sub-agents** for B.3 groups + 1 for B.4.

### Tests
- `cargo test` — Rust unit tests
- `test_database.py`(28), `test_database_v2_fts.py`(3), `test_database_diagnostics.py`(7), `test_database_schema_migration.py`(7), `test_sync_queue.py`(9), `test_artifact_pinning.py`(5), `test_circuit_breaker.py`(7), `test_tool_metrics.py`(7), `test_signing_keys.py`(5)

---

## Phase C: Indexer — Parser + Symbols + Call Graph + Pipeline

**Goal**: Port entire indexer (2,496 LOC) to Rust. ALL languages use tree-sitter queries — no more Python ast + regex.

### C.1: Native Tree-Sitter Parser
- `crates/bombe-core/src/indexer/parser.rs`
- `parse_file(path, language) -> ParsedUnit`, `tree_sitter_capability_report() -> HashMap`
- Maps "python"/"java"/"typescript"/"go" to tree-sitter grammars

### C.2: Tree-Sitter Query Files
- `crates/bombe-core/queries/{python,java,typescript,go}/{symbols,calls}.scm`
- Replaces all 19 regex patterns from `symbols.py` and `ast.walk()` logic
- Embedded at compile time via `include_str!()`
- **Sub-agent 1**: Python .scm queries
- **Sub-agent 2**: Java + Go .scm queries
- **Sub-agent 3**: TypeScript .scm queries

### C.3: Unified Symbol Extraction
- `crates/bombe-core/src/indexer/symbols.rs` — single `extract_symbols(source, language, file_path, tree)` using `QueryCursor`
- Replaces `_python_symbols()`(85L), `_java_symbols()`(100L), `_typescript_symbols()`(145L), `_go_symbols()`(133L)
- Also: `_parse_parameters`, `_normalize_type_name`, `_build_signature`, `_visibility`

### C.4: Call Graph Building
- `crates/bombe-core/src/indexer/callgraph.rs` — port 594 LOC
- `build_call_edges(parsed, file_symbols, candidate_symbols, symbol_id_lookup, semantic_hints) -> Vec<EdgeRecord>`
- Tree-sitter queries replace `_extract_python_calls` (ast.walk) and `_extract_regex_calls`
- 7-level `_resolve_targets` cascading: class-scoped → type hints → alias hints → receiver name → qualified substring → same-file → import-scoped → fallback
- Supporting: `CallSite`, `ReceiverHintBlock`, `symbol_id` (crc32), `import_hints`, `import_aliases`, `caller_for_line`, `receiver_types_for_call`, `lexical_receiver_type_hints`, `method_owner_name`, `type_name_tokens`

### C.5: Import Resolution
- `crates/bombe-core/src/indexer/imports.rs` — port 178 LOC
- `resolve_imports(repo_root, file_record, imports, files_map, file_id_lookup) -> (Vec<EdgeRecord>, Vec<ExternalDepRecord>)`
- 4 language resolvers: `_resolve_python`, `_resolve_java`, `_resolve_typescript`, `_resolve_go`

### C.6: Filesystem Walker
- `crates/bombe-core/src/indexer/filesystem.rs` — port 156 LOC
- `iter_repo_files(repo_root, include, exclude) -> Vec<PathBuf>` using `ignore::WalkBuilder`
- `compute_content_hash(path) -> String` using sha2
- `detect_language(path) -> Option<String>` extension-based

### C.7: PageRank
- `crates/bombe-core/src/indexer/pagerank.rs` — port 69 LOC
- `recompute_pagerank(db)` — power iteration, 20 iterations, damping 0.85, batch update

### C.8: Indexing Pipeline (Rayon)
- `crates/bombe-core/src/indexer/pipeline.rs` — port 656 LOC
- `full_index(py, repo_root, db, workers, include, exclude) -> IndexStats`
- `incremental_index(py, repo_root, db, changes, workers) -> IndexStats`
- Replace `ProcessPoolExecutor` with `files.par_iter().map(|f| extract_file_worker(f)).collect()` inside `py.allow_threads()`
- Supporting: `_ExtractionResult`, `_rebuild_dependencies`, `_scan_repo_files`, `_load_symbols`, `_current_files`, `_progress_snapshots`, `_diagnostic_category_and_hint`

### C.9: Semantic Receiver Type Hints
- `crates/bombe-core/src/indexer/semantic.rs` — port 139 LOC
- `load_receiver_type_hints(repo_root, relative_path) -> HashMap<(i32,String), HashSet<String>>`

### Parallelization
```
C.1 (parser)       ◄── Sub-agent A ─┐
C.2 Python .scm    ◄── Sub-agent B  │
C.2 Java+Go .scm   ◄── Sub-agent C  ├── ALL PARALLEL
C.2 TS .scm        ◄── Sub-agent D  │
C.5 (imports)      ◄── Sub-agent E  │
C.6 (filesystem)   ◄── Sub-agent A  │
C.7 (pagerank)     ◄── Sub-agent E  │
C.9 (semantic)     ◄── Sub-agent A ─┘
         │
         ▼ (C.2 complete)
C.3 (symbols.rs)   ◄── depends on C.2
C.4 (callgraph.rs) ◄── depends on C.2
         │
         ▼ (ALL above complete)
C.8 (pipeline.rs)  ◄── orchestrates C.1-C.7,C.9
```
**Max parallel: 5 sub-agents** (C.1/C.6/C.9, C.2-python, C.2-java/go, C.2-ts, C.5/C.7).

### Tests
- `test_parser.py`(5), `test_symbols.py`(12), `test_callgraph.py`(11), `test_imports.py`(8), `test_indexer.py`(10), `test_incremental.py`(7), `test_multilang_regression.py`(6), `test_filesystem.py`(5), `test_pagerank.py`(4)
- **Golden output test**: index fixtures with both backends, diff symbol/edge sets

---

## Phase D: Query Engines

**Goal**: Port all query engines (1,718 LOC + 299 LOC supporting) to Rust. **All engines are independent and can be ported in parallel.**

### D.1: Guards Constants + Helpers
- `crates/bombe-core/src/query/guards.rs` — 18 constants + 6 functions (`clamp_int`, `clamp_depth`, `clamp_budget`, `clamp_limit`, `truncate_query`, `adaptive_graph_cap`). Expose as `#[pyfunction]`.

### D.2: Context Assembly (535 LOC — largest engine)
- `crates/bombe-core/src/query/context.rs`
- `get_context(db, req) -> ContextResponse`
- Internal: `pick_seeds` (FTS5+LIKE), `expand` (BFS), `personalized_pagerank` (20-iter PPR), `topology_order` (BFS ordering), `quality_metrics`, `source_fragment`, `redact_sensitive_text` (4 regex patterns via lazy_static)
- Secret redaction: `sk-[A-Za-z0-9]{20,}`, `AKIA[0-9A-Z]{16}`, API key assignments, PEM blocks

### D.3: Reference Traversal (215 LOC)
- `crates/bombe-core/src/query/references.rs`
- `get_references(db, req) -> ReferenceResponse`
- Internal: `resolve_symbol_id`, `load_symbol`, `read_source`, `walk` (BFS with 5 directions: callers/callees/implementors/supers/both)

### D.4: Symbol Search + Hybrid Scoring (176 + 85 LOC)
- `crates/bombe-core/src/query/search.rs`
- `search_symbols(db, req) -> SymbolSearchResponse` — FTS5+LIKE dual strategy, hybrid ranking
- `rank_symbol(query, name, qname, sig, doc, pr, callers, callees) -> f64` — weights: lexical 0.55, structural 0.35, semantic 0.10
- Internal: `search_with_like`, `search_with_fts`, `count_refs`, `lexical_score`, `structural_score`, `semantic_score`

### D.5: Blast Radius (108 LOC)
- `crates/bombe-core/src/query/blast.rs`
- `get_blast_radius(db, req) -> BlastRadiusResponse` — reverse-edge BFS + risk assessment

### D.6: Data Flow Tracing (178 LOC)
- `crates/bombe-core/src/query/data_flow.rs`
- `trace_data_flow(db, symbol_name, direction, max_depth) -> HashMap` — bidirectional BFS

### D.7: Change Impact (163 LOC)
- `crates/bombe-core/src/query/change_impact.rs`
- `change_impact(db, symbol_name, change_type, max_depth) -> HashMap` — BFS + EXTENDS/IMPLEMENTS lookup

### D.8: Query Planner Cache (110 LOC)
- `crates/bombe-core/src/query/planner.rs`
- `#[pyclass(frozen)] QueryPlanner { cache: Mutex<IndexMap<String, CacheEntry>>, max_entries, ttl }`
- Methods: `get_or_compute`, `get_or_compute_with_trace`, `stats`

### D.9: Tokenizer (39 LOC) + Structure (65 LOC)
- `crates/bombe-core/src/query/tokenizer.rs` — `estimate_tokens(text) -> i32`
- `crates/bombe-core/src/query/structure.rs` — `get_structure(db, req) -> HashMap`

### D.10: Python Thin Wrappers
- Modify all `src/bombe/query/*.py` files to delegate to Rust backend

### Parallelization
```
D.1 (guards+D.8+D.9)   ◄── Sub-agent 1 ─┐
D.2 (context)           ◄── Sub-agent 2   │ ALL ENGINES ARE
D.3 (references+D.9)    ◄── Sub-agent 3   │ INDEPENDENT —
D.4 (search+hybrid)     ◄── Sub-agent 4   │ MAX PARALLELISM
D.5+D.6+D.7 (blast/flow/impact) ◄── Sub-agent 5 ─┘
D.10 (Python wrappers)  ◄── Sub-agent 6 (parallel)
```
**Max parallel: 5-6 sub-agents.** D.2 is largest; should be dedicated agent.

### Tests
- `test_query_context.py`(8), `test_query_references.py`(8), `test_query_search.py`(7), `test_query_blast.py`(5), `test_query_data_flow.py`(6), `test_query_change_impact.py`(6), `test_query_planner.py`(8), `test_query_hybrid.py`(7), `test_query_guards.py`(6), `test_mcp_contract.py`(12)

---

## Phase E: Sharding + Federation

**Goal**: Port catalog (459 LOC), router (235 LOC), resolver (251 LOC), federated planner (93 LOC), executor (246 LOC).

### E.1: Shard Catalog
- `crates/bombe-core/src/store/sharding/catalog.rs`
- `#[pyclass(frozen)] ShardCatalog { db_path: PathBuf }`
- Schema: 4 tables (`catalog_meta`, `shards`, `cross_repo_edges`, `exported_symbols`), 5 indexes
- ~20 methods: `init_schema`, `register_shard`, `unregister_shard`, `list_shards`, `get_shard`, `update_shard_stats`, `upsert_cross_repo_edges`, `get_cross_repo_edges_from/to`, `delete_cross_repo_edges_for_repo`, `refresh_exported_symbols`, `search_exported_symbols`, `resolve_external_import`, `query`

### E.2: Shard Router
- `crates/bombe-core/src/store/sharding/router.rs`
- `#[pyclass(frozen)] ShardRouter { catalog, pool: Mutex<HashMap<String, Database>> }`
- `get_shard_db` (pooling+eviction), `route_symbol_query`, `route_reference_query`, `all_shard_ids`, `shard_health`, `close_all`

### E.3: Cross-Repo Resolver
- `crates/bombe-core/src/store/sharding/resolver.rs`
- `compute_repo_id(repo_root) -> String` (sha256[:16])
- `resolve_cross_repo_imports(catalog, repo_id, shard_db) -> Vec<CrossRepoEdge>`
- `post_index_cross_repo_sync(repo_root, db, catalog) -> HashMap` (7-step workflow)

### E.4: Federated Query Planner
- `crates/bombe-core/src/query/federated/planner.rs`
- `FederatedQueryPlan { shard_ids, fan_out_strategy, cross_repo_edges }`
- `plan_search`, `plan_references`, `plan_blast_radius`, `plan_context`

### E.5: Federated Query Executor
- `crates/bombe-core/src/query/federated/executor.rs`
- `execute_search`, `execute_references`, `execute_blast_radius`
- Parallel shard execution via Rayon inside `py.allow_threads()`
- Internal: `execute_on_shard`, `search_on_shard`, `references_on_shard`, `blast_on_shard`, `merge_reference_results`

### E.6: Python Thin Wrappers
- Modify all sharding/federation Python files for backend delegation

### Parallelization
```
E.1 (catalog)    ◄── Sub-agent 1 ─┐
E.2 (router)     ◄── Sub-agent 2  ├── parallel
E.3 (resolver)   ◄── Sub-agent 3 ─┘
E.4 (fed planner) ◄── after E.1/E.2 types exist
E.5 (fed executor) ◄── after E.4
E.6 (wrappers)    ◄── parallel with E.4/E.5
```
**Max parallel: 3 sub-agents** for E.1/E.2/E.3.

### Tests
- `test_shard_catalog.py`(12), `test_shard_router.py`(6), `test_cross_repo_resolver.py`(7), `test_federated_planner.py`(5), `test_federated_executor.py`(6), `test_federated_search.py`(4), `test_federated_references.py`(4)

---

## Phase F: Integration + Benchmarks + CI

**Goal**: Wire everything together, full test suite, benchmarks, CI.

### F.1: Complete lib.rs Registration
- Register ALL classes (30+ models, Database, ShardCatalog, ShardRouter, QueryPlanner, FederatedQueryPlanner, FederatedQueryExecutor) and ALL functions (query engines, guards, indexer entry points)

### F.2: Update models.py Re-exports
- `src/bombe/models.py` — re-export from `_bombe_core` when Rust backend active

### F.3: Complete Type Stubs
- `src/bombe/_bombe_core.pyi` — full stubs for everything

### F.4: Rust Test Suite
- `cargo test --workspace` + `cargo clippy -- -D warnings` + `cargo fmt --check`

### F.5: Full Python Test Suite
- `maturin develop && PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` — all 183+ pass

### F.6: Fallback Validation
- `BOMBE_USE_PYTHON_CORE=1` — all tests pass with pure Python

### F.7: Benchmarks (criterion)
- Full index 10K files: 30s→3-6s (5-10x)
- Symbol search: 50ms→10-25ms (2-5x)
- Context 8K tokens: 200ms→40-100ms (2-5x)
- PageRank 50K nodes: 2s→200ms (10x)

### F.8: CI Pipeline
- cargo clippy, cargo test, maturin develop, Python tests, ruff check, fallback test

### F.9: Update CLAUDE.md
- Add `maturin develop`, `cargo test`, `cargo clippy`, `cargo bench`

### Parallelization
```
F.1+F.2+F.3 ◄── Sub-agent 1 (integration wiring)
F.7+F.8     ◄── Sub-agent 2 (benchmarks + CI)
F.9         ◄── Sub-agent 3
F.4+F.5+F.6 ◄── sequential after F.1-F.3
```

---

## Phase Dependency Graph

```
Phase A ──► Phase B ──┬──► Phase C (Indexer)    ──┐
                      │                            ├──► Phase E ──► Phase F
                      └──► Phase D (Query Engines) ┘
```
**C and D can run in parallel** — both depend only on B. Up to 11 simultaneous sub-agents during C+D.

## Aggregate Parallelization

| Phase | Sub-tasks | Max Parallel Agents | Key Constraint |
|-------|-----------|-------------------|----------------|
| A | 7 | 3-4 | Model groups |
| B | 5 | 3-4 | Method groups |
| C | 9 | 5 | Query files per language |
| D | 11 | 5-6 | ALL engines independent |
| C+D | 20 | 11 | C and D run simultaneously |
| E | 6 | 3 | E.5 depends on E.4 |
| F | 9 | 3 | Integration needs prior phases |

## Verification Protocol (Every Phase)

1. `cargo build`
2. `cargo clippy -- -D warnings`
3. `cargo test --workspace`
4. `maturin develop`
5. `PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"`
6. `python3 -m ruff check src tests`
7. `BOMBE_USE_PYTHON_CORE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"`

## Critical Files

**Rust (create)**: `lib.rs`, `errors.rs`, `models.rs`, `store/{database,schema}.rs`, `store/sharding/{catalog,router,resolver}.rs`, `indexer/{pipeline,parser,symbols,callgraph,imports,filesystem,pagerank,semantic}.rs`, `query/{context,references,search,blast,data_flow,change_impact,guards,planner,tokenizer,structure}.rs`, `query/federated/{planner,executor}.rs`, `queries/{python,java,typescript,go}/{symbols,calls}.scm`

**Python (modify)**: `pyproject.toml`, `_backend.py`(new), `_bombe_core.pyi`(new), `models.py`, `store/database.py`, `store/sharding/{catalog,router,cross_repo_resolver}.py`, `query/{context,references,search,blast,data_flow,change_impact,planner,guards}.py`, `query/federated/{planner,executor}.py`, `CLAUDE.md`

**Python (unchanged)**: `server.py`, `tools/definitions.py`, `sync/*`, `plugins/*`, `workspace.py`, `watcher/*`, `control_plane/*`, `ui_api/*`, `lsp/*`
