# Hybrid Control Plane + Local Fast Path

Date: 2026-02-08  
Project: Bombe MCP  
Design target: make Bombe the default traversal and context skill for coding agents on large polyglot repositories (OpenSearch-scale)

## 1. Decision and Alternatives

We will implement **Approach A: Hybrid Control Plane + Local Fast Path** as the canonical architecture. The local runtime remains the primary execution path for low-latency interaction and offline resilience, while a shared control plane provides multi-agent reuse and consistency artifacts at scale.

Alternatives considered:

- **Approach B: Centralized-first runtime**: best single source of truth and global deduplication, but too fragile for day-to-day coding if network or service quality degrades, and too expensive in tail latency for interactive tool calls.
- **Approach C: Local-only with exported artifacts**: strongest isolation and simplest operations, but poor agent-to-agent reuse and repeated recomputation on large repositories.

Why Approach A wins:

1. Preserves local speed and reliability under all conditions.
2. Enables shared intelligence that compounds across agents and sessions.
3. Allows strict release gates on both mid-size and large-repo profiles without forcing every query through a remote service.
4. Supports phased rollout with reversible risk: local remains authoritative fallback.

Primary constraints and hard gates:

- **ALL-PASS HARD GATE** across four workflows: flow tracing, change impact, cross-module symbol traversal, bug triage context assembly.
- Must pass on **Apple Silicon laptop baseline with 16GB RAM + SSD**.
- No regression to current stable tool contracts unless versioned explicitly.
- Strict degradation semantics: if control plane is unavailable, local behavior remains fully functional within documented confidence bounds.

## 2. System Architecture (Balanced for Accuracy, Performance, Capability, Reliability)

Bombe will run as two cooperating planes.

### Local Runtime (per agent workspace)

- Repo watcher + incremental indexer.
- Local SQLite graph slice with symbol, edge, import, and ranking metadata.
- Hot query cache for repeated interactions.
- Local router that decides whether to satisfy request locally or enrich from control plane artifacts.
- Confidence and provenance annotator for every response.

### Shared Control Plane (multi-agent)

- Artifact registry keyed by `(repo, branch lineage, snapshot)`.
- Canonical symbol identity and reconciliation service.
- Cross-agent promoted graph artifacts (high-confidence edges, call neighborhoods, impact priors).
- Contract catalog for tool schemas and version compatibility.
- Telemetry collector and benchmark evaluator.

### Routing policy

- Local-first for interactive queries under confidence thresholds.
- Control-plane-assisted when query scope exceeds local confidence (for example deep transitive paths across sparsely indexed areas).
- Responses include `source=local|hybrid|remote_artifact`, confidence score, and feature flags used.

### Design principle

A request should never fail solely because the control plane is down. Instead, it should return best-effort local output with explicit confidence and missing-scope indicators.

## 3. Component Boundaries and Responsibilities

### A. Local Index Engine

- Parse + extract symbols/imports/calls incrementally from changed files.
- Maintain deterministic local IDs and edge sets.
- Compute local ranking features (PageRank, proximity, path coherence).

### B. Local Query Engine

- Implements MCP handlers and response assembly.
- Executes search, references, context packing, structure, blast, data-flow, change-impact.
- Attaches explainability metadata and quality metrics.

### C. Sync Client

- Pushes signed local deltas to control plane.
- Pulls approved artifacts with explicit version pinning.
- Reconciles local state using conflict policy and TTL.

### D. Control Plane Artifact Service

- Validates incoming deltas against schema + integrity checks.
- Promotes artifacts only after confidence and consistency tests.
- Publishes immutable artifact versions with rollback pointers.

### E. Contract and Governance Layer

- Maintains MCP schema versions and compatibility matrix.
- Rejects incompatible artifact consumption.
- Supports canary channels and emergency rollback.

### F. Observability Layer

- Records p50/p95 latency and quality metrics per tool/workflow.
- Tracks false-link, ambiguity, and contract drift counters.
- Emits benchmark summaries and trend history.

