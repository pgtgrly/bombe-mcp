# Bombe

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Local-first](https://img.shields.io/badge/runtime-local--first-success)
![MCP](https://img.shields.io/badge/protocol-MCP-informational)

Bombe is a structure-aware code retrieval MCP server for AI coding agents.
It builds a local graph index from source code, then serves graph-aware tools for:

- Symbol discovery
- Caller/callee/reference traversal
- Data-flow tracing
- Change-impact estimation
- Token-budgeted context assembly

The runtime is local-first and works without any control plane. Optional hybrid sync modules are included for delta/artifact exchange when shared intelligence is needed.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Environment Variables](#environment-variables)
- [MCP Tools](#mcp-tools)
- [Architecture](#architecture)
- [Indexing Model](#indexing-model)
- [Hybrid Sync Model](#hybrid-sync-model)
- [Performance and Release Gates](#performance-and-release-gates)
- [Repository Layout](#repository-layout)
- [Runbooks](#runbooks)
- [Development Workflow](#development-workflow)
- [Spec Roadmap](#spec-roadmap)
- [Verification Snapshot](#verification-snapshot)
- [Troubleshooting and Limitations](#troubleshooting-and-limitations)
- [Status](#status)

## Features

- Local-first graph indexing with SQLite
- Multi-language symbol extraction (Python, TypeScript, Java, Go)
- Call/import/type dependency edges for structural traversal
- Strict MCP tool schemas with contract tests
- Incremental indexing support (git + non-git fallback) and perf trend tracking
- Persistent parse/index diagnostics with per-run summaries
- Include/exclude indexing filters plus `.bombeignore` support
- Parallel extraction path with deterministic merge + throughput telemetry
- Sensitive-path exclusion and context redaction safeguards
- Release governance checks for latency and workflow quality gates

## Requirements

- Python `>=3.11`
- A local checkout of the repository to index
- Write access to the configured DB location

## Install

Install package:

```bash
python3 -m pip install .
```

Developer install:

```bash
python3 -m pip install ".[dev]"
```

## Quick Start

Initialize storage:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --init-only --log-level INFO
```

Run a full index:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . index-full
```

Start server (STDIO MCP runtime when `mcp` is available):

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --log-level INFO
```

Check runtime health:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . doctor
```

## CLI Reference

### Global arguments

| Argument | Description | Default |
|---|---|---|
| `--repo` | Repository root to index | `.` |
| `--db-path` | Explicit SQLite path | `<repo>/.bombe/bombe.db` |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `--init-only` | Initialize storage and exit | `false` |
| `--hybrid-sync` | Enable post-index sync cycle | `false` |
| `--control-plane-root` | File-backed control-plane root | `<repo>/.bombe/control-plane` |
| `--sync-timeout-ms` | Sync push/pull timeout budget | `500` |
| `--runtime-profile` | Runtime policy (`default` or strict hard-fail mode) | `default` |
| `--diagnostics-limit` | Max diagnostics rows for `status`, `doctor`, `diagnostics` | `50` |
| `--include` | Optional include glob (repeatable) | `[]` |
| `--exclude` | Optional exclude glob (repeatable) | `[]` |

### Subcommands

| Command | Purpose | Key options |
|---|---|---|
| `serve` | Start MCP server runtime | `--index-mode none|full|incremental` |
| `index-full` | Run full index and exit (JSON stats) | `--workers` |
| `index-incremental` | Run incremental index from git status or filesystem snapshot (JSON stats) | `--workers` |
| `watch` | Loop incremental indexing (JSON summary) | `--max-cycles`, `--poll-interval-ms`, `--watch-mode`, `--debounce-ms`, `--max-change-batch` |
| `status` | Print index/sync status JSON and exit | - |
| `diagnostics` | Print parse/index diagnostics JSON and exit | `--run-id`, `--stage`, `--severity` |
| `doctor` | Run health checks (JSON report) | `--fix` |
| `preflight` | Run startup compatibility checks (JSON report) | `--runtime-profile` |

### Command examples

Full index:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo index-full
```

Incremental index:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo index-incremental
```

Incremental index with filters:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo --include "src/**/*.py" --exclude "*test*" index-incremental
```

Hybrid full index + sync:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo --hybrid-sync index-full
```

Serve with incremental warmup:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo --hybrid-sync serve --index-mode incremental
```

Watch mode (single cycle):

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo watch --max-cycles 1 --poll-interval-ms 500
```

Watch mode with filesystem events when available:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo watch --watch-mode fs --max-cycles 1
```

Diagnostics for the latest runs:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo diagnostics --severity error --diagnostics-limit 100
```

Doctor with safe auto-remediation:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo doctor --fix
```

Strict-profile preflight (fails fast when required parser backends are unavailable):

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo --runtime-profile strict preflight
```

## Environment Variables

| Variable | Description |
|---|---|
| `BOMBE_RUN_PERF=1` | Enables perf suites in `tests/perf` |
| `BOMBE_PERF_HISTORY=/abs/path/file.jsonl` | Metrics history output for perf suites and release gates |
| `BOMBE_SYNC_SIGNING_KEY=...` | Enables hybrid artifact signing/verification |
| `BOMBE_SYNC_SIGNING_ALGO=hmac-sha256\|ed25519` | Signature algorithm (`ed25519` requires `cryptography`) |
| `BOMBE_SYNC_KEY_ID=...` | Artifact signing key identifier |
| `BOMBE_REAL_REPO_PATHS=/path/repo1,/path/repo2` | Optional real-repo perf/eval coverage |
| `BOMBE_SEMANTIC_HINTS_FILE=/abs/semantic-hints.json` | Optional semantic receiver-type hints for call resolution |
| `BOMBE_REQUIRE_TREE_SITTER=1` | Internal strict parser switch (normally set via `--runtime-profile strict`) |
| `BOMBE_EXCLUDE_SENSITIVE=0` | Disables default sensitive-path exclusion (not recommended) |

## MCP Tools

Available tools:

- `search_symbols`
- `get_references`
- `get_context`
- `get_structure`
- `get_blast_radius`
- `trace_data_flow`
- `change_impact`
- `get_indexing_diagnostics`
- `get_server_status`
- `estimate_context_size`
- `get_context_summary`
- `get_entry_points`
- `get_hot_paths`
- `get_orphan_symbols`

### Input examples

`search_symbols`

```json
{"query":"auth","kind":"function","limit":20}
```

`get_references`

```json
{"symbol_name":"app.auth.authenticate","direction":"both","depth":2}
```

`get_context`

```json
{"query":"authenticate flow","entry_points":["app.auth.authenticate"],"token_budget":1200}
```

`get_structure`

```json
{"path":".","token_budget":4000,"include_signatures":true}
```

`get_blast_radius`

```json
{"symbol_name":"app.auth.authenticate","change_type":"behavior","max_depth":3}
```

`trace_data_flow`

```json
{"symbol_name":"app.auth.authenticate","direction":"both","max_depth":3}
```

`change_impact`

```json
{"symbol_name":"app.auth.authenticate","change_type":"signature","max_depth":3}
```

`get_indexing_diagnostics`

```json
{"run_id":"<optional_run_id>","stage":"parse","severity":"error","limit":50}
```

`get_server_status`

```json
{"diagnostics_limit":20,"metrics_limit":20}
```

`estimate_context_size`

```json
{"query":"authenticate flow","entry_points":["app.auth.authenticate"],"token_budget":1200}
```

`get_context_summary`

```json
{"query":"authenticate flow","entry_points":["app.auth.authenticate"],"token_budget":1200}
```

`get_entry_points`

```json
{"limit":20}
```

`get_hot_paths`

```json
{"limit":20}
```

`get_orphan_symbols`

```json
{"limit":50}
```

All dict-returning tools also accept:

```json
{"include_explanations":true}
```

When enabled, responses include an `explanations` section with reasoning metadata.

All tools also accept:

```json
{"include_plan":true}
```

When enabled, dict responses include `planner_trace` metadata (cache mode, lookup/compute timing, cache epoch token).

### Contract validation

Strict contract behavior is verified by:

- `tests/test_mcp_contract.py`

## Architecture

Bombe is split into local runtime modules and optional hybrid modules.

### Local runtime

- File scanning and language detection
- Parser plus symbol extraction plus call/import resolution
- Optional semantic receiver-type hints merged into call resolution
- SQLite graph storage
  - `files`
  - `symbols`
  - `edges`
  - `external_deps`
  - FTS virtual table `symbol_fts`
- Query engines
  - search
  - references
  - context assembly
  - blast radius
  - data flow
  - change impact
- MCP tool registration and handler wiring
- Query planner cache layer for repeated payloads
- Tokenizer abstraction with optional model-aware token counting (`tiktoken` if installed)

### Hybrid modules

- `src/bombe/sync/client.py`
  - compatibility checks
  - async push/pull
  - timeout budgets
  - circuit breaker
  - checksum validation
  - optional artifact signature verification
  - quarantine
- `src/bombe/sync/reconcile.py`
  - promotion policy gates
  - touched-scope merge precedence
- `src/bombe/sync/transport.py`
  - file-backed control-plane transport for local hybrid deployments
- `src/bombe/sync/orchestrator.py`
  - delta construction from local graph state
  - persisted sync queue status updates
  - pull plus reconcile plus artifact pin flow

### Release governance

`src/bombe/release/gates.py` evaluates recorded suite metrics against hard thresholds and returns pass/fail.

### Observability and persisted state

SQLite schema version `6` includes state for operations and diagnostics:

- `sync_queue`
- `artifact_quarantine`
- `artifact_pins`
- `circuit_breakers`
- `sync_events`
- `tool_metrics`
- `indexing_diagnostics`
- `migration_history`
- `trusted_signing_keys`
- Repo metadata key `cache_epoch` for query cache invalidation

## Indexing Model

High-level pipeline:

1. Walk repository files and detect supported languages.
2. Parse files and extract symbols/imports.
3. Resolve imports and call edges.
4. Persist symbols/edges/dependencies into SQLite.
5. Recompute rank features used by query layers.

Incremental path updates only changed files, then rebuild impacted graph state.

### Guardrails and safety limits

Runtime payload guardrails are enforced for depth, limits, and query lengths:

- Search limit clamped to max `100`
- Graph depth clamped to max `6`
- Context token budget clamped to max `32000`
- Entry points capped at `32`
- Traversal node/edge caps prevent runaway expansion
- Adaptive traversal caps scale by repository symbol count for large-repo memory safety

These limits are defined in `src/bombe/query/guards.py`.

## Hybrid Sync Model

Hybrid sync is additive and does not replace local query serving.

- Local path remains authoritative and available.
- Incompatible artifacts are rejected.
- Corrupt artifacts are quarantined.
- Signature mismatches are quarantined when signing is configured.
- Trusted key policy can be persisted per repository in `trusted_signing_keys` for verification key selection.
- Repeated remote failures open the circuit breaker.
- Results explicitly expose fallback mode (`local_fallback`) when remote operations are skipped or fail.
- Sync outcomes are persisted in SQLite (`sync_queue`, `sync_events`, `artifact_pins`, `circuit_breakers`).

### Backup and restore

Database backup/restore helpers are available on `Database`:

```python
from pathlib import Path
from bombe.store.database import Database

db = Database(Path("/abs/repo/.bombe/bombe.db"))
backup_path = db.backup_to(Path("/tmp/bombe-backup.db"))
db.restore_from(backup_path)
```

## Performance and Release Gates

Run perf suites:

```bash
BOMBE_RUN_PERF=1 PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
```

Run optional real-repo evaluation (OpenSearch/Kubernetes-style local checkouts):

```bash
BOMBE_RUN_PERF=1 BOMBE_REAL_REPO_PATHS=/abs/opensearch,/abs/kubernetes PYTHONPATH=src python3 -m unittest tests.perf.test_real_repo_eval -v
```

Evaluate release gates:

```bash
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.jsonl
```

Current gate families:

- `index`
- `incremental`
- `query`
- `workflow_gates`
- `gold_eval`

Thresholds are defined in `src/bombe/release/gates.py`.

## Repository Layout

- `src/bombe/indexer`: scanning, parsing, extraction, imports, callgraph, ranking, pipeline
- `src/bombe/query`: tool backends
- `src/bombe/store`: SQLite schema and migrations
- `src/bombe/tools`: MCP definitions and schemas
- `src/bombe/sync`: hybrid sync client and reconcile logic
- `src/bombe/release`: release gate evaluator
- `tests`: unit and integration tests
- `tests/perf`: perf suites and workflow harness
- `tests/perf/real_repo_harness.py`: env-driven real-repo evaluation harness
- `docs/plans`: implementation design docs
- `docs/runbooks`: operator playbooks

## Runbooks

- `docs/runbooks/local-only-mode.md`
- `docs/runbooks/hybrid-mode.md`
- `docs/runbooks/rollback-and-quarantine.md`

## Development Workflow

Compile:

```bash
PYTHONPATH=src python3 -m compileall src tests
```

Run test suite:

```bash
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
```

Smoke initialize server:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --init-only --log-level INFO
```

CI jobs in `.github/workflows/ci.yml`:

- `lint-and-typecheck`
- `test`
- `release-gates`

Recommended local all-check run:

```bash
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
BOMBE_RUN_PERF=1 PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.jsonl
```

## Spec Roadmap

Implementation is tracked as nine execution phases so agents can audit progress against spec outcomes:

1. Contract and identity foundation
2. Local call-resolution precision
3. Collision-safe symbol/edge identity mapping
4. Migration framework and persisted state
5. Query guardrails and quality metrics
6. Hybrid sync protocol and reconciliation
7. Server lifecycle commands and operational status
8. Workflow benchmark gates and release gate integration
9. Observability, runbooks, and operator readiness

Detailed execution plan:

- `docs/plans/2026-02-08-spec-completion-9-phase-execution-plan.md`

## Verification Snapshot

Latest local verification run (2026-02-08):

- `PYTHONPATH=src python3 -m compileall src tests` -> pass
- `PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` -> pass (`100` tests)
- `BOMBE_RUN_PERF=1 BOMBE_PERF_HISTORY=/tmp/bombe-perf-history.final.jsonl PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v` -> pass (`6` tests, `1` skipped when `BOMBE_REAL_REPO_PATHS` is unset)
- `PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.final.jsonl` -> `RELEASE_GATES=PASS`

## Troubleshooting and Limitations

### Troubleshooting

- Parse/extraction misses
  - Verify file extension and syntax
  - Confirm file is under indexed repo path
- Empty query output
  - Verify indexing completed and symbols exist in DB
- Gate failure
  - Inspect perf history JSONL
  - Rerun only the failing suite
  - Evaluate again with `bombe.release.gates`

### Known limitations

- Static call resolution can miss dynamic dispatch and reflection-heavy patterns.
- Precision/recall in highly dynamic codebases depends on semantic signal quality.
- Hybrid sync primitives are implemented, but deployment topology for shared control plane remains operator-specific.

## Status

Active implementation toward a hybrid, spec-complete MCP traversal/runtime with hard release gates and operator runbooks.
