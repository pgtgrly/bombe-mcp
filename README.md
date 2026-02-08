# Bombe

Bombe is a structure-aware code retrieval MCP server for AI coding agents.
It indexes source code into a local graph and serves graph-aware MCP tools for symbol search, references, data flow, impact analysis, and token-budgeted context assembly.

## Core capabilities

- Local-first indexing and query execution with SQLite graph storage.
- Language support for Python, TypeScript, Java, and Go through tree-sitter extraction.
- Graph edges for calls, imports, and type relations.
- Query tools:
  - `search_symbols`
  - `get_references`
  - `get_context`
  - `get_structure`
  - `get_blast_radius`
  - `trace_data_flow`
  - `change_impact`
- Optional hybrid sync primitives:
  - async delta push and artifact pull
  - compatibility checks
  - artifact checksum validation
  - quarantine and circuit-breaker fallback
- Release governance gates over performance and workflow quality metrics.

## Architecture summary

Bombe is designed as local runtime first, with hybrid control-plane reuse as an additive feature.

- Local runtime:
  - file scanning and incremental indexing
  - SQLite graph (`files`, `symbols`, `edges`, FTS)
  - query engines for search/references/context/impact/flow
  - MCP tool handlers
- Hybrid sync modules:
  - `src/bombe/sync/client.py`: push/pull, timeout handling, compatibility policy, circuit breaker
  - `src/bombe/sync/reconcile.py`: promotion policy and touched-scope reconciliation
- Release governance:
  - `src/bombe/release/gates.py`: evaluates JSONL perf/workflow histories against hard thresholds

## Project layout

- `src/bombe/indexer`: parsing, extraction, import resolution, callgraph, pagerank, pipeline
- `src/bombe/query`: query backends for all MCP tools
- `src/bombe/store/database.py`: schema and migrations
- `src/bombe/tools/definitions.py`: MCP schemas and handler registry
- `src/bombe/sync`: hybrid sync client and reconciliation policies
- `src/bombe/release`: release gate evaluator
- `tests`: unit and integration tests
- `tests/perf`: perf and workflow gate suites
- `docs/plans`: implementation design documents
- `docs/runbooks`: operator playbooks

## Installation

Install runtime package:

```bash
python3 -m pip install .
```

Install with development tooling:

```bash
python3 -m pip install ".[dev]"
```

## Local development workflow

Compile checks:

```bash
PYTHONPATH=src python3 -m compileall src tests
```

Unit + integration tests:

```bash
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
```

Server initialization smoke test:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --init-only --log-level INFO
```

Start server (STDIO MCP runtime if `mcp` runtime is available):

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --log-level INFO
```

## Performance and workflow gate checks

Run perf suites and append metrics history:

```bash
BOMBE_RUN_PERF=1 PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
```

By default, metrics are appended to `/tmp/bombe-perf-history.jsonl`.
Use `BOMBE_PERF_HISTORY=/absolute/path/history.jsonl` to override.

Evaluate release gates from recorded history:

```bash
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.jsonl
```

Gate evaluator enforces thresholds for:

- `index` suite
- `incremental` suite
- `query` suite
- `workflow_gates` suite (flow trace, change impact, cross-module traversal, bug-triage context)

## MCP tool payload examples

`search_symbols`

```json
{"query":"auth", "kind":"function", "limit":20}
```

`get_references`

```json
{"symbol_name":"app.auth.authenticate", "direction":"both", "depth":2}
```

`get_context`

```json
{"query":"authenticate flow", "entry_points":["app.auth.authenticate"], "token_budget":1200}
```

`get_structure`

```json
{"path":".", "token_budget":4000, "include_signatures":true}
```

`get_blast_radius`

```json
{"symbol_name":"app.auth.authenticate", "change_type":"behavior", "max_depth":3}
```

`trace_data_flow`

```json
{"symbol_name":"app.auth.authenticate", "direction":"both", "max_depth":3}
```

`change_impact`

```json
{"symbol_name":"app.auth.authenticate", "change_type":"signature", "max_depth":3}
```

## Hybrid sync usage notes

Hybrid sync is currently implemented as internal modules and tests; local query execution remains the authoritative default path.

- Compatibility policy checks tool major version and schema versions before sync.
- Corrupt artifacts are quarantined and excluded from pulls.
- Circuit breaker opens on repeated push/pull failures and automatically recovers after reset timeout.
- Local fallback mode is explicit (`mode=local_fallback`) in sync results.

## Runbooks

- `docs/runbooks/local-only-mode.md`
- `docs/runbooks/hybrid-mode.md`
- `docs/runbooks/rollback-and-quarantine.md`

## CI pipeline

`.github/workflows/ci.yml` defines:

- lint and type check job
- unit and coverage job
- release-gates job:
  - runs all perf suites with `BOMBE_RUN_PERF=1`
  - evaluates JSONL history with `bombe.release.gates`

## Troubleshooting

- Tree-sitter parse issues:
  - verify supported file extensions and syntax validity.
- Empty query responses:
  - ensure indexing completed and symbols exist in SQLite.
- Slow perf locally:
  - compare current metrics with prior history JSONL before changing thresholds.
- Release gate failures:
  - inspect suite-specific metrics in history and rerun only affected perf tests first.

## Status

Active implementation toward hybrid, spec-complete MCP traversal/runtime with hard release gates.
