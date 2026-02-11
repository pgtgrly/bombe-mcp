//! Criterion benchmarks for bombe-core.
//!
//! These benchmarks exercise the pure-Rust internals that do NOT require a
//! Python runtime.  Functions decorated with `#[pyfunction]` are still plain
//! Rust functions at the language level -- PyO3 merely wraps them -- so they can
//! be called directly from Rust benchmark code.
//!
//! ## Benchmark groups
//!
//! 1. **schema** — DDL init + migration overhead.
//! 2. **guards** — Input clamping / truncation.
//! 3. **token_estimation** — Token counting at various text sizes.
//! 4. **hybrid_scoring** — Lexical, structural, and composite ranking.
//! 5. **symbol_helpers** — Module-name derivation, visibility, parameter parsing.
//! 6. **pagerank** — Convergence on synthetic graphs.
//! 7. **query_engines** — All 7 query `_impl` functions on realistic data.
//!
//! ## Running
//!
//! ```sh
//! cargo bench --manifest-path crates/bombe-core/Cargo.toml
//! # Run only the query engine group:
//! cargo bench --manifest-path crates/bombe-core/Cargo.toml -- query_engines
//! ```

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rusqlite::Connection;

// Re-export crate under a friendlier alias.  The lib target is called
// `_bombe_core` (matching the Python extension module name).
use _bombe_core::indexer::callgraph::build_call_edges;
use _bombe_core::indexer::pagerank::recompute_pagerank_impl;
use _bombe_core::indexer::symbols::{
    build_parameters, extract_symbols, to_module_name, visibility,
};
use _bombe_core::query::blast::get_blast_radius_impl;
use _bombe_core::query::change_impact::change_impact_impl;
use _bombe_core::query::context::get_context_impl;
use _bombe_core::query::data_flow::trace_data_flow_impl;
use _bombe_core::query::guards::{
    adaptive_graph_cap, clamp_budget, clamp_depth, clamp_int, clamp_limit, truncate_query,
};
use _bombe_core::query::hybrid::{lexical_score, rank_symbol, structural_score};
use _bombe_core::query::references::get_references_impl;
use _bombe_core::query::search::search_symbols_impl;
use _bombe_core::query::structure::get_structure_impl;
use _bombe_core::query::tokenizer::estimate_tokens;
use _bombe_core::store::schema::{
    migrate_schema, FTS_STATEMENTS, SCHEMA_STATEMENTS, SCHEMA_VERSION,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Create a fresh in-memory database with the full Bombe schema applied and
/// migrated to the latest version.
fn setup_db() -> Connection {
    let conn = Connection::open_in_memory().unwrap();
    conn.execute_batch("PRAGMA foreign_keys = ON;").unwrap();
    for stmt in SCHEMA_STATEMENTS {
        conn.execute_batch(stmt).unwrap();
    }
    for stmt in FTS_STATEMENTS {
        let _ = conn.execute_batch(stmt);
    }
    migrate_schema(&conn).unwrap();
    conn
}

/// Insert `n` synthetic symbol nodes and a chain of CALLS edges between
/// consecutive symbols so that PageRank has non-trivial work to do.
fn populate_graph(conn: &Connection, n: usize) {
    // Insert a dummy file entry.
    conn.execute(
        "INSERT OR IGNORE INTO files(path, language, content_hash) VALUES ('bench.py', 'python', 'abc123');",
        [],
    )
    .unwrap();

    // Insert symbols.
    for i in 0..n {
        conn.execute(
            "INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line) \
             VALUES (?1, ?2, 'function', 'bench.py', ?3, ?4);",
            rusqlite::params![
                format!("func_{i}"),
                format!("bench.func_{i}"),
                (i * 10) as i64,
                (i * 10 + 5) as i64,
            ],
        )
        .unwrap();
    }

    // Retrieve the auto-generated symbol IDs.
    let mut stmt = conn.prepare("SELECT id FROM symbols ORDER BY id;").unwrap();
    let ids: Vec<i64> = stmt
        .query_map([], |row| row.get(0))
        .unwrap()
        .filter_map(|r| r.ok())
        .collect();

    // Create a chain of CALLS edges: func_0 -> func_1 -> func_2 -> ...
    for window in ids.windows(2) {
        conn.execute(
            "INSERT INTO edges(source_id, target_id, source_type, target_type, relationship) \
             VALUES (?1, ?2, 'symbol', 'symbol', 'CALLS');",
            rusqlite::params![window[0], window[1]],
        )
        .unwrap();
    }

    // Add some fan-out edges so the graph is not purely linear.
    // Every 5th node also calls the node 3 positions ahead (if it exists).
    for i in (0..ids.len()).step_by(5) {
        if i + 3 < ids.len() {
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, target_type, relationship) \
                 VALUES (?1, ?2, 'symbol', 'symbol', 'CALLS');",
                rusqlite::params![ids[i], ids[i + 3]],
            )
            .unwrap();
        }
    }
}

