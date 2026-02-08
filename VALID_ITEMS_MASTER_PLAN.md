# Bombe Valid Items Master Plan

Date: 2026-02-08
Status: Proposed
Location: repository root

## Purpose
This document is the execution plan for all currently valid improvements from the vision catalog.
It covers both near-term items with immediate product value and later items that remain valid but depend on foundation work.

## Included Valid Items
The plan includes every item previously marked valid:

1. Dependency/runtime preflight checks with strict hard-fail mode.
2. Tree-sitter language expansion matrix for Python, Java, Go, TypeScript, Rust, and C++.
3. Parallel indexing and throughput scaling.
4. Parse and index diagnostics as first-class outputs/tools.
5. Non-git file watching with include and exclude controls.
6. Better observability tools (server status, indexing progress, query trace).
7. Token-estimation and context-summary tools.
8. Secret redaction and sensitive-path exclusion.
9. Real-repo benchmark gates plus fuzzing plus mutation-style regression tests.
10. Workspace and multi-root indexing (single logical graph).
11. Discovery tools (entry points, hot paths, orphan symbols).
12. Hybrid retrieval scoring (FTS plus structural plus optional vectors).
13. Optional LSP bridge for type and symbol precision.
14. Reference remote control plane for shared indices and artifacts.
15. Plugin system for custom extractors and ranking hooks.
16. IDE/editor integrations.
17. Docker and Kubernetes packaging.
18. Interactive graph and index inspector UI.
19. Cross-repo dependency graphing with sharding strategy.

## Success Criteria
A release is considered complete when all of the following are true:

- Core paths work in both `default` and `strict` runtime profiles.
- Strict profile can run large-repo indexing with no silent fallback.
- Local-first latency and reliability gates pass in CI and real-repo suites.
- Operators can diagnose parse and index failures from machine-readable outputs.
- Agents can get progress, traces, and token forecasts before expensive operations.
- Sensitive data is redacted and excluded consistently from index and query outputs.
- Multi-root and cross-repo behavior is deterministic and test-covered.
- Deployment and integration assets are versioned and reproducible.

## Delivery Strategy
Work is split into two waves.

- Wave 1: Product-critical reliability and agent utility.
- Wave 2: Ecosystem, scale, and platform extensions.

Wave 2 begins only after Wave 1 gates are green for two consecutive CI runs and one real-repo run.

## Global Execution Protocol
Each phase follows this loop:

1. Implement one scoped item.
2. Run compile plus targeted tests.
3. Audit diff for regressions, edge cases, and contract drift.
4. Fix issues and rerun until clean.
5. Run phase-level full suite.
6. Proceed only after no open issues.

## Baseline Requirements

- Python pinned to `>=3.11,<3.12`.
- Tree-sitter dependency compatibility validated during startup preflight.
- Tree-sitter coverage matrix tracked in CI for required languages in strict profile.
- SQLite schema migrations forward-only and idempotent.
- All new tools have contract tests.
- All long-running CLI operations return structured progress data.

## Phase Plan

## Phase 1: Runtime Preflight and Strict Profile
### Goal
Guarantee deterministic runtime compatibility and an explicit strict profile for benchmark and demo quality.

### Deliverables
- Preflight check command and startup preflight path.
- Runtime profiles: `default`, `strict`.
- Tree-sitter language expansion matrix with capability report (Python, Java, Go, TypeScript, Rust, C++).
- Strict mode hard-fails on parser/backend incompatibility.
- Structured preflight output for CI and operators.

### Work Items
1. Add dependency compatibility checks for tree-sitter runtime matrix.
2. Implement and validate parser backend capability checks by required language.
3. Add `--runtime-profile default|strict` CLI argument.
4. Add `doctor` checks for profile readiness.
5. Add machine-readable error codes for preflight failures.
6. Add CI matrix validation that strict profile fails if any required parser backend is unavailable.

### Affected Areas
- `src/bombe/server.py`
- `src/bombe/indexer/parser.py`
- `src/bombe/config.py`
- `README.md`
- `docs/runbooks/local-only-mode.md`

### Tests
- Unit tests for profile behavior.
- Integration test for strict fail-fast behavior.
- Snapshot test for preflight JSON payload.

