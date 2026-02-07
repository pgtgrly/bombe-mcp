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

## Status

Active implementation toward spec-complete MVP.
