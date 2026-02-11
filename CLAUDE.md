# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bombe is a structure-aware code retrieval MCP (Model Context Protocol) server for AI coding agents. It builds a SQLite graph index from source code using tree-sitter and serves graph-aware tools for navigation, discovery, and impact analysis. The core engine is implemented in Rust (PyO3/maturin) with thin Python wrappers providing the public API. Python 3.11, local-first, zero external dependencies beyond SQLite and tree-sitter.

## Build Prerequisites

- Python 3.11+
- Rust stable toolchain (`rustup`)
- maturin (`pip install maturin`)

## Common Commands

All commands require `PYTHONPATH=src` and the Rust extension to be built.

```bash
# Build and install the Rust extension into the current Python env
maturin develop --manifest-path crates/bombe-core/Cargo.toml

# Run all unit tests
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"

# Run a single test file
PYTHONPATH=src python3 -W error -m unittest tests/test_database.py

# Run a single test case
PYTHONPATH=src python3 -W error -m unittest tests.test_database.TestDatabase.test_insert_symbol

# Lint
python3 -m ruff check src tests

# Type check
PYTHONPATH=src python3 -m mypy src tests

# Compile check
PYTHONPATH=src python3 -m compileall src tests

# Preflight (parser backend validation)
PYTHONPATH=src python3 -m bombe.server --repo . --runtime-profile strict preflight

# Performance suites (gated behind env var)
BOMBE_RUN_PERF=1 PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v

# Release gate evaluation
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.jsonl

# Test coverage (CI enforces 90% threshold)
PYTHONPATH=src python3 -m coverage run -m unittest discover -s tests -p "test_*.py"
python3 -m coverage report --fail-under=90
```

## Architecture

### Data Flow

```
AI Agent (Claude Code / Cursor / Copilot)
    ↓ MCP over STDIO
bombe.server (CLI entry point, command routing)
    ↓
bombe.tools.definitions (13 MCP tool schemas + handler wiring)
    ↓
bombe.query.* (Python wrappers with error handling + guardrails)
    ↓
_bombe_core (Rust PyO3 native extension — all query engines, store, indexer)
    ↓
SQLite graph store (FTS, schema v6)
    ↑ populated by
Rust indexer (parallel extraction via Rayon, incremental via git-diff)
    ├── tree-sitter parsing
    ├── symbol extraction
    ├── callgraph construction
    ├── import resolution
    └── PageRank scoring
```

### Key Modules

- **`server.py`** — CLI entry point. All subcommands: `serve`, `index-full`, `index-incremental`, `watch`, `status`, `diagnostics`, `doctor`, `preflight`.
- **`tools/definitions.py`** — MCP tool registration. Every tool has a JSON schema in `TOOL_SCHEMAS` and a handler function. To add a new tool: add schema, write handler, wire in `build_tool_handlers()`.
- **`query/guards.py`** — Shared guardrails (re-exported from Rust). Search limit max 100, graph depth max 6, token budget max 32000, entry points max 32. All query engines import and apply these.
- **`query/planner.py`** — LRU query cache (512 entries, 15s TTL, thread-safe). Cache key = tool_name + version_token + normalized payload.
- **`query/_error_handling.py`** — Shared `is_not_found()` utility for graceful handling of missing-symbol errors from Rust.
- **`query/references.py`**, **`query/blast.py`**, **`query/data_flow.py`**, **`query/change_impact.py`** — Python wrappers that delegate to `_bombe_core` Rust functions, translating field names and catching not-found errors.
- **`store/database.py`** — Python wrapper around Rust `Database` pyclass. SQLite schema with tables: `files`, `symbols`, `edges`, `external_deps`, `parameters`, `symbol_fts`. Schema version 6 with migration framework.
- **`store/sharding/catalog.py`** — Python wrapper around Rust `ShardCatalog` pyclass.
- **`models.py`** — PyO3 model classes re-exported from Rust (`SymbolRecord`, `EdgeRecord`, etc.). `model_to_dict()` and `model_replace()` provide dict/copy operations for frozen pyclasses.
- **`indexer/pipeline.py`** — Orchestrates full/incremental indexing via Rust backend. Incremental path uses git-diff to detect changed files.
- **`sync/`** — Optional hybrid sync layer. Local index stays authoritative; sync adds remote push/pull with circuit breaker, artifact signing (HMAC-SHA256 or ED25519), and quarantine for corrupt artifacts.
- **`watcher/`** — Filesystem watch mode with git-diff detection and polling fallback.
- **`workspace.py`** — Multi-root workspace support via `.bombe/workspace.json`.
- **`ui_api/inspector.py`** — Web API for interactive graph exploration.

