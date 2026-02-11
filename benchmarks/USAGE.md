# Bombe Benchmark Suite: Usage Guide

## Prerequisites

- Rust toolchain (stable, 1.70+)
- cargo and criterion (auto-installed as dev-dependency)

## Running Benchmarks

### All Benchmark Groups

```bash
cargo bench --manifest-path crates/bombe-core/Cargo.toml
```

### Specific Groups

```bash
# Only query engines:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- query_engines

# Only indexing:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- indexing

# Only guards:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- guards

# Only PageRank:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- pagerank

# Pattern matching (e.g., all context benchmarks):
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- context

# Multiple patterns:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- "guards|hybrid"
```

### Available Benchmark Groups

| Group | Command Filter | Description |
|-------|---------------|-------------|
| Schema init | `schema_init` | DDL + migration timing |
| Schema DDL | `schema_ddl` | DDL statements only |
| Schema migration | `schema_migration` | Noop migration on current version |
| Guards | `guards` | Clamping, truncation, adaptive cap |
| Token estimation | `token_estimation` | Token counting at various sizes |
| Hybrid scoring | `hybrid_scoring` | Lexical, structural, rank_symbol |
| Symbol helpers | `symbol_helpers` | Module names, visibility, parameters |
| PageRank | `pagerank` | Graph scoring on 10-500 node graphs |
| Schema version | `schema_version` | Hot-path version lookup |
| Query engines | `query_engines` | All 7 query `_impl` functions at 3 scales |
| Indexing | `indexing` | Symbol extraction, call graph, FTS, bulk inserts |

### Viewing Criterion HTML Reports

After running benchmarks, Criterion generates HTML reports:

```bash
open target/criterion/report/index.html
```

Each benchmark has detailed statistics, histograms, and regression analysis.

## Understanding the Output

Criterion reports three values per benchmark:

```
query_engines/search_fts/medium
                    time:   [1.7614 ms 1.7866 ms 1.8162 ms]
```

These are: [lower bound, estimate, upper bound] of the 95% confidence interval.

On subsequent runs, Criterion also reports change detection:

```
                    change: [-5.38% -4.12% -2.76%] (p = 0.00 < 0.05)
                    Performance has improved.
```

### Interpreting Results

- **The middle value (estimate)** is the best single-number summary.
- **Narrow confidence intervals** indicate stable measurements.
- **"Performance has improved/regressed"** is relative to the previous run stored in `target/criterion/`.
- **"Change within noise threshold"** means the difference is not statistically significant.
- **Outlier detection** flags measurements affected by OS scheduling or other interference.

## Benchmark Scales

Query engine benchmarks run at three synthetic data scales:

| Scale | Files | Symbols/File | Total Symbols | Total Edges (approx) |
|-------|-------|-------------|---------------|---------------------|
| small | 10 | 10 | 100 | ~185 |
| medium | 25 | 20 | 500 | ~924 |
| large | 50 | 40 | 2000 | ~3700 |

The synthetic graph includes:
- **Chain edges**: Linear sequence of CALLS
- **Fan-out**: Every 3rd symbol calls 5 positions ahead
- **Cross-file**: Every 7th symbol calls n/2 positions ahead (wrapping)
- **Type hierarchy**: Every 4th EXTENDS the next, every 8th IMPLEMENTS
- **FTS index**: All symbols indexed for full-text search

## Adding Custom Benchmarks

Add new benchmark functions to `crates/bombe-core/benches/core_bench.rs`:

```rust
group.bench_function("my_custom_benchmark", |b| {
    let conn = create_bench_db("medium");
    b.iter(|| {
        let result = my_function(black_box(&conn), args);
        black_box(result);
    });
});
```

Helper functions available in `core_bench.rs`:
- `setup_db()` — Creates a fresh in-memory database with schema
- `create_bench_db(scale)` — Creates a database populated at the given scale ("small", "medium", "large")
- `populate_graph(conn, n)` — Inserts n symbols with chain edges
- `populate_realistic_graph(conn, n_files, symbols_per_file)` — Multi-file, multi-language graph with diverse edge types

## Troubleshooting

### "Cargo.toml not found"

Run from the project root:
```bash
cd /path/to/bombe-mcp
cargo bench --manifest-path crates/bombe-core/Cargo.toml
```

### Benchmarks take too long

Run a specific group instead of the full suite:
```bash
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- search_fts
```

### High variance in results

- Close other CPU-intensive applications
- Run on a quiet machine (not a shared CI runner)
- The median/estimate is the most reliable metric; outliers are flagged by Criterion
- Context benchmarks have inherent variance from FTS seed picking

### Comparing across runs

Criterion stores baseline results in `target/criterion/`. To reset baselines:
```bash
rm -rf target/criterion/
cargo bench --manifest-path crates/bombe-core/Cargo.toml
```
