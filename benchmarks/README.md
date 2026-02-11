# Bombe Benchmark Suite

Criterion-based performance benchmarks for the Rust query engine and indexing pipeline in `bombe-core`.

All times use `us` for microseconds, `ms` for milliseconds, `ns` for nanoseconds (ASCII equivalents of SI symbols).

## Architecture

```
crates/bombe-core/
  benches/
    core_bench.rs   - Criterion benchmarks (all groups)
  src/
    query/          - 7 query engine _impl functions
    indexer/        - Symbol extraction + call graph construction
    store/          - Schema init, migration, version check

benchmarks/
  README.md         - This file
  USAGE.md          - Detailed usage guide
```

## Benchmark Groups

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

## Quick Start

```bash
# Run all benchmark groups:
cargo bench --manifest-path crates/bombe-core/Cargo.toml

# Run only query engine benchmarks:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- query_engines

# Run only indexing benchmarks:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- indexing

# Run a specific benchmark by pattern:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- search_fts

# Run guards + scoring together:
cargo bench --manifest-path crates/bombe-core/Cargo.toml -- "guards|hybrid"
```

See [USAGE.md](USAGE.md) for the complete usage guide.

## Methodology

- **Framework**: Criterion 0.5 with 100 samples, 3-second warmup, 10-second measurement window for query engines.
- **Synthetic data**: In-memory SQLite databases generated at three scales (small: 100, medium: 500, large: 2000 symbols) with chain edges, fan-out, cross-file links, EXTENDS/IMPLEMENTS edges, and FTS population.
- **What's measured**: Rust `_impl` functions called directly with a pre-opened `&Connection`. No Python overhead, no connection setup, no guardrails — pure query execution time.
- **Output**: Mean with 95% confidence intervals and outlier detection. Criterion also generates HTML reports with histograms and regression analysis.

## Reproducing

```bash
# 1. Ensure Rust toolchain is installed
rustup --version  # or install via https://rustup.rs

# 2. Run all benchmarks
cargo bench --manifest-path crates/bombe-core/Cargo.toml 2>&1 | tee bench_results.txt

# 3. View HTML reports
open target/criterion/report/index.html
```

## Adding Custom Benchmarks

Add to `crates/bombe-core/benches/core_bench.rs`:

```rust
group.bench_function("my_custom_benchmark", |b| {
    let conn = create_bench_db("medium");
    b.iter(|| {
        let result = my_function(black_box(&conn), args);
        black_box(result);
    });
});
```

See existing benchmark groups in `core_bench.rs` for patterns.