/// Create a realistic benchmark database with multiple files, diverse symbol
/// kinds, edges of various types, FTS entries, and PageRank scores.
///
/// This mirrors a real codebase graph far better than the simple chain used by
/// `populate_graph`.
fn populate_realistic_graph(conn: &Connection, n_files: usize, symbols_per_file: usize) {
    let kinds = ["class", "function", "method", "interface"];
    let languages = ["java", "python", "typescript", "go"];

    // Insert files.
    for f in 0..n_files {
        let lang = languages[f % languages.len()];
        let ext = match lang {
            "java" => "java",
            "python" => "py",
            "typescript" => "ts",
            "go" => "go",
            _ => "txt",
        };
        conn.execute(
            "INSERT OR IGNORE INTO files(path, language, content_hash) \
             VALUES (?1, ?2, ?3);",
            rusqlite::params![
                format!("src/pkg{}/module_{f}.{ext}", f / 4),
                lang,
                format!("hash_{f}"),
            ],
        )
        .unwrap();
    }

    // Gather file paths.
    let file_paths: Vec<String> = {
        let mut stmt = conn
            .prepare("SELECT path FROM files ORDER BY path;")
            .unwrap();
        stmt.query_map([], |row| row.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect()
    };

    // Insert symbols — each file gets `symbols_per_file` symbols.
    let mut sym_count: usize = 0;
    for fp in &file_paths {
        for s in 0..symbols_per_file {
            let kind = kinds[s % kinds.len()];
            let name = match kind {
                "class" => format!("Class{sym_count}"),
                "interface" => format!("IService{sym_count}"),
                "method" => format!("process_{sym_count}"),
                _ => format!("func_{sym_count}"),
            };
            let qname = format!("pkg.{name}");
            let sig = format!("{kind} {name}(arg0: i32, arg1: String) -> Result");
            let pagerank = 1.0 / (1.0 + sym_count as f64);
            conn.execute(
                "INSERT INTO symbols(name, qualified_name, kind, file_path, \
                 start_line, end_line, signature, pagerank_score) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8);",
                rusqlite::params![
                    name,
                    qname,
                    kind,
                    fp,
                    (s * 20 + 1) as i64,
                    (s * 20 + 15) as i64,
                    sig,
                    pagerank,
                ],
            )
            .unwrap();
            sym_count += 1;
        }
    }

    // Retrieve symbol IDs.
    let ids: Vec<i64> = {
        let mut stmt = conn.prepare("SELECT id FROM symbols ORDER BY id;").unwrap();
        stmt.query_map([], |row| row.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect()
    };

    // Insert edges: chain + fan-out + cross-file + EXTENDS/IMPLEMENTS.
    // 1. Chain: func_i CALLS func_{i+1}
    for w in ids.windows(2) {
        conn.execute(
            "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, target_type, relationship) \
             VALUES (?1, ?2, 'symbol', 'symbol', 'CALLS');",
            rusqlite::params![w[0], w[1]],
        )
        .unwrap();
    }
    // 2. Fan-out: every 3rd calls the node 5 ahead
    for i in (0..ids.len()).step_by(3) {
        if i + 5 < ids.len() {
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, target_type, relationship) \
                 VALUES (?1, ?2, 'symbol', 'symbol', 'CALLS');",
                rusqlite::params![ids[i], ids[i + 5]],
            )
            .unwrap();
        }
    }
    // 3. Cross-file: every 7th calls the symbol `n/2` positions ahead (wrapping)
    for i in (0..ids.len()).step_by(7) {
        let target_idx = (i + ids.len() / 2) % ids.len();
        if target_idx != i {
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, target_type, relationship) \
                 VALUES (?1, ?2, 'symbol', 'symbol', 'CALLS');",
                rusqlite::params![ids[i], ids[target_idx]],
            )
            .unwrap();
        }
    }
    // 4. Type hierarchy: every 4th symbol EXTENDS the next class-kind symbol
    for i in (0..ids.len()).step_by(4) {
        let target = (i + 4).min(ids.len() - 1);
        if target != i {
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, target_type, relationship) \
                 VALUES (?1, ?2, 'symbol', 'symbol', 'EXTENDS');",
                rusqlite::params![ids[i], ids[target]],
            )
            .unwrap();
        }
    }
    // 5. IMPLEMENTS: every 8th implements every 16th
    for i in (0..ids.len()).step_by(8) {
        let target = (i + 16) % ids.len();
        if target != i {
            conn.execute(
                "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, target_type, relationship) \
                 VALUES (?1, ?2, 'symbol', 'symbol', 'IMPLEMENTS');",
                rusqlite::params![ids[i], ids[target]],
            )
            .unwrap();
        }
    }

    // Populate FTS index.
    for &id in &ids {
        let row: (String, String, String) = conn
            .query_row(
                "SELECT name, qualified_name, COALESCE(signature, '') FROM symbols WHERE id = ?1;",
                rusqlite::params![id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        let _ = conn.execute(
            "INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature) \
             VALUES (?1, ?2, ?3, '', ?4);",
            rusqlite::params![id, row.0, row.1, row.2],
        );
    }
}

// ---------------------------------------------------------------------------
// Benchmark: Schema initialization & migration
// ---------------------------------------------------------------------------

fn bench_schema_init(c: &mut Criterion) {
    c.bench_function("schema_init_and_migrate", |b| {
        b.iter(|| {
            let conn = Connection::open_in_memory().unwrap();
            conn.execute_batch("PRAGMA foreign_keys = ON;").unwrap();
            for stmt in SCHEMA_STATEMENTS {
                conn.execute_batch(stmt).unwrap();
            }
            for stmt in FTS_STATEMENTS {
                let _ = conn.execute_batch(stmt);
            }
            migrate_schema(&conn).unwrap();
            black_box(&conn);
        });
    });
}

fn bench_schema_ddl_only(c: &mut Criterion) {
    c.bench_function("schema_ddl_statements_only", |b| {
        b.iter(|| {
            let conn = Connection::open_in_memory().unwrap();
            conn.execute_batch("PRAGMA foreign_keys = ON;").unwrap();
            for stmt in SCHEMA_STATEMENTS {
                conn.execute_batch(stmt).unwrap();
            }
            black_box(&conn);
        });
    });
}

fn bench_schema_migration_on_existing(c: &mut Criterion) {
    c.bench_function("schema_migration_noop_on_current", |b| {
        // Pre-create a fully migrated database; measure re-running migrate
        // (should be essentially a no-op version check).
        let conn = setup_db();
        b.iter(|| {
            migrate_schema(black_box(&conn)).unwrap();
        });
    });
}

// ---------------------------------------------------------------------------
// Benchmark: Guard clamping functions
// ---------------------------------------------------------------------------

fn bench_guards(c: &mut Criterion) {
    let mut group = c.benchmark_group("guards");

    group.bench_function("clamp_int", |b| {
        b.iter(|| clamp_int(black_box(150), black_box(1), black_box(100)));
    });

    group.bench_function("clamp_int_within_range", |b| {
        b.iter(|| clamp_int(black_box(50), black_box(1), black_box(100)));
    });

    group.bench_function("clamp_depth", |b| {
        b.iter(|| clamp_depth(black_box(10), black_box(6)));
    });

    group.bench_function("clamp_budget", |b| {
        b.iter(|| clamp_budget(black_box(50000), black_box(1), black_box(32000)));
    });

    group.bench_function("clamp_limit", |b| {
        b.iter(|| clamp_limit(black_box(200), black_box(100)));
    });

    group.bench_function("truncate_query_short", |b| {
        b.iter(|| truncate_query(black_box("find all classes")));
    });

    group.bench_function("truncate_query_long", |b| {
        let long_query = "a".repeat(1024);
        b.iter(|| truncate_query(black_box(&long_query)));
    });

    group.bench_function("truncate_query_with_whitespace", |b| {
        let padded = format!("   {}   ", "search query".repeat(10));
        b.iter(|| truncate_query(black_box(&padded)));
    });

    group.bench_function("adaptive_graph_cap_small_repo", |b| {
        b.iter(|| adaptive_graph_cap(black_box(50), black_box(2000), black_box(Some(200))));
    });

    group.bench_function("adaptive_graph_cap_large_repo", |b| {
        b.iter(|| adaptive_graph_cap(black_box(10000), black_box(2000), black_box(Some(200))));
    });

    group.bench_function("adaptive_graph_cap_no_floor", |b| {
        b.iter(|| adaptive_graph_cap(black_box(5000), black_box(2000), black_box(None)));
    });

    group.finish();
}

// ---------------------------------------------------------------------------
// Benchmark: Token estimation
// ---------------------------------------------------------------------------

fn bench_token_estimation(c: &mut Criterion) {
    let mut group = c.benchmark_group("token_estimation");

    group.bench_function("empty_string", |b| {
        b.iter(|| estimate_tokens(black_box(""), black_box(None)));
    });

    group.bench_function("short_text", |b| {
        b.iter(|| estimate_tokens(black_box("def foo(x, y): return x + y"), black_box(None)));
    });

    let medium_text =
        "fn process_data(input: &str) -> Result<Vec<u8>, Error> { todo!() }\n".repeat(50);
    group.bench_function("medium_text_50_lines", |b| {
        b.iter(|| estimate_tokens(black_box(&medium_text), black_box(None)));
    });

    let large_text =
        "pub struct Field { name: String, value: i64, tags: Vec<String> }\n".repeat(1000);
    group.bench_function("large_text_1000_lines", |b| {
        b.iter(|| estimate_tokens(black_box(&large_text), black_box(None)));
    });

    group.finish();
}

// ---------------------------------------------------------------------------
// Benchmark: Hybrid scoring functions
// ---------------------------------------------------------------------------

fn bench_hybrid_scoring(c: &mut Criterion) {
    let mut group = c.benchmark_group("hybrid_scoring");

    // -- lexical_score -------------------------------------------------------

    group.bench_function("lexical_score_exact_match", |b| {
        b.iter(|| {
            lexical_score(
                black_box("process_data"),
                black_box("process_data"),
                black_box("mymodule.process_data"),
            )
        });
    });

    group.bench_function("lexical_score_partial_match", |b| {
        b.iter(|| {
            lexical_score(
                black_box("process"),
                black_box("process_data"),
                black_box("mymodule.process_data"),
            )
        });
    });

    group.bench_function("lexical_score_token_overlap", |b| {
        b.iter(|| {
            lexical_score(
                black_box("data handler"),
                black_box("process_data_handler"),
                black_box("mymodule.handlers.process_data_handler"),
            )
        });
    });

    group.bench_function("lexical_score_no_match", |b| {
        b.iter(|| {
            lexical_score(
                black_box("completely_unrelated"),
                black_box("process_data"),
                black_box("mymodule.process_data"),
            )
        });
    });

    group.bench_function("lexical_score_empty_query", |b| {
        b.iter(|| {
            lexical_score(
                black_box(""),
                black_box("process_data"),
                black_box("mymodule.process_data"),
            )
        });
    });

    // -- structural_score ----------------------------------------------------

    group.bench_function("structural_score_high_traffic", |b| {
        b.iter(|| structural_score(black_box(0.85), black_box(50), black_box(30)));
    });

    group.bench_function("structural_score_leaf_node", |b| {
        b.iter(|| structural_score(black_box(0.001), black_box(0), black_box(0)));
    });

    group.bench_function("structural_score_zero_pagerank", |b| {
        b.iter(|| structural_score(black_box(0.0), black_box(10), black_box(5)));
    });

    // -- rank_symbol (composite) ---------------------------------------------

    group.bench_function("rank_symbol_typical", |b| {
        b.iter(|| {
            rank_symbol(
                black_box("process"),
                black_box("process_data"),
                black_box("mymodule.process_data"),
                black_box(Some("fn process_data(input: &str) -> Vec<u8>")),
                black_box(Some("Process incoming data and return bytes.")),
                black_box(0.5),
                black_box(10),
                black_box(3),
            )
        });
    });

    group.bench_function("rank_symbol_no_optional_fields", |b| {
        b.iter(|| {
            rank_symbol(
                black_box("find"),
                black_box("find_all"),
                black_box("search.find_all"),
                black_box(None),
                black_box(None),
                black_box(0.1),
                black_box(2),
                black_box(1),
            )
        });
    });

    group.finish();
}

// ---------------------------------------------------------------------------
// Benchmark: Symbol extraction helpers
// ---------------------------------------------------------------------------

fn bench_symbol_helpers(c: &mut Criterion) {
    let mut group = c.benchmark_group("symbol_helpers");

    group.bench_function("to_module_name_simple", |b| {
        b.iter(|| to_module_name(black_box("src/query/guards.py")));
    });

    group.bench_function("to_module_name_deep_path", |b| {
        b.iter(|| to_module_name(black_box("src/bombe/query/federated/planner.py")));
    });

    group.bench_function("visibility_public", |b| {
        b.iter(|| visibility(black_box("process_data")));
    });

    group.bench_function("visibility_private", |b| {
        b.iter(|| visibility(black_box("_internal_helper")));
    });

    group.bench_function("build_parameters_java", |b| {
        b.iter(|| {
            build_parameters(
                black_box("String name, int age, List<String> tags, boolean active"),
                black_box("java"),
            )
        });
    });

    group.bench_function("build_parameters_typescript", |b| {
        b.iter(|| {
            build_parameters(
                black_box("name: string, age: number, tags: string[], active: boolean"),
                black_box("typescript"),
            )
        });
    });

    group.bench_function("build_parameters_go", |b| {
        b.iter(|| {
            build_parameters(
                black_box("name string, age int, tags []string, active bool"),
                black_box("go"),
            )
        });
    });

    group.bench_function("build_parameters_empty", |b| {
        b.iter(|| build_parameters(black_box(""), black_box("java")));
    });

    group.finish();
}

// ---------------------------------------------------------------------------
// Benchmark: PageRank on synthetic graphs
// ---------------------------------------------------------------------------

fn bench_pagerank(c: &mut Criterion) {
    let mut group = c.benchmark_group("pagerank");
    // PageRank convergence can be slow, so allow longer measurement times.
    group.measurement_time(std::time::Duration::from_secs(10));

    for &node_count in &[10, 50, 100, 500] {
        group.bench_with_input(
            BenchmarkId::new("recompute", node_count),
            &node_count,
            |b, &n| {
                // Setup: create a pre-populated database for each iteration.
                // We recreate the graph fresh each iteration to avoid score
                // drift from repeated PageRank runs on the same DB.
                b.iter_with_setup(
                    || {
                        let conn = setup_db();
                        populate_graph(&conn, n);
                        conn
                    },
                    |conn| {
                        recompute_pagerank_impl(&conn, 0.85, 1e-6).unwrap();
                        black_box(&conn);
                    },
                );
            },
        );
    }

    group.finish();
}

// ---------------------------------------------------------------------------
// Benchmark: Schema version check (hot path in query engines)
// ---------------------------------------------------------------------------

fn bench_schema_version_check(c: &mut Criterion) {
    let conn = setup_db();
    c.bench_function("schema_version_read", |b| {
        b.iter(|| {
            let version: String = conn
                .query_row(
                    "SELECT value FROM repo_meta WHERE key = 'schema_version';",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(black_box(version).parse::<i32>().unwrap(), SCHEMA_VERSION);
        });
    });
}

// ---------------------------------------------------------------------------
// Benchmark: Query engines on realistic synthetic data
// ---------------------------------------------------------------------------

/// Helper: create a database at a specific scale and return it.
///
/// Scales:
/// - "small"  → 10 files × 10 symbols = 100 symbols
/// - "medium" → 25 files × 20 symbols = 500 symbols
/// - "large"  → 50 files × 40 symbols = 2000 symbols
fn create_bench_db(scale: &str) -> Connection {
    let conn = setup_db();
    let (files, syms) = match scale {
        "small" => (10, 10),
        "medium" => (25, 20),
        "large" => (50, 40),
        _ => (10, 10),
    };
    populate_realistic_graph(&conn, files, syms);
    conn
}

/// Get a symbol name that exists in the bench database for benchmarking.
fn get_bench_symbol(conn: &Connection, offset: usize) -> String {
    let name: String = conn
        .query_row(
            "SELECT qualified_name FROM symbols ORDER BY pagerank_score DESC LIMIT 1 OFFSET ?1;",
            rusqlite::params![offset as i64],
            |row| row.get(0),
        )
        .unwrap();
    name
}

/// Get a file path that exists in the bench database for benchmarking.
fn get_bench_file(conn: &Connection) -> String {
    let path: String = conn
        .query_row(
            "SELECT file_path FROM symbols GROUP BY file_path \
             ORDER BY COUNT(*) DESC LIMIT 1;",
            [],
            |row| row.get(0),
        )
        .unwrap();
    path
}

fn bench_query_engines(c: &mut Criterion) {
    let mut group = c.benchmark_group("query_engines");
    group.measurement_time(std::time::Duration::from_secs(10));

    // ---- search_symbols_impl ------------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(BenchmarkId::new("search_fts", scale), scale, |b, &scale| {
            let conn = create_bench_db(scale);
            b.iter(|| {
                let result =
                    search_symbols_impl(black_box(&conn), "func", "any", None, 20).unwrap();
                black_box(result);
            });
        });
    }

    group.bench_function("search_kind_filter", |b| {
        let conn = create_bench_db("medium");
        b.iter(|| {
            let result = search_symbols_impl(black_box(&conn), "Class", "class", None, 20).unwrap();
            black_box(result);
        });
    });

    group.bench_function("search_file_pattern", |b| {
        let conn = create_bench_db("medium");
        b.iter(|| {
            let result =
                search_symbols_impl(black_box(&conn), "func", "any", Some("src/pkg0/%"), 20)
                    .unwrap();
            black_box(result);
        });
    });

    // ---- get_references_impl ------------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(
            BenchmarkId::new("references_callers", scale),
            scale,
            |b, &scale| {
                let conn = create_bench_db(scale);
                let sym = get_bench_symbol(&conn, 5);
                b.iter(|| {
                    let result =
                        get_references_impl(black_box(&conn), &sym, "callers", 2, false).unwrap();
                    black_box(result);
                });
            },
        );
    }

    group.bench_function("references_both_directions", |b| {
        let conn = create_bench_db("medium");
        let sym = get_bench_symbol(&conn, 5);
        b.iter(|| {
            let result = get_references_impl(black_box(&conn), &sym, "both", 2, false).unwrap();
            black_box(result);
        });
    });

    group.bench_function("references_deep", |b| {
        let conn = create_bench_db("medium");
        let sym = get_bench_symbol(&conn, 5);
        b.iter(|| {
            let result = get_references_impl(black_box(&conn), &sym, "callers", 5, false).unwrap();
            black_box(result);
        });
    });

    // ---- get_context_impl ---------------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(
            BenchmarkId::new("context_assembly", scale),
            scale,
            |b, &scale| {
                let conn = create_bench_db(scale);
                b.iter(|| {
                    let result =
                        get_context_impl(black_box(&conn), "func", &[], 8000, false, 2).unwrap();
                    black_box(result);
                });
            },
        );
    }

    group.bench_function("context_with_entry_points", |b| {
        let conn = create_bench_db("medium");
        let sym = get_bench_symbol(&conn, 0);
        let entries = vec![sym];
        b.iter(|| {
            let result =
                get_context_impl(black_box(&conn), "func", &entries, 8000, false, 2).unwrap();
            black_box(result);
        });
    });

    group.bench_function("context_signatures_only", |b| {
        let conn = create_bench_db("medium");
        b.iter(|| {
            let result = get_context_impl(black_box(&conn), "func", &[], 8000, true, 2).unwrap();
            black_box(result);
        });
    });

    group.bench_function("context_deep_expansion", |b| {
        let conn = create_bench_db("medium");
        b.iter(|| {
            let result = get_context_impl(black_box(&conn), "func", &[], 16000, false, 4).unwrap();
            black_box(result);
        });
    });

    // ---- get_blast_radius_impl ----------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(
            BenchmarkId::new("blast_radius", scale),
            scale,
            |b, &scale| {
                let conn = create_bench_db(scale);
                let sym = get_bench_symbol(&conn, 10);
                b.iter(|| {
                    let result =
                        get_blast_radius_impl(black_box(&conn), &sym, "modified", 3).unwrap();
                    black_box(result);
                });
            },
        );
    }

    // ---- get_structure_impl -------------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(BenchmarkId::new("structure", scale), scale, |b, &scale| {
            let conn = create_bench_db(scale);
            b.iter(|| {
                let result = get_structure_impl(black_box(&conn), ".", 4000, true).unwrap();
                black_box(result);
            });
        });
    }

    group.bench_function("structure_filtered_path", |b| {
        let conn = create_bench_db("medium");
        let file = get_bench_file(&conn);
        let dir = file.rsplit_once('/').map(|(d, _)| d).unwrap_or(".");
        b.iter(|| {
            let result = get_structure_impl(black_box(&conn), dir, 4000, true).unwrap();
            black_box(result);
        });
    });

    // ---- change_impact_impl -------------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(
            BenchmarkId::new("change_impact", scale),
            scale,
            |b, &scale| {
                let conn = create_bench_db(scale);
                let sym = get_bench_symbol(&conn, 3);
                b.iter(|| {
                    let result = change_impact_impl(black_box(&conn), &sym, "behavior", 3).unwrap();
                    black_box(result);
                });
            },
        );
    }

    // ---- trace_data_flow_impl -----------------------------------------------

    for scale in &["small", "medium", "large"] {
        group.bench_with_input(
            BenchmarkId::new("data_flow_both", scale),
            scale,
            |b, &scale| {
                let conn = create_bench_db(scale);
                let sym = get_bench_symbol(&conn, 5);
                b.iter(|| {
                    let result = trace_data_flow_impl(black_box(&conn), &sym, "both", 3).unwrap();
                    black_box(result);
                });
            },
        );
    }

    group.bench_function("data_flow_upstream_only", |b| {
        let conn = create_bench_db("medium");
        let sym = get_bench_symbol(&conn, 10);
        b.iter(|| {
            let result = trace_data_flow_impl(black_box(&conn), &sym, "upstream", 3).unwrap();
            black_box(result);
        });
    });

    group.bench_function("data_flow_downstream_only", |b| {
        let conn = create_bench_db("medium");
        let sym = get_bench_symbol(&conn, 10);
        b.iter(|| {
            let result = trace_data_flow_impl(black_box(&conn), &sym, "downstream", 3).unwrap();
            black_box(result);
        });
    });

    group.finish();
}

