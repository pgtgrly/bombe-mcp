# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bombe is a structure-aware code retrieval MCP (Model Context Protocol) server for AI coding agents. It builds a SQLite graph index from source code using tree-sitter and serves graph-aware tools for navigation, discovery, and impact analysis. Python 3.11, local-first, zero external dependencies beyond SQLite and tree-sitter.

## Common Commands

All commands require `PYTHONPATH=src`.

```bash
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
bombe.query.* (query engines, all subject to guards.py guardrails)
    ↓
bombe.store.database (SQLite graph store with FTS, schema v6)
    ↑ populated by
bombe.indexer.pipeline (parallel extraction, incremental via git-diff)
    ├── parser.py (tree-sitter abstraction)
    ├── symbols.py (symbol extraction)
    ├── callgraph.py (call/import edge resolution)
    ├── imports.py (import parsing, module resolution)
    ├── filesystem.py (file scanning, language detection)
    └── pagerank.py (importance scoring)
```

### Key Modules

- **`server.py`** — CLI entry point. All subcommands: `serve`, `index-full`, `index-incremental`, `watch`, `status`, `diagnostics`, `doctor`, `preflight`.
- **`tools/definitions.py`** — MCP tool registration. Every tool has a JSON schema in `TOOL_SCHEMAS` and a handler function. To add a new tool: add schema, write handler, wire in `build_tool_handlers()`.
- **`query/guards.py`** — Shared guardrails. Search limit max 100, graph depth max 6, token budget max 32000, entry points max 32. All query engines import and apply these.
- **`query/planner.py`** — LRU query cache (512 entries, 15s TTL, thread-safe). Cache key = tool_name + version_token + normalized payload.
- **`store/database.py`** — SQLite schema with tables: `files`, `symbols`, `edges`, `external_deps`, `parameters`, `symbol_fts`. Observability tables for sync, diagnostics, metrics. Schema version 6 with migration framework.
- **`indexer/pipeline.py`** — Orchestrates full/incremental indexing with `ProcessPoolExecutor` for parallel file parsing. Incremental path uses git-diff to detect changed files.
- **`models.py`** — Shared dataclasses (`SymbolRecord`, `EdgeRecord`, etc.). Symbol identity uses qualified_name + file_path + line range + signature_hash for collision safety.
- **`sync/`** — Optional hybrid sync layer. Local index stays authoritative; sync adds remote push/pull with circuit breaker, artifact signing (HMAC-SHA256 or ED25519), and quarantine for corrupt artifacts.
- **`watcher/`** — Filesystem watch mode with git-diff detection and polling fallback.
- **`workspace.py`** — Multi-root workspace support via `.bombe/workspace.json`.
- **`ui_api/inspector.py`** — Web API for interactive graph exploration.

### Design Decisions

- **SQLite as graph store**: Relational structure for efficient graph traversal, ACID guarantees, zero external dependencies.
- **Tree-sitter for all languages**: Unified parsing (Java, Python, TypeScript, Go). Language-specific logic lives in tree-sitter query patterns, not in the graph layer.
- **PageRank scoring**: Differentiates entry points from leaf functions, enables token-budget-driven pruning for context assembly.
- **Parallel extraction with deterministic merge**: `ProcessPoolExecutor` for throughput; results normalized before DB insertion to ensure consistency.
- **All query engines share guardrails**: `guards.py` constants imported everywhere. No query can exceed bounds regardless of caller.

### Testing Patterns

Tests live in `tests/` (unit/integration) and `tests/perf/` (performance suites). Key contract: `test_mcp_contract.py` validates all tools are callable with correct schemas. Test files mirror source modules (e.g., `test_database.py` for `store/database.py`, `test_query_context.py` for `query/context.py`).

## Configuration

- `--repo` — Repository root (default `.`)
- `--db-path` — SQLite path (default `<repo>/.bombe/bombe.db`)
- `--runtime-profile` — `default` (graceful fallback) or `strict` (fail fast on missing parser)
- `.bombeignore` — Gitignore-format file exclusion patterns
- Ruff config: line-length 100, target Python 3.11 (in `pyproject.toml`)