### Exit Criteria
- Strict profile never silently falls back.
- Default profile remains backward compatible.
- Preflight command exits non-zero on incompatibility.
- Required-language Tree-sitter capability report is available and green in CI.

## Phase 2: Parse and Index Diagnostics
### Goal
Expose parse/index failures as first-class diagnostics for agents and operators.

### Deliverables
- Persistent parse failure table.
- Index diagnostics tool/command.
- Failure categories and remediation hints.
- Per-run diagnostics summary in index response payload.

### Work Items
1. Extend schema with parse/index diagnostics tables.
2. Capture parser exceptions with file, language, stage, and error message.
3. Add `get_indexing_diagnostics` MCP tool.
4. Add `status` and `doctor` summaries for failure counts.
5. Add optional `--diagnostics-limit` CLI arg.

### Affected Areas
- `src/bombe/store/database.py`
- `src/bombe/indexer/pipeline.py`
- `src/bombe/tools/definitions.py`
- `src/bombe/server.py`

### Tests
- Schema migration and persistence tests.
- Contract tests for diagnostics tool.
- Real fixture tests with intentionally broken source files.

### Exit Criteria
- Parse failures are queryable and reproducible.
- No crash due to individual file parse errors in default profile.

## Phase 3: Watching and Index Controls
### Goal
Support non-git workflows and precise include/exclude controls for large repositories.

### Deliverables
- Non-git change detection path.
- Include/exclude globs and ignore overrides.
- Change visibility logs and payload entries.
- Configurable debounce and batch limits.

### Work Items
1. Add filesystem scan diff path for non-git roots.
2. Add merged ignore policy (`.gitignore` plus user config).
3. Add `--include` and `--exclude` CLI options.
4. Emit structured change list before index trigger.
5. Add safeguards for excessive watch event bursts.

### Affected Areas
- `src/bombe/watcher/git_diff.py`
- `src/bombe/server.py`
- `src/bombe/indexer/filesystem.py`
- `src/bombe/config.py`

### Tests
- Non-git watch tests.
- Include/exclude matching tests.
- Debounce and event burst tests.

### Exit Criteria
- Watch mode works for git and non-git repos.
- Operators can predict exactly which files are indexed.

## Phase 3A: Parallel Indexing and Throughput
### Goal
Reduce large-repo indexing latency by fully utilizing available CPU while preserving deterministic results.

### Deliverables
- Multiprocess parse/extract pipeline with bounded worker pool.
- Deterministic merge strategy for parallel symbol/edge writes.
- Throughput telemetry (files/sec, queue depth, worker utilization).
- Throughput tuning knobs for batch size and worker count.

### Work Items
1. Partition indexing work by file chunks and language buckets.
2. Implement process-pool parsing and extraction workers.
3. Add deterministic aggregation and conflict-safe DB write sequencing.
4. Add backpressure controls for memory and queue growth.
5. Add perf baselines and regression thresholds for single-thread vs parallel modes.

### Affected Areas
- `src/bombe/indexer/pipeline.py`
- `src/bombe/indexer/parser.py`
- `src/bombe/store/database.py`
- `src/bombe/server.py`
- `tests/perf/*`

### Tests
- Concurrency determinism tests against fixed fixtures.
- Correctness parity tests (single-thread vs parallel index outputs).
- Throughput benchmarks on medium and large repositories.

### Exit Criteria
- Parallel mode shows measurable speedup on multicore hosts.
- No correctness drift relative to single-thread baseline.
- Memory growth remains bounded under configured limits.

## Phase 4: Observability APIs and Progress
### Goal
Provide structured visibility for long operations and query planning behavior.

### Deliverables
- `get_server_status` MCP tool.
- Index progress notifications for long runs.
- Consistent query planner traces.
- Health summary payload used by CLI and MCP.

### Work Items
1. Add unified runtime status model.
2. Add progress event emitter in index pipeline.
3. Add progress snapshots to watch/index responses.
4. Standardize `planner_trace` schema across tools.
5. Add timing and cache-hit counters to status payload.

### Affected Areas
- `src/bombe/tools/definitions.py`
- `src/bombe/query/planner.py`
- `src/bombe/server.py`
- `src/bombe/indexer/pipeline.py`

### Tests
- Contract tests for `get_server_status`.
- Long-run progress tests.
- Trace shape and value assertions.

