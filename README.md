# Bombe

Bombe is a structure-aware code retrieval MCP server for AI coding agents.
It builds a local graph index from source code, then serves graph-aware MCP tools for:

- symbol discovery
- caller/callee/reference traversal
- data flow tracing
- change impact estimation
- token-budgeted context assembly

The runtime is local-first and works without a control plane. Hybrid sync modules are included for delta/artifact exchange when shared intelligence is desired.

## Table of contents

- What Bombe provides
- Requirements
- Quick start
- CLI and environment reference
- Spec completion roadmap
- Architecture
- Indexing model
- MCP tools and response contracts
- Hybrid sync model
- Performance and release gates
- Repository layout
- Runbooks
- Development workflow
- Troubleshooting and limitations

## What Bombe provides

- Local-first graph indexing with SQLite.
- Multi-language symbol extraction (Python, TypeScript, Java, Go).
- Call/import/type dependency edges for structural traversal.
- Strict MCP tool schemas with contract tests.
- Incremental indexing support and perf trend tracking.
- Release-governance checks for latency and workflow quality gates.

## Requirements

- Python `>=3.11`
- A local checkout of the repository to index
- Write access to the configured DB location

## Quick start

Install:

```bash
python3 -m pip install .
```

Developer install:

```bash
python3 -m pip install ".[dev]"
```

Initialize storage:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --init-only --log-level INFO
```

Start server (STDIO MCP runtime when `mcp` runtime package is available):

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --log-level INFO
```

## CLI and environment reference

### Global CLI arguments

- `--repo`: repository root to index (default: `.`)
- `--db-path`: explicit SQLite path (default: `<repo>/.bombe/bombe.db`)
- `--log-level`: `DEBUG|INFO|WARNING|ERROR` (default: `INFO`)
- `--init-only`: initialize storage and exit
- `--hybrid-sync`: enable post-index sync cycle (push/pull/reconcile)
- `--control-plane-root`: file-backed control-plane root (default: `<repo>/.bombe/control-plane`)
- `--sync-timeout-ms`: sync push/pull timeout budget in ms (default: `500`)

### Subcommands

- `serve`: start MCP server runtime.
  - `--index-mode none|full|incremental`: optional pre-serve index action.
- `index-full`: run full index and exit (prints JSON stats).
- `index-incremental`: run incremental index from git diff and exit (prints JSON stats).
- `watch`: poll git changes and run incremental indexing loop (prints JSON summary).
- `status`: print index/sync status JSON and exit.
- `doctor`: run health checks for schema/runtime/writability/tool registry (prints JSON report).
  - `--fix`: apply safe auto-remediation for schema/queue/cache meta issues.

### Command examples

Full index:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo index-full
```

Incremental index:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo index-incremental
```

Hybrid full index + sync:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo --hybrid-sync index-full
```

Serve with incremental warmup:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo --hybrid-sync serve --index-mode incremental
```

Status:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo status
```

Watch mode (single cycle example):

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo watch --max-cycles 1 --poll-interval-ms 500
```

Watch mode with filesystem events when available:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo watch --watch-mode fs --max-cycles 1
```

Doctor:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /abs/repo doctor
```

### Environment variables

- `BOMBE_RUN_PERF=1`: enables perf suites in `tests/perf`
- `BOMBE_PERF_HISTORY=/absolute/path/file.jsonl`: metrics history output for perf suites and release gate evaluation
- `BOMBE_SYNC_SIGNING_KEY=your-shared-secret`: enables HMAC signing/verification for hybrid artifacts
- `BOMBE_SYNC_SIGNING_ALGO=hmac-sha256|ed25519`: selects artifact signature algorithm (`ed25519` requires `cryptography`)
- `BOMBE_SYNC_KEY_ID=key-identifier`: sets artifact signing key identifier
- `BOMBE_REAL_REPO_PATHS=/path/to/repo1,/path/to/repo2`: enables optional real-repo perf/eval test coverage
- `BOMBE_SEMANTIC_HINTS_FILE=/abs/semantic-hints.json`: optional semantic receiver type hints for call resolution

## Spec completion roadmap

The implementation is tracked as nine execution phases so agents can audit progress against spec outcomes:

1. Contract and identity foundation.
2. Local call-resolution precision.
3. Collision-safe symbol/edge identity mapping.
4. Migration framework and persisted state.
5. Query guardrails and quality metrics.
6. Hybrid sync protocol and reconciliation.
7. Server lifecycle commands and operational status.
8. Workflow benchmark gates and release gate integration.
9. Observability, runbooks, and operator readiness.

Detailed execution plan:

- `docs/plans/2026-02-08-spec-completion-9-phase-execution-plan.md`

## Architecture

Bombe is split into local runtime modules and optional hybrid modules.

### Local runtime

- File scanning and language detection
- Parser + symbol extraction + call/import resolution
- Optional semantic receiver-type hints merged into call resolution
- SQLite graph storage:
  - `files`
  - `symbols`
  - `edges`
  - `external_deps`
  - FTS virtual table (`symbol_fts`)
