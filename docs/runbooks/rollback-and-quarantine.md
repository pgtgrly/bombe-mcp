# Rollback and Quarantine Runbook

## Purpose

Provide safe rollback and containment for release-gate failures, sync corruption, or schema migration regressions.

## Trigger conditions

- Release gate evaluator returns `RELEASE_GATES=FAIL`.
- Artifact checksum mismatch detected.
- Artifact compatibility validation fails repeatedly.
- New migration causes startup failures.
- Query correctness or latency regression appears after a change.

## Rollback strategy

1. Stop deploying new artifacts/commits.
2. Keep local query path active.
3. Revert to last known-good commit in a controlled branch.
4. Re-run:
   - compile
   - unit/integration tests
   - perf suites
   - release gate evaluation
5. Promote only after all gates pass.

## Quarantine strategy

When artifact corruption is detected:

1. Add artifact ID to quarantine store.
2. Reject pull application for quarantined artifact IDs.
3. Pull previous compatible artifact version if available.
4. If no safe artifact exists, stay in local fallback mode.

## Schema migration rollback guidance

- Database migrations are stepwise (`vN -> vN+1`).
- If migration fails:
  - inspect migration step and `repo_meta.schema_version`,
  - restore from backup/snapshot where available,
  - rerun startup with migration logs enabled,
  - ship migration fix with regression tests before reattempt.

## Release gate recovery workflow

1. Capture latest perf history file.
2. Evaluate violations:

```bash
PYTHONPATH=src python3 -m bombe.release.gates --history /tmp/bombe-perf-history.jsonl
```

3. Isolate suite with failure:
   - index
   - incremental
   - query
   - workflow_gates
4. Patch issue and rerun affected suite.
5. Rerun full perf suite and gates before merging.

## Final validation checklist

- `PYTHONPATH=src python3 -m compileall src tests` passes.
- `PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"` passes.
- `BOMBE_RUN_PERF=1` perf suites pass.
- release gate evaluation passes.
- no unreviewed diff remains in changed runtime modules.
