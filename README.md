# Bombe

Bombe is a structure-aware code retrieval MCP server for AI coding agents.

## Installation

Install runtime dependencies and package:

```bash
python3 -m pip install .
```

Install with development tooling:

```bash
python3 -m pip install ".[dev]"
```

## Development Commands

Run all local checks:

```bash
PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -W error -m unittest discover -s tests -p "test_*.py"
```

Run server initialization smoke test:

```bash
PYTHONPATH=src python3 -m bombe.server --repo . --init-only --log-level INFO
```

Run perf checks and persist trend history:

```bash
BOMBE_RUN_PERF=1 PYTHONPATH=src python3 -m unittest discover -s tests/perf -p "test_*.py" -v
```

Perf metrics are appended to `/tmp/bombe-perf-history.jsonl` by default.
Override with `BOMBE_PERF_HISTORY=/absolute/path/history.jsonl`.

## MCP Tools

`search_symbols`:

```json
{"query":"auth", "kind":"function", "limit":20}
```

`get_references`:

```json
{"symbol_name":"app.auth.authenticate", "direction":"both", "depth":2}
```

`get_context`:

```json
{"query":"authenticate flow", "entry_points":["app.auth.authenticate"], "token_budget":1200}
```

`get_structure`:

```json
{"path":".", "token_budget":4000, "include_signatures":true}
```

`get_blast_radius`:

```json
{"symbol_name":"app.auth.authenticate", "change_type":"behavior", "max_depth":3}
```

`trace_data_flow`:

```json
{"symbol_name":"app.auth.authenticate", "direction":"both", "max_depth":3}
```

`change_impact`:

```json
{"symbol_name":"app.auth.authenticate", "change_type":"signature", "max_depth":3}
```

## Status

Active implementation toward spec-complete MVP.