### Exit Criteria
- Agents can poll status without parsing logs.
- Long runs expose monotonic progress updates.

## Phase 5: Token Forecast and Summary Tools
### Goal
Improve context efficiency before payload generation.

### Deliverables
- `estimate_context_size` tool.
- `get_context_summary` tool.
- Optional paged response strategy for large outputs.

### Work Items
1. Implement token estimate from symbol metadata and tokenizer.
2. Implement summary-only context path at module and symbol level.
3. Add pagination controls for large list outputs.
4. Add compatibility rules with existing `get_context` behavior.

### Affected Areas
- `src/bombe/query/context.py`
- `src/bombe/query/tokenizer.py`
- `src/bombe/tools/definitions.py`
- `src/bombe/models.py`

### Tests
- Token estimate accuracy bounds tests.
- Summary contract tests.
- Pagination determinism tests.

### Exit Criteria
- Agents can predict token cost pre-fetch.
- Summary path reduces payload size while preserving relevance.

## Phase 6: Security Redaction and Sensitive Data Controls
### Goal
Prevent indexing and retrieval of sensitive material by default policy.

### Deliverables
- Default sensitive path exclusions.
- Content redaction patterns for known secret formats.
- Redaction metadata in diagnostics.
- Operator policy override file.

### Work Items
1. Add sensitive file pattern set.
2. Add content redaction filters in ingestion path.
3. Track redaction stats per index run.
4. Add policy config and validation.
5. Ensure query outputs cannot reveal redacted values.

### Affected Areas
- `src/bombe/indexer/filesystem.py`
- `src/bombe/indexer/pipeline.py`
- `src/bombe/query/*`
- `src/bombe/config.py`
- `docs/runbooks/*`

### Tests
- Secret fixture tests.
- Redaction correctness and leakage tests.
- Policy override tests.

### Exit Criteria
- Sensitive patterns are excluded or redacted consistently.
- No plain secret leakage in tool responses.

## Phase 7: Discovery Tools for Agent Navigation
### Goal
Improve initial navigation and dead-code discovery.

### Deliverables
- `get_entry_points` tool.
- `get_hot_paths` tool.
- `get_orphan_symbols` tool.

### Work Items
1. Define entry-point scoring strategy (pagerank plus call centrality plus file role heuristics).
2. Define hot-path scoring strategy (inbound call density plus centrality).
3. Define orphan detection (no callers/inbound references with ignore exceptions).
4. Add tool schemas and explanations.
5. Add docs and usage recipes.

### Affected Areas
- `src/bombe/query/*`
- `src/bombe/tools/definitions.py`
- `tests/test_mcp_contract.py`

### Tests
- Deterministic ranking tests on fixed fixtures.
- Contract tests for each tool.
- False-positive controls for orphans.

### Exit Criteria
- Tools produce deterministic top-N lists.
- Discovery outputs reduce first-query latency for agents.

## Phase 8: Quality Gates Expansion (Real-Repo plus Fuzz plus Mutation)
### Goal
Increase confidence under realistic scale and adversarial inputs.

### Deliverables
- Expanded real-repo benchmark harness.
- Query fuzz test suite.
- Mutation-style graph regression suite.
- New release gates tied to these suites.

### Work Items
1. Add benchmark scenarios for OpenSearch-style workloads.
2. Add randomized query generator with reproducible seeds.
3. Add mutation fixtures that simulate graph corruption or parser misses.
4. Add gate thresholds and historical trend checks.
5. Add nightly workflow for extended suites.

### Affected Areas
- `tests/perf/*`
- `tests/test_correctness_gates.py`
- `src/bombe/release/gates.py`
- `.github/workflows/ci.yml`

### Tests
- CI and nightly split execution tests.
- Gate fail/pass threshold tests.
- Harness determinism checks.

### Exit Criteria
- Gate evaluator includes new suite families.
- Reproducible perf and correctness outcomes across runs.

## Phase 9: Multi-Root Workspace Graph
### Goal
Index multiple repository roots as one logical workspace.

### Deliverables
- Workspace config with named roots.
- Root-aware symbol identity and query filters.
- Unified status and diagnostics across roots.

