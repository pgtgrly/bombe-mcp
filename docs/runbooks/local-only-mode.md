# Local-Only Mode Runbook

## Purpose

Operate Bombe without control-plane sync dependencies.
Use this mode for offline development, deterministic local debugging, and incident containment when remote artifacts are unavailable or untrusted.

## Preconditions

- Repository is available locally.
- Python environment has Bombe dependencies installed.
- Write access is available for local database path (default: `<repo>/.bombe/bombe.db`).

## Startup procedure

1. Validate project checks:

```bash
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
```

2. Initialize local storage:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /absolute/repo/path --init-only --log-level INFO
```

3. Start local server:

```bash
PYTHONPATH=src python3 -m bombe.server --repo /absolute/repo/path --log-level INFO
```

## Validation checklist

- Database schema initializes without migration errors.
- MCP tool registry includes all expected tools.
- Search and references calls return local data.
- No hybrid sync path is required for serving queries.

## Local-only troubleshooting

- Schema mismatch error:
  - delete test database and re-run init in non-production environments.
  - verify `SCHEMA_VERSION` handling in `src/bombe/store/database.py`.
- Missing symbols:
  - run indexing pipeline and confirm `symbols` table has rows.
- Performance regressions:
  - run perf suites with local history file and compare trends:

```bash
BOMBE_RUN_PERF=1 BOMBE_PERF_HISTORY=/tmp/bombe-local-perf.jsonl PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
```

## Exit criteria

Local-only mode is healthy when:

- all tests pass,
- expected MCP payload contracts are satisfied,
- release gates pass using local perf history.