// ---------------------------------------------------------------------------
// Benchmark: Indexing — symbol extraction, call graph, DB writes
// ---------------------------------------------------------------------------

// Realistic source samples for each supported language.
const JAVA_SOURCE: &str = r#"package com.example.service;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import com.example.auth.AuthService;
import com.example.model.User;

public class UserService {
    private final AuthService auth;

    public UserService(AuthService auth) {
        this.auth = auth;
    }

    public User findUser(String id) {
        return auth.validate(id);
    }

    public List<User> listUsers(int limit) {
        return getAll().stream().limit(limit).collect(Collectors.toList());
    }

    private List<User> getAll() {
        return List.of();
    }

    public static Map<String, User> buildIndex(List<User> users) {
        return users.stream().collect(Collectors.toMap(User::getId, u -> u));
    }
}

interface Repository<T> {
    T findById(String id);
    List<T> findAll();
    void save(T entity);
    void delete(String id);
}

class UserRepository implements Repository<User> {
    public User findById(String id) { return null; }
    public List<User> findAll() { return List.of(); }
    public void save(User entity) {}
    public void delete(String id) {}
}
"#;

const TYPESCRIPT_SOURCE: &str = r#"import { User, UserRole } from './models';
import { AuthService } from './auth';
import { Logger } from '../utils/logger';