### Work Items
1. Introduce workspace model and root registry.
2. Add root namespace to symbols and edges.
3. Update query APIs to accept optional root scope.
4. Add CLI operations for workspace bootstrap and status.
5. Add migration strategy for single-root DB.

### Affected Areas
- `src/bombe/models.py`
- `src/bombe/store/database.py`
- `src/bombe/indexer/pipeline.py`
- `src/bombe/query/*`
- `src/bombe/server.py`

### Tests
- Multi-root ingestion tests.
- Cross-root query tests.
- Backward compatibility migration tests.

### Exit Criteria
- Multiple roots index and query correctly in one runtime.
- Existing single-root usage remains valid.

## Phase 10: Hybrid Retrieval Scoring (FTS plus Structural plus Optional Vectors)
### Goal
Improve semantic recall without sacrificing local determinism.

### Deliverables
- Pluggable hybrid scorer.
- Optional vector path behind feature flag.
- Weighted rank formula with versioned config.

### Work Items
1. Define rank composition model and normalization.
2. Add optional vector index backend adapter.
3. Add reranking path for top-K candidates.
4. Add observability for score component contributions.
5. Add fallback behavior rules per runtime profile.

### Affected Areas
- `src/bombe/query/search.py`
- `src/bombe/query/planner.py`
- `src/bombe/store/database.py`
- `src/bombe/config.py`

### Tests
- Ranking stability tests.
- Recall and precision evaluations on curated corpora.
- Feature-flag behavior tests.

### Exit Criteria
- Hybrid ranking improves hit rate on gold queries.
- Strict profile behavior remains deterministic.

## Phase 11: Optional LSP Bridge
### Goal
Improve type resolution and call-target precision using language servers.

### Deliverables
- LSP integration adapter layer.
- Per-language capability checks.
- Cached LSP-derived hints merged into local graph.

### Work Items
1. Define LSP provider abstraction.
2. Add provider health and timeout controls.
3. Merge LSP hints with confidence weighting.
4. Add diagnostics when LSP responses are stale or unavailable.

### Affected Areas
- `src/bombe/indexer/semantic.py`
- `src/bombe/indexer/callgraph.py`
- `src/bombe/server.py`

### Tests
- Mock provider tests.
- Timeout and partial-response tests.
- Precision regression tests.

### Exit Criteria
- LSP bridge is optional and isolated from core runtime stability.

## Phase 12: Plugin System and Extensibility Hooks
### Goal
Allow custom language extractors and ranking policies without core forks.

### Deliverables
- Plugin loading model.
- Lifecycle hook points (pre-index, post-index, pre-query, post-query).
- Versioned plugin API contract.

### Work Items
1. Define plugin manifest and capability interface.
2. Add secure plugin loading and isolation boundaries.
3. Add hook execution order and timeout rules.
4. Add plugin diagnostics and failure reporting.

### Affected Areas
- `src/bombe/plugins/*` (new)
- `src/bombe/server.py`
- `src/bombe/indexer/pipeline.py`
- `src/bombe/query/*`

### Tests
- Plugin lifecycle tests.
- Misbehaving plugin isolation tests.
- API version compatibility tests.

### Exit Criteria
- Core runtime remains stable when plugins fail.
- Plugin API is documented and test-covered.

## Phase 13: Integration and Deployment Surface
### Goal
Make Bombe easy to adopt in tooling and infrastructure.

### Deliverables
- VS Code integration package.
- Docker image and compose profile.
- Kubernetes deployment manifests and health probes.

### Work Items
1. Define MCP client integration contract examples.
2. Build minimal extension using existing tool contract.
3. Add Dockerfile and runtime image tests.
4. Add Helm or raw K8s manifests with storage guidance.

### Affected Areas
- `integrations/vscode/*` (new)
- `deploy/docker/*` (new)
- `deploy/k8s/*` (new)
- `README.md`

### Tests
- Container smoke tests.
- Extension protocol integration tests.
- K8s manifest lint and startup checks.

### Exit Criteria
- One-command local container run works.
- Integration docs are reproducible.

## Phase 13A: Reference Remote Control Plane
### Goal
Provide a reference shared control-plane service for teams to exchange artifacts and index intelligence safely.