- Query engines:
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

- `src/bombe/sync/client.py`:
  - compatibility checks
  - async push/pull
  - timeout budgets
  - circuit breaker
  - checksum validation
  - optional HMAC artifact signature verification
  - quarantine
- `src/bombe/sync/reconcile.py`:
  - promotion policy gates
  - touched-scope merge precedence
- `src/bombe/sync/transport.py`:
  - file-backed control-plane transport for local hybrid deployments
- `src/bombe/sync/orchestrator.py`:
  - delta construction from local graph state
  - persisted sync queue status updates
  - pull + reconcile + artifact pin flow

### Release governance

- `src/bombe/release/gates.py` evaluates recorded suite metrics against hard thresholds and returns pass/fail.

### Observability and persisted state

SQLite schema version `5` includes state for operations and diagnostics:

- `sync_queue`
- `artifact_quarantine`
- `artifact_pins`
- `circuit_breakers`
- `sync_events`
- `tool_metrics`
- `migration_history`
- `trusted_signing_keys`
- repo metadata key `cache_epoch` for query cache invalidation

## Indexing model

High-level pipeline:

1. Walk repository files and detect supported languages.
2. Parse files and extract symbols/imports.
3. Resolve imports and call edges.
4. Persist symbols/edges/dependencies into SQLite.
5. Recompute rank features used by query layers.

Incremental path updates only changed files and then rebuilds impacted graph state.

## Guardrails and safety limits

Runtime payload guardrails are enforced for depth, limits, and query lengths:

- search limit clamped to max `100`
- graph depth clamped to max `6`
- context token budget clamped to max `32000`
- entry points capped at `32`
- traversal node/edge caps prevent runaway expansion
- adaptive traversal caps scale by repository symbol count for large-repo memory safety

These limits are defined in `src/bombe/query/guards.py`.

## MCP tools and response contracts

Available tools:

- `search_symbols`
- `get_references`
- `get_context`
- `get_structure`
- `get_blast_radius`
- `trace_data_flow`
- `change_impact`

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

All dict-returning tools also accept:

```json
{"include_explanations":true}
```

When enabled, responses include an `explanations` section with reasoning metadata. `get_structure` remains a string response and prepends a structured explanation header line.

All tools also accept:

```json
{"include_plan":true}
```

When enabled, dict responses include `planner_trace` metadata (cache mode, lookup/compute timing, cache epoch token).

### Contract validation

Strict contract behavior is verified by:

- `tests/test_mcp_contract.py`

The tests assert key sets and payload shapes for all tool responses.

## Hybrid sync model

Hybrid sync is additive and does not replace local query serving.

- Local path remains authoritative and available.
- Incompatible artifacts are rejected.
- Corrupt artifacts are quarantined.
- Signature mismatches are quarantined when `BOMBE_SYNC_SIGNING_KEY` is configured.
- Trusted key policy can be persisted per repository in `trusted_signing_keys` for verification key selection.
- Repeated remote failures open the circuit breaker.
- Results explicitly expose fallback mode (`local_fallback`) when remote operations are skipped or fail.
- Sync outcomes are persisted in SQLite (`sync_queue`, `sync_events`, `artifact_pins`, `circuit_breakers`).

### Backup and restore

Database backup/restore helpers are available in `Database`:

```python
from pathlib import Path
from bombe.store.database import Database

db = Database(Path("/abs/repo/.bombe/bombe.db"))
backup_path = db.backup_to(Path("/tmp/bombe-backup.db"))
db.restore_from(backup_path)
```

## Performance and release gates

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

## Repository layout

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

## Development workflow

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

- lint/typecheck
- unit + coverage
- release-gates (perf + gate evaluation)

Recommended local all-check run:

```bash
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
BOMBE_RUN_PERF=1 PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.jsonl
```

## Verification snapshot

Latest local verification run (2026-02-08):

- `PYTHONPATH=src python3 -m compileall src tests` -> pass
- `PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` -> pass (`100` tests)
- `BOMBE_RUN_PERF=1 BOMBE_PERF_HISTORY=/tmp/bombe-perf-history.final.jsonl PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v` -> pass (`6` tests, `1` skipped when `BOMBE_REAL_REPO_PATHS` is unset)
- `PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.final.jsonl` -> `RELEASE_GATES=PASS`

## Troubleshooting and limitations

Troubleshooting:

- Parse/extraction misses:
  - verify file extension and syntax
  - confirm file is under indexed repo path
- Empty query output:
  - verify indexing completed and symbols exist in DB
- Gate failure:
  - inspect perf history JSONL
  - rerun only failing suite
  - evaluate again with `bombe.release.gates`

Known limitations:

- Static call resolution can miss dynamic dispatch and reflection-heavy patterns.
- Precision/recall in extremely dynamic codebases depends on language semantics available to static analysis.
- Hybrid sync primitives are implemented, but deployment topology for shared control plane is intentionally left operator-specific.

## Status

Active implementation toward hybrid, spec-complete MCP traversal/runtime with hard release gates and operator runbooks.