export interface UserPort {
  findUser(id: string): Promise<User | null>;
  listUsers(limit: number): Promise<User[]>;
  createUser(name: string, role: UserRole): Promise<User>;
}

export const DEFAULT_LIMIT = 100;
export const MAX_RETRIES = 3;

export class UserServiceImpl implements UserPort {
  private logger: Logger;
  private auth: AuthService;

  constructor(auth: AuthService, logger: Logger) {
    this.auth = auth;
    this.logger = logger;
  }

  async findUser(id: string): Promise<User | null> {
    this.logger.info(`Finding user ${id}`);
    const valid = await this.auth.validate(id);
    if (!valid) return null;
    return this.fetchFromDb(id);
  }

  async listUsers(limit: number = DEFAULT_LIMIT): Promise<User[]> {
    return this.fetchAll().then(users => users.slice(0, limit));
  }

  async createUser(name: string, role: UserRole): Promise<User> {
    this.logger.info(`Creating user ${name}`);
    return { id: crypto.randomUUID(), name, role };
  }

  private async fetchFromDb(id: string): Promise<User | null> {
    return null;
  }

  private async fetchAll(): Promise<User[]> {
    return [];
  }
}

export const createService = (auth: AuthService, logger: Logger): UserPort => {
  return new UserServiceImpl(auth, logger);
};
"#;