### Deliverables
- Reference control-plane server implementation.
- Artifact registry API with pinning, retention, and lineage lookup.
- Auth and signing policy for push/pull operations.
- Operator runbook for deployment, rotation, and recovery.

### Work Items
1. Define reference control-plane API and artifact metadata contract.
2. Implement control-plane service for artifact publish/fetch and lifecycle operations.
3. Add client transport support for authenticated remote endpoints.
4. Add key management and rotation procedures for signed artifact exchange.
5. Add failure-mode handling for network partition, stale snapshots, and conflict resolution.

### Affected Areas
- `src/bombe/sync/transport.py`
- `src/bombe/sync/client.py`
- `src/bombe/sync/orchestrator.py`
- `src/bombe/control_plane/*` (new)
- `docs/runbooks/hybrid-mode.md`

### Tests
- Multi-client integration tests for push/pull/reconcile.
- Auth and signature verification tests.
- Resilience tests for retries, partitions, and stale artifacts.

### Exit Criteria
- Teams can share artifacts through a reference service with signed integrity checks.
- Local-first behavior remains intact when remote service is unavailable.
- Operational runbook is complete and reproducible.

## Phase 14: Interactive Inspector UI
### Goal
Provide a first-party interface to inspect graph, rankings, and diagnostics.

### Deliverables
- Web UI for symbol graph inspection.
- Query explainer view.
- Index diagnostics explorer.

### Work Items
1. Build read-only API surface for UI consumption.
2. Implement graph navigation and query panels.
3. Add performance safeguards for large graphs.
4. Add authentication mode if exposed beyond localhost.

### Affected Areas
- `src/bombe/ui_api/*` (new)
- `ui/*` (new)
- `src/bombe/server.py`

### Tests
- UI API contract tests.
- Snapshot tests for query explainers.
- Load tests for large graph views.

### Exit Criteria
- Operators can inspect index health without SQL.
- UI does not degrade core MCP latency.

## Phase 15: Cross-Repo Graphing and Sharding
### Goal
Support organization-scale code graphs with predictable performance.

### Deliverables
- Cross-repo reference model.
- Shard planner and routing strategy.
- Aggregated query execution across shards.

### Work Items
1. Define global symbol identity across repositories.
2. Implement shard assignment policy and metadata catalog.
3. Implement federated query planner.
4. Add consistency model for incremental updates.
5. Add resilience and recovery strategy for shard failures.

### Affected Areas
- `src/bombe/store/sharding/*` (new)
- `src/bombe/query/federated/*` (new)
- `src/bombe/server.py`

### Tests
- Federated query correctness tests.
- Shard rebalance and recovery tests.
- Scale and latency benchmark suites.

### Exit Criteria
- Cross-repo calls resolve deterministically.
- Sharding improves throughput without correctness regression.

## Cross-Phase Program Management

## Program Backlog Format
Every phase ticket should include:

- Problem statement.
- Scope and non-goals.
- Target modules and migration notes.
- Test plan.
- Rollout and rollback strategy.
- Acceptance criteria.

## Risk Register

- Dependency drift risk.
- Large-repo latency regression risk.
- Schema migration safety risk.
- Observability payload bloat risk.
- Security redaction false-negative risk.

Each risk must have an owner, detection metric, and rollback trigger.

## Metrics and Gates
Program-level metrics tracked per release:

- Index throughput (files/sec) and p95 index duration.
- Query p50/p95 latency by tool.
- Gold query top-5 hit rate.
- Parse failure rate and unresolved diagnostics count.
- Strict-profile preflight pass rate.
- Redaction leakage count (must remain zero).

## Suggested Sprint Grouping

- Sprint 1 to 2: Phases 1, 2, 3, 3A.
- Sprint 3 to 4: Phases 4, 5, 6.
- Sprint 5 to 6: Phases 7, 8.
- Sprint 7 to 8: Phases 9, 10.
- Sprint 9 to 10: Phases 11, 12, 13, 13A.
- Sprint 11+: Phases 14 and 15.

## Final Program Exit Checklist

- All phase exit criteria satisfied.
- Release gates green for two consecutive runs.
- Real-repo benchmark run recorded and passing.
- Strict-profile Tree-sitter capability matrix recorded and passing.
- Reference remote control-plane smoke tests passing.
- Runbooks updated for new operational paths.
- README and integration docs synchronized.
