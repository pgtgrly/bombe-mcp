# Bombe Spec Completion Plan (9 Phases)

Date: 2026-02-08  
Status: Execution complete  
Scope: make Bombe a robust local-first MCP traversal/runtime with hybrid sync, correctness gates, and operator-grade documentation.

## Execution protocol

Each phase follows the same loop:

1. Implement scoped changes.
2. Compile and run targeted tests.
3. Audit diff for regressions and contract drift.
4. Rebuild and rerun tests until clean.
5. Move to the next phase only when no unresolved issues remain.

## Phase 1: Contract and identity foundation

### Objectives

- Stabilize tool input/output contracts.
- Ensure schema-aware sync payload structures are explicit and versioned.
- Keep symbol and edge identity deterministic across index and sync flows.

### Key artifacts

- `src/bombe/models.py`
- `src/bombe/tools/definitions.py`
- `src/bombe/sync/client.py`
- `tests/test_mcp_contract.py`

### Exit criteria

- MCP tool payloads are deterministic and contract-tested.
- Delta and artifact schema versions are enforced.

## Phase 2: Local call-resolution precision

### Objectives

- Improve callgraph resolution for receiver/type/alias cases.
- Reduce false positives in ambiguous method dispatch.

### Key artifacts

- `src/bombe/indexer/callgraph.py`
- `tests/test_callgraph.py`

### Exit criteria

- Receiver-hint scenarios pass (local instantiation, `self.member`, class-scoped calls).
- Ambiguous cases carry lower confidence values.

## Phase 3: Collision-safe symbol and edge identity mapping

### Objectives

- Ensure collision-safe mapping paths when persistent symbol IDs are available.
- Preserve deterministic fallback behavior for ephemeral contexts.

### Key artifacts

- `src/bombe/indexer/callgraph.py`
- `src/bombe/indexer/pipeline.py`
- `tests/test_callgraph.py`

### Exit criteria

- Explicit `symbol_id_lookup` path is used in production index pipeline.
- Hash fallback remains available for isolated unit fixtures.

## Phase 4: Migration framework and persisted state

### Objectives

- Add migration-safe schema evolution and operational state tables.
- Support backup and restore for rollback safety.

### Key artifacts

- `src/bombe/store/database.py`
- `tests/test_database.py`

### Exit criteria

- `SCHEMA_VERSION=4` migration path works from earlier versions.
- Sync queue, pins, quarantine, breaker, events, and metrics persist correctly.
- Backup/restore round-trip is tested.

## Phase 5: Query guardrails and quality metrics

### Objectives

- Bound query payloads and graph traversal expansion.
- Preserve stable behavior under large or malformed inputs.

### Key artifacts

- `src/bombe/query/guards.py`
- `src/bombe/query/search.py`
- `src/bombe/query/references.py`
- `src/bombe/query/context.py`
- `src/bombe/query/data_flow.py`
- `src/bombe/query/change_impact.py`
- `tests/test_tool_guardrails.py`

### Exit criteria

- Depth/limit/budget clamps are enforced centrally.
- Context quality metrics are emitted and validated by workflow gates.

## Phase 6: Hybrid sync protocol and reconciliation

### Objectives

- Add local control-plane transport, sync orchestration, and reconciliation flow.
- Persist sync outcomes and quarantine/circuit-breaker state.

### Key artifacts

- `src/bombe/sync/transport.py`
- `src/bombe/sync/orchestrator.py`
- `src/bombe/sync/client.py`
- `src/bombe/sync/reconcile.py`
- `tests/test_sync_orchestrator.py`

### Exit criteria

- Push/pull/reconcile cycle runs without blocking local operation.
- Compatibility and checksum failures degrade to local fallback.

## Phase 7: Server lifecycle commands and operator status

### Objectives

- Add explicit CLI commands for indexing and status.
- Support optional hybrid sync during index operations.

### Key artifacts

- `src/bombe/server.py`
- `tests/test_server.py`

### Exit criteria

- `index-full`, `index-incremental`, and `status` return stable JSON.
- `serve --index-mode` supports pre-serve warmup modes.

## Phase 8: Workflow benchmark gates and release integration

### Objectives

- Validate behavior against OpenSearch-like traversal scenarios.
- Enforce hard release gates for index, incremental, query, and workflow quality.

### Key artifacts

- `tests/perf/workflow_harness.py`
- `tests/test_correctness_gates.py`
- `tests/perf/test_workflow_gates.py`
- `src/bombe/release/gates.py`

### Exit criteria

- Correctness gates pass on the curated corpus.
- Perf suites record metrics and gate evaluator returns pass.

## Phase 9: Observability, docs, and operator runbooks

### Objectives

- Persist tool-level operational telemetry.
- Publish runbooks for local-only, hybrid, rollback, and quarantine operations.
- Document verification protocol and completion status.

### Key artifacts

- `src/bombe/tools/definitions.py`
- `src/bombe/store/database.py`
- `README.md`
- `docs/runbooks/local-only-mode.md`
- `docs/runbooks/hybrid-mode.md`
- `docs/runbooks/rollback-and-quarantine.md`

### Exit criteria

- Tool metrics are recorded without impacting runtime availability.
- Operators have complete docs for run/rollback/recovery paths.

## Final verification commands

```bash
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
BOMBE_RUN_PERF=1 BOMBE_PERF_HISTORY=/tmp/bombe-perf-history.final.jsonl PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.final.jsonl
```

Expected terminal status for completion:

- Unit/integration suite passes with no warnings promoted to errors.
- Perf suites pass and write history.
- `RELEASE_GATES=PASS`.