Boundary rule: only the Local Query Engine touches live agent requests; all other components are support services and must be non-blocking for local operations.

## 4. Data Contracts and Synchronization Protocol

### Core identities

- `repo_id`: stable logical repository identity.
- `snapshot_id`: immutable content snapshot identifier.
- `symbol_key`: `(qualified_name, file_path, start_line, end_line, signature_hash)`.
- `edge_key`: `(source_symbol_key, target_symbol_key, relationship, line_number)`.

### Delta contract (local -> control plane)

- `delta_header`: repo_id, parent_snapshot, local_snapshot, tool_version, schema_version.
- `file_changes`: added/modified/deleted/renamed with content hash.
- `symbol_upserts` and `symbol_deletes`.
- `edge_upserts` and `edge_deletes` with confidence and provenance.
- `quality_stats`: ambiguity rate, unresolved imports, parse failures.

### Artifact contract (control plane -> local)

- Immutable artifact metadata: version, snapshot lineage, generation timestamp.
- `promoted_symbols`, `promoted_edges`, `impact_priors`, `flow_hints`.
- Compatibility fields: required schema/tool versions.
- Validation checksum and signature.

### Sync semantics

1. Local pushes delta asynchronously.
2. Control plane validates and may promote derived artifact.
3. Local pulls compatible artifact versions opportunistically.
4. Merge strategy: prefer local fresh data for touched files, artifact data for untouched cross-module context.
5. Conflict policy: newer snapshot wins when lineage is compatible; otherwise isolate under branch scope.

### Failure semantics

- Failed push: queue and retry with backoff.
- Incompatible artifact: skip and continue local-only.
- Corrupt artifact: quarantine version and revert to previous pinned artifact.

## 5. Hard Workflow Gates (OpenSearch-Scale Benchmark Program)

All workflows must pass on both profiles:

- Mid-size polyglot: 50k-300k LOC.
- Large monorepo profile: 300k-2M+ LOC.

Hardware baseline: Apple Silicon, 16GB RAM, SSD.

### Workflow A: End-to-end flow trace

Goal: from entry symbol to critical downstream path with minimal noise.

- Accuracy gate: top-N path precision >= 0.9 on curated scenarios.
- Latency gate: p95 <= 2.0s for depth-3 path request.

### Workflow B: Safe change impact

Goal: identify direct + transitive breakage surface.

- Accuracy gate: direct dependency recall >= 0.95; transitive precision >= 0.85.
- Latency gate: p95 <= 2.5s for depth-3 impact request.

### Workflow C: Cross-module symbol traversal

Goal: discover definitions and references across language/module boundaries.

- Accuracy gate: top-5 hit rate >= 0.95 for gold queries.
- Latency gate: p95 <= 1.2s for search + references sequence.

### Workflow D: Bug triage context assembly

Goal: build token-budgeted context from stack/function hint.

- Quality gate: seed_hit_rate >= 0.9, connectedness >= 0.8.
- Latency gate: p95 <= 1.8s for context assembly at target budget.

Release rule: a release is blocked if any workflow fails any gate on either profile.

## 6. Detailed Implementation Plan

## Phase 1: Identity and Contract Foundation

- Introduce canonical `symbol_key` and `edge_key` structures.
- Version tool contracts and add strict snapshot tests.
- Deliverable: stable schemas for local and artifact exchange.

## Phase 2: Local Accuracy Engine

- Improve call resolution with alias, receiver, scope, and import-aware disambiguation.
- Add ambiguity scoring and fallback rationale.
- Deliverable: reduced false positives in call graph.

## Phase 3: Collision-Free ID Pipeline

- Remove hash-only staging in mapping layers.
- Use composite keys for deterministic mapping.
- Deliverable: zero collision risk in ID translation.

## Phase 4: Migration Framework

- Implement stepwise DB migrations (`vN -> vN+1`) with tests and rollback path.
- Add migration health checks and startup guardrails.
- Deliverable: safe forward schema evolution.

## Phase 5: Local Query Quality Metrics