### Rust Core (`crates/bombe-core`)

The `_bombe_core` native extension is the mandatory backend. All store, query, indexer, and sharding logic lives in Rust.

```
crates/bombe-core/
├── Cargo.toml          # PyO3 0.23, rusqlite 0.32 (bundled), tree-sitter 0.24
├── src/
│   ├── lib.rs          # #[pymodule] _bombe_core — registers all exports
│   ├── errors.rs       # BombeError enum (Database, Index, Query, Parse, Io, Sqlite, Json)
│   ├── models.rs       # 33 frozen pyclasses + constants + register_models()
│   ├── store/
│   │   ├── database.rs # Database pyclass with 38+ methods
│   │   ├── schema.rs   # Schema v7 DDL + migration framework
│   │   └── sharding/   # ShardCatalog, ShardRouter, cross_repo_resolver
│   ├── query/
│   │   ├── guards.rs   # Constants + clamping pyfunctions
│   │   ├── search.rs, references.rs, context.rs, blast.rs, etc.
│   │   ├── planner.rs  # QueryPlanner pyclass (LRU cache)
│   │   └── federated/  # FederatedQueryPlanner, FederatedQueryExecutor
│   └── indexer/
│       ├── symbols.rs  # Regex-based extraction (Java, TypeScript, Go)
│       ├── callgraph.rs # Call graph construction with 8-tier resolution
│       ├── pipeline.rs # Rayon parallel extraction
│       └── filesystem.rs, imports.rs, pagerank.rs, parser.rs, semantic.rs
```

### Error Handling

Rust errors map to Python exceptions:
- `BombeError::Query` ("Symbol not found: ...") -> `ValueError`
- `BombeError::Database` / `BombeError::Sqlite` -> `RuntimeError`
- `BombeError::Io` -> `IOError`

Python wrappers in `bombe.query.*` catch `ValueError` for not-found cases and return empty responses, ensuring MCP tools never crash on missing symbols.

### Design Decisions

- **Rust core via PyO3**: All compute-heavy logic (parsing, indexing, querying) runs in native Rust. Python provides the MCP server layer and thin API wrappers.
- **SQLite as graph store**: Relational structure for efficient graph traversal, ACID guarantees, zero external dependencies.
- **Tree-sitter for all languages**: Unified parsing (Java, Python, TypeScript, Go). Language-specific logic lives in tree-sitter query patterns, not in the graph layer.
- **PageRank scoring**: Differentiates entry points from leaf functions, enables token-budget-driven pruning for context assembly.
- **Parallel extraction with deterministic merge**: Rayon for throughput; results normalized before DB insertion to ensure consistency.
- **All query engines share guardrails**: `guards.py` constants imported everywhere. No query can exceed bounds regardless of caller.

### Testing Patterns

Tests live in `tests/` (unit/integration) and `tests/perf/` (performance suites). Key contract: `test_mcp_contract.py` validates all tools are callable with correct schemas. Error path tests in `test_query_error_paths.py` verify all query wrappers handle missing symbols gracefully. Test files mirror source modules (e.g., `test_database.py` for `store/database.py`, `test_query_context.py` for `query/context.py`).

## Configuration

- `--repo` — Repository root (default `.`)
- `--db-path` — SQLite path (default `<repo>/.bombe/bombe.db`)
- `--runtime-profile` — `default` (graceful fallback) or `strict` (fail fast on missing parser)
- `.bombeignore` — Gitignore-format file exclusion patterns
- Ruff config: line-length 100, target Python 3.11 (in `pyproject.toml`)