const GO_SOURCE: &str = r#"package service

import (
	"context"
	"errors"
	"fmt"
	"sync"
)

const MaxRetries = 3
const DefaultTimeout = 30

type User struct {
	ID   string
	Name string
	Role string
}

type UserRepository interface {
	FindByID(ctx context.Context, id string) (*User, error)
	FindAll(ctx context.Context) ([]*User, error)
	Save(ctx context.Context, user *User) error
	Delete(ctx context.Context, id string) error
}

type UserService struct {
	repo   UserRepository
	mu     sync.RWMutex
	cache  map[string]*User
}

func NewUserService(repo UserRepository) *UserService {
	return &UserService{
		repo:  repo,
		cache: make(map[string]*User),
	}
}

func (s *UserService) FindUser(ctx context.Context, id string) (*User, error) {
	s.mu.RLock()
	if u, ok := s.cache[id]; ok {
		s.mu.RUnlock()
		return u, nil
	}
	s.mu.RUnlock()

	user, err := s.repo.FindByID(ctx, id)
	if err != nil {
		return nil, fmt.Errorf("find user: %w", err)
	}
	s.mu.Lock()
	s.cache[id] = user
	s.mu.Unlock()
	return user, nil
}

func (s *UserService) ListUsers(ctx context.Context) ([]*User, error) {
	return s.repo.FindAll(ctx)
}

