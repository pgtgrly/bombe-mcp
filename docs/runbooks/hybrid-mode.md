# Hybrid Mode Runbook

## Purpose

Run Bombe with local-first query execution plus optional control-plane sync for artifact reuse across agents and sessions.

Hybrid mode must never block local query serving.

## Components

- Sync client: `src/bombe/sync/client.py`
- Reconciliation policy: `src/bombe/sync/reconcile.py`
- Compatibility checks:
  - tool major version
  - delta schema version
  - artifact schema version
  - snapshot lineage compatibility
- Reliability controls:
  - bounded timeouts
  - circuit breaker
  - artifact quarantine

## Operating sequence

1. Build local delta from indexed changes.
2. Push delta asynchronously with timeout budget.
3. Pull latest compatible artifact by repo and snapshot lineage.
4. Validate artifact checksum and compatibility.
5. Reconcile:
  - local changes win for touched files/symbols/edges,
  - artifact data reused for untouched scope.

## Failure behavior

- Push timeout/error:
  - result mode must be `local_fallback`.
- Pull timeout/error:
  - result mode must be `local_fallback`.
- Incompatible artifact:
  - skip artifact and continue local.
- Corrupt artifact checksum:
  - add artifact to quarantine store and do not apply.
- Repeated failures:
  - circuit breaker opens and blocks new remote attempts until reset timeout.

## Verification commands

Run sync and reconciliation tests:

```bash
PYTHONPATH=src python3 -m unittest tests.test_sync_client tests.test_sync_reconcile -v
```

Run full suite:

```bash
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
```

## Promotion guardrails

Promotion from local delta to shared artifact must fail when:

- ambiguity rate exceeds configured threshold,
- parse failure count exceeds threshold,
- promotable symbols/edges are absent.

Only edges above configured confidence are promoted.

## Exit criteria

Hybrid mode is healthy when:

- sync tests pass,
- corruption and incompatibility paths degrade to local fallback,
- reconciliation preserves touched-scope local precedence,
- circuit breaker recovers after remote path restoration.