- Add query-quality outputs (seed hit, connectedness, token efficiency, avg depth).
- Track metric regressions in tests.
- Deliverable: measurable context quality.

## Phase 6: Sync Protocol v1

- Add async delta push and artifact pull clients.
- Implement compatibility checks and quarantine behavior.
- Deliverable: control-plane integration without local blocking.

## Phase 7: Artifact Promotion and Reconciliation

- Implement server-side validators and promotion policies.
- Define local merge precedence rules by touched scope and snapshot lineage.
- Deliverable: trusted shared intelligence reuse.

## Phase 8: Benchmark Harness

- Build curated OpenSearch-like scenario corpus for all four workflows.
- Add pass/fail gates with p95 and accuracy thresholds.
- Deliverable: reproducible hard gate pipeline.

## Phase 9: Observability and Trend History

- Persist JSONL performance and quality histories.
- Add dashboards or summary reports for release readiness.
- Deliverable: visible trend tracking and regression alerts.

## Phase 10: Reliability Hardening

- Add circuit breakers, timeout budgets, and controlled degradation paths.
- Add chaos-style tests for control-plane outage and artifact corruption.
- Deliverable: resilient hybrid runtime.

## Phase 11: Documentation and Operator Playbooks

- Publish tool usage, sync semantics, migration operations, rollback steps.
- Add “local-only mode” and “hybrid mode” runbooks.
- Deliverable: production-ready operational clarity.

## Phase 12: Release Criteria and Governance

- Enforce all-pass hard gate in CI release workflow.
- Add signed release manifests and compatibility matrices.
- Deliverable: auditable release process for all agents.

## 7. Error Handling, Reliability, and Security

### Error taxonomy

- Parse/index errors (file-level, non-fatal).
- Query resolution errors (recoverable with partial output).
- Sync errors (retryable/non-retryable).
- Contract incompatibility (explicit downgrade to local-only).

### Resilience requirements

- Local tool calls must succeed if repository is accessible, regardless of control plane status.
- Timeouts and retries must be bounded and observable.
- Artifact corruption must trigger quarantine and rollback automatically.

### Security and trust

- Artifact manifests signed and checksummed.
- Schema/tool version pinning prevents accidental incompatible merges.
- Provenance attached to promoted edges and impact priors.

### Degradation rules

- On remote failure: emit `mode=local_fallback` with confidence and missing-scope list.
- On local parse failure: skip file, surface diagnostics, keep prior graph where safe.

## 8. Test Strategy

### Unit

- Resolver behavior (alias/scope/receiver cases).
- Migration steps and reentrancy.
- Delta/artifact serialization and compatibility checks.

### Integration

- Full and incremental indexing across mixed-language fixtures.
- Query workflow correctness against gold cases.
- Sync push/pull and reconciliation correctness.

### Contract

- Strict MCP output snapshot tests.
- Schema version compatibility matrix tests.

### Performance

- p50/p95 per workflow and per stage.
- Trend regression thresholds on laptop baseline.

### Reliability

- Control-plane outage simulation.
- Corrupt artifact and stale lineage scenarios.

## 9. Execution Order and Ownership

Recommended execution order:

1. Phases 1-4 (foundational correctness and compatibility).
2. Phases 5-7 (quality + hybrid sync mechanics).
3. Phases 8-10 (hard gates + resilience).
4. Phases 11-12 (operationalization and release governance).

Each phase exits only when:

- Compile and full test suite pass.
- Workflow gates for touched areas pass.
- Diff audit finds no unresolved regression.
- Documentation and contracts for changed behavior are updated.

## 10. Deliverables Checklist

- [ ] Hybrid architecture runtime and sync protocol implemented.
- [ ] All four workflows pass accuracy + latency hard gates on both repo profiles.
- [ ] Migration framework supports stepwise upgrades and rollback.
- [ ] MCP contracts are strict and versioned.
- [ ] Perf and quality trend history is persisted and reviewed in release flow.
- [ ] Operator docs and runbooks are complete.