func (s *UserService) CreateUser(ctx context.Context, name, role string) (*User, error) {
	if name == "" {
		return nil, errors.New("name required")
	}
	user := &User{Name: name, Role: role}
	if err := s.repo.Save(ctx, user); err != nil {
		return nil, err
	}
	return user, nil
}
"#;

fn bench_indexing(c: &mut Criterion) {
    let mut group = c.benchmark_group("indexing");

    // ---- Symbol extraction per language ------------------------------------

    group.bench_function("extract_symbols/java", |b| {
        b.iter(|| {
            let (syms, imports) = extract_symbols(
                black_box(JAVA_SOURCE),
                "src/com/example/service/UserService.java",
                "java",
            );
            black_box((syms, imports));
        });
    });

    group.bench_function("extract_symbols/typescript", |b| {
        b.iter(|| {
            let (syms, imports) = extract_symbols(
                black_box(TYPESCRIPT_SOURCE),
                "src/service/user-service.ts",
                "typescript",
            );
            black_box((syms, imports));
        });
    });

    group.bench_function("extract_symbols/go", |b| {
        b.iter(|| {
            let (syms, imports) =
                extract_symbols(black_box(GO_SOURCE), "pkg/service/user.go", "go");
            black_box((syms, imports));
        });
    });

    // ---- Call graph building per language -----------------------------------

    group.bench_function("build_call_edges/java", |b| {
        let (syms, _) = extract_symbols(
            JAVA_SOURCE,
            "src/com/example/service/UserService.java",
            "java",
        );
        b.iter(|| {
            let edges = build_call_edges(
                black_box(JAVA_SOURCE),
                "src/com/example/service/UserService.java",
                "java",
                &syms,
                &syms,
                None,
                None,
            );
            black_box(edges);
        });
    });

    group.bench_function("build_call_edges/typescript", |b| {
        let (syms, _) = extract_symbols(
            TYPESCRIPT_SOURCE,
            "src/service/user-service.ts",
            "typescript",
        );
        b.iter(|| {
            let edges = build_call_edges(
                black_box(TYPESCRIPT_SOURCE),
                "src/service/user-service.ts",
                "typescript",
                &syms,
                &syms,
                None,
                None,
            );
            black_box(edges);
        });
    });

    group.bench_function("build_call_edges/go", |b| {
        let (syms, _) = extract_symbols(GO_SOURCE, "pkg/service/user.go", "go");
        b.iter(|| {
            let edges = build_call_edges(
                black_box(GO_SOURCE),
                "pkg/service/user.go",
                "go",
                &syms,
                &syms,
                None,
                None,
            );
            black_box(edges);
        });
    });

    // ---- Combined: extract + call edges + DB write -------------------------

    for &lang_data in &[
        ("java", JAVA_SOURCE, "src/UserService.java"),
        ("typescript", TYPESCRIPT_SOURCE, "src/user-service.ts"),
        ("go", GO_SOURCE, "pkg/user.go"),
    ] {
        let (lang, source, path) = lang_data;
        group.bench_function(&format!("full_file_index/{lang}"), |b| {
            b.iter_with_setup(
                || setup_db(),
                |conn| {
                    // 1. Insert file record
                    conn.execute(
                        "INSERT OR IGNORE INTO files(path, language, content_hash) VALUES (?1, ?2, 'bench');",
                        rusqlite::params![path, lang],
                    )
                    .unwrap();

                    // 2. Extract symbols
                    let (syms, _imports) = extract_symbols(source, path, lang);

                    // 3. Insert symbols
                    for sym in &syms {
                        conn.execute(
                            "INSERT INTO symbols(name, qualified_name, kind, file_path, \
                             start_line, end_line, signature, visibility) \
                             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8);",
                            rusqlite::params![
                                sym.name,
                                sym.qualified_name,
                                sym.kind,
                                path,
                                sym.start_line,
                                sym.end_line,
                                sym.signature,
                                sym.visibility,
                            ],
                        )
                        .unwrap();
                    }

                    // 4. Build call edges
                    let edges = build_call_edges(source, path, lang, &syms, &syms, None, None);

                    // 5. Insert edges
                    for edge in &edges {
                        let _ = conn.execute(
                            "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, \
                             target_type, relationship, file_path, line_number, confidence) \
                             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8);",
                            rusqlite::params![
                                edge.source_id,
                                edge.target_id,
                                edge.source_type,
                                edge.target_type,
                                edge.relationship,
                                edge.file_path,
                                edge.line_number,
                                edge.confidence,
                            ],
                        );
                    }

                    // 6. Populate FTS
                    for sym in &syms {
                        let id: i64 = conn
                            .query_row(
                                "SELECT id FROM symbols WHERE qualified_name = ?1 AND file_path = ?2;",
                                rusqlite::params![sym.qualified_name, path],
                                |row| row.get(0),
                            )
                            .unwrap_or(0);
                        let _ = conn.execute(
                            "INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature) \
                             VALUES (?1, ?2, ?3, '', ?4);",
                            rusqlite::params![
                                id,
                                sym.name,
                                sym.qualified_name,
                                sym.signature.as_deref().unwrap_or(""),
                            ],
                        );
                    }

                    black_box(&conn);
                },
            );
        });
    }

    // ---- Multi-file indexing (N files of same language) --------------------

    for &n_files in &[10, 50] {
        group.bench_with_input(
            BenchmarkId::new("multi_file_java", n_files),
            &n_files,
            |b, &n| {
                // Generate N variants of the Java source
                let sources: Vec<(String, String)> = (0..n)
                    .map(|i| {
                        let path = format!("src/pkg{}/Service{i}.java", i / 4);
                        let source = JAVA_SOURCE
                            .replace("UserService", &format!("Service{i}"))
                            .replace("UserRepository", &format!("Repo{i}"));
                        (path, source)
                    })
                    .collect();

                b.iter_with_setup(
                    || setup_db(),
                    |conn| {
                        let mut all_syms = Vec::new();
                        for (path, source) in &sources {
                            conn.execute(
                                "INSERT OR IGNORE INTO files(path, language, content_hash) VALUES (?1, 'java', 'bench');",
                                rusqlite::params![path],
                            )
                            .unwrap();
                            let (syms, _) = extract_symbols(source, path, "java");
                            for sym in &syms {
                                conn.execute(
                                    "INSERT INTO symbols(name, qualified_name, kind, file_path, \
                                     start_line, end_line, signature) \
                                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7);",
                                    rusqlite::params![
                                        sym.name,
                                        sym.qualified_name,
                                        sym.kind,
                                        path,
                                        sym.start_line,
                                        sym.end_line,
                                        sym.signature,
                                    ],
                                )
                                .unwrap();
                            }
                            all_syms.extend(syms);
                        }

                        // Build call edges across all files
                        for (path, source) in &sources {
                            let file_syms: Vec<_> = all_syms
                                .iter()
                                .filter(|s| s.file_path == *path)
                                .cloned()
                                .collect();
                            let edges = build_call_edges(
                                source, path, "java", &file_syms, &all_syms, None, None,
                            );
                            for edge in &edges {
                                let _ = conn.execute(
                                    "INSERT OR IGNORE INTO edges(source_id, target_id, source_type, \
                                     target_type, relationship, file_path, line_number) \
                                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7);",
                                    rusqlite::params![
                                        edge.source_id,
                                        edge.target_id,
                                        edge.source_type,
                                        edge.target_type,
                                        edge.relationship,
                                        edge.file_path,
                                        edge.line_number,
                                    ],
                                );
                            }
                        }

                        // PageRank
                        recompute_pagerank_impl(&conn, 0.85, 1e-6).unwrap();

                        black_box(&conn);
                    },
                );
            },
        );
    }

    // ---- FTS population benchmark ------------------------------------------

    group.bench_function("fts_populate_500", |b| {
        b.iter_with_setup(
            || {
                let conn = setup_db();
                populate_realistic_graph(&conn, 25, 20);
                // Clear FTS
                let _ = conn.execute_batch("DELETE FROM symbol_fts;");
                conn
            },
            |conn| {
                let rows: Vec<(i64, String, String, String)> = {
                    let mut stmt = conn
                        .prepare(
                            "SELECT id, name, qualified_name, COALESCE(signature, '') FROM symbols;",
                        )
                        .unwrap();
                    stmt.query_map([], |row| {
                        Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
                    })
                    .unwrap()
                    .filter_map(|r| r.ok())
                    .collect()
                };
                for (id, name, qname, sig) in &rows {
                    let _ = conn.execute(
                        "INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature) \
                         VALUES (?1, ?2, ?3, '', ?4);",
                        rusqlite::params![id, name, qname, sig],
                    );
                }
                black_box(&conn);
            },
        );
    });

    // ---- Symbol insert throughput ------------------------------------------

    group.bench_function("symbol_insert_500", |b| {
        b.iter_with_setup(
            || {
                let conn = setup_db();
                conn.execute(
                    "INSERT INTO files(path, language, content_hash) VALUES ('bench.java', 'java', 'x');",
                    [],
                ).unwrap();
                conn
            },
            |conn| {
                for i in 0..500 {
                    conn.execute(
                        "INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line) \
                         VALUES (?1, ?2, 'function', 'bench.java', ?3, ?4);",
                        rusqlite::params![
                            format!("func_{i}"),
                            format!("pkg.func_{i}"),
                            (i * 10) as i64,
                            (i * 10 + 8) as i64,
                        ],
                    )
                    .unwrap();
                }
                black_box(&conn);
            },
        );
    });

    group.finish();
}

// ---------------------------------------------------------------------------
// Register all benchmark groups
// ---------------------------------------------------------------------------

criterion_group!(
    benches,
    bench_schema_init,
    bench_schema_ddl_only,
    bench_schema_migration_on_existing,
    bench_guards,
    bench_token_estimation,
    bench_hybrid_scoring,
    bench_symbol_helpers,
    bench_pagerank,
    bench_schema_version_check,
    bench_query_engines,
    bench_indexing,
);
criterion_main!(benches);
