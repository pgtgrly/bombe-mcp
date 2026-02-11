//! Context assembly backend for task-oriented code retrieval.
//!
//! Direct port of the Python `bombe.query.context` module (535 LOC).
//! Implements seeded BFS expansion, personalized PageRank, topology-aware
//! ordering, token-budget pruning, and secret redaction.

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::LazyLock;

use pyo3::prelude::*;
use regex::Regex;
use rusqlite::Connection;

use crate::errors::BombeResult;
use crate::query::guards::{
    adaptive_graph_cap, clamp_budget, clamp_depth, truncate_query, MAX_CONTEXT_EXPANSION_DEPTH,
    MAX_CONTEXT_SEEDS, MAX_CONTEXT_TOKEN_BUDGET, MAX_GRAPH_VISITED, MIN_CONTEXT_TOKEN_BUDGET,
};
use crate::query::tokenizer::estimate_tokens;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RELATIONSHIPS: &[&str] = &[
    "CALLS",
    "IMPORTS_SYMBOL",
    "EXTENDS",
    "IMPLEMENTS",
    "HAS_METHOD",
];

static WORD_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[A-Za-z_][A-Za-z0-9_]+").unwrap());

static REDACTION_PATTERNS: LazyLock<Vec<(Regex, &'static str)>> = LazyLock::new(|| {
    vec![
        (
            Regex::new(r"sk-[A-Za-z0-9]{20,}").unwrap(),
            "[REDACTED_OPENAI_KEY]",
        ),
        (
            Regex::new(r"AKIA[0-9A-Z]{16}").unwrap(),
            "[REDACTED_AWS_ACCESS_KEY]",
        ),
        (
            Regex::new(r#"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['"][^'"]+['"]"#).unwrap(),
            r#"$1="[REDACTED]""#,
        ),
        (
            Regex::new(r"(?s)-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----").unwrap(),
            "[REDACTED_PRIVATE_KEY]",
        ),
    ]
});

// ---------------------------------------------------------------------------
// Internal helper: resolve path
// ---------------------------------------------------------------------------

fn resolve_path(file_path: &str) -> std::path::PathBuf {
    let path = std::path::Path::new(file_path);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| std::path::PathBuf::from("."))
            .join(path)
    }
}

// ---------------------------------------------------------------------------
// Internal helper: source fragment
// ---------------------------------------------------------------------------

fn source_fragment(file_path: &str, start_line: i64, end_line: i64) -> String {
    let path = resolve_path(file_path);
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };
    let lines: Vec<&str> = content.split('\n').collect();
    let start_idx = (start_line - 1).max(0) as usize;
    let end_idx = (end_line as usize).min(lines.len());
    if start_idx >= lines.len() || start_idx >= end_idx {
        return String::new();
    }
    lines[start_idx..end_idx].join("\n")
}

// ---------------------------------------------------------------------------
// Internal helper: query terms extraction
// ---------------------------------------------------------------------------

fn query_terms(query: &str) -> HashSet<String> {
    WORD_RE
        .find_iter(query)
        .map(|m| m.as_str().to_lowercase())
        .filter(|t| t.len() >= 2)
        .collect()
}

// ---------------------------------------------------------------------------
// Internal helper: symbol-query relevance scoring
// ---------------------------------------------------------------------------

fn symbol_query_relevance(
    name: &str,
    qualified_name: &str,
    signature: &str,
    terms: &HashSet<String>,
) -> i64 {
    if terms.is_empty() {
        return 0;
    }
    let haystacks = [
        name.to_lowercase(),
        qualified_name.to_lowercase(),
        signature.to_lowercase(),
    ];
    let mut score: i64 = 0;
    for term in terms {
        for haystack in &haystacks {
            if haystack.contains(term.as_str()) {
                score += 1;
                break;
            }
        }
    }
    score
}

// ---------------------------------------------------------------------------
// Internal helper: redact sensitive text
// ---------------------------------------------------------------------------

fn redact_sensitive_text(text: &str) -> (String, i64) {
    let mut redacted = text.to_string();
    let mut redaction_hits: i64 = 0;
    for (pattern, replacement) in REDACTION_PATTERNS.iter() {
        // Count matches first, then replace (mirrors Python's re.subn).
        let count = pattern.find_iter(&redacted).count() as i64;
        redaction_hits += count;
        if count > 0 {
            redacted = pattern.replace_all(&redacted, *replacement).into_owned();
        }
    }
    (redacted, redaction_hits)
}

// ---------------------------------------------------------------------------
// Internal helper: relationship placeholders
// ---------------------------------------------------------------------------

fn rel_placeholders() -> String {
    RELATIONSHIPS
        .iter()
        .enumerate()
        .map(|(i, _)| format!("?{}", i + 1))
        .collect::<Vec<_>>()
        .join(", ")
}

fn rel_params() -> Vec<Box<dyn rusqlite::types::ToSql>> {
    RELATIONSHIPS
        .iter()
        .map(|r| Box::new(r.to_string()) as Box<dyn rusqlite::types::ToSql>)
        .collect()
}

// ---------------------------------------------------------------------------
// Internal helper: pick seeds
// ---------------------------------------------------------------------------

fn pick_seeds(conn: &Connection, query: &str, entry_points: &[String]) -> BombeResult<Vec<i64>> {
    // 1. Try entry points first
    if !entry_points.is_empty() {
        let mut seeds: Vec<i64> = Vec::new();
        for entry in entry_points {
            let result = conn.query_row(
                "SELECT id FROM symbols \
                 WHERE qualified_name = ?1 OR name = ?1 \
                 ORDER BY pagerank_score DESC LIMIT 1;",
                rusqlite::params![entry],
                |row| row.get::<_, i64>(0),
            );
            if let Ok(id) = result {
                seeds.push(id);
            }
        }
        if !seeds.is_empty() {
            return Ok(seeds);
        }
    }

    // 2. Try FTS5 match
    let query_text = query.trim();
    let fts_result: Result<Vec<i64>, rusqlite::Error> = (|| {
        let mut stmt = conn.prepare(
            "SELECT s.id \
             FROM symbol_fts \
             JOIN symbols s ON s.id = symbol_fts.symbol_id \
             WHERE symbol_fts MATCH ?1 \
             ORDER BY bm25(symbol_fts), s.pagerank_score DESC \
             LIMIT 8;",
        )?;
        let rows: Vec<i64> = stmt
            .query_map(rusqlite::params![query_text], |row| row.get(0))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    })();

    if let Ok(ref rows) = fts_result {
        if !rows.is_empty() {
            return Ok(rows.clone());
        }
    }

    // 3. Fallback to LIKE
    let words: Vec<String> = query_text
        .split_whitespace()
        .map(|w| w.trim().to_lowercase())
        .filter(|w| !w.is_empty())
        .collect();
    if words.is_empty() {
        return Ok(Vec::new());
    }

    let name_clauses: Vec<String> = words
        .iter()
        .map(|_| "LOWER(name) LIKE ?".to_string())
        .collect();
    let qname_clauses: Vec<String> = words
        .iter()
        .map(|_| "LOWER(qualified_name) LIKE ?".to_string())
        .collect();
    let all_clauses: Vec<String> = name_clauses.into_iter().chain(qname_clauses).collect();
    let where_clause = all_clauses.join(" OR ");

    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();
    for word in &words {
        params.push(Box::new(format!("%{word}%")));
    }
    for word in &words {
        params.push(Box::new(format!("%{word}%")));
    }

    let sql = format!(
        "SELECT id FROM symbols WHERE {where_clause} \
         ORDER BY pagerank_score DESC LIMIT 8;"
    );

    let mut stmt = conn.prepare(&sql)?;
    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows: Vec<i64> = stmt
        .query_map(param_refs.as_slice(), |row| row.get(0))?
        .filter_map(|r| r.ok())
        .collect();
    Ok(rows)
}

// ---------------------------------------------------------------------------
// Internal helper: BFS expansion
// ---------------------------------------------------------------------------

fn expand(
    conn: &Connection,
    seeds: &[i64],
    depth: i64,
    max_nodes: i64,
) -> BombeResult<HashMap<i64, i64>> {
    let mut reached: HashMap<i64, i64> = HashMap::new();
    let mut queue: VecDeque<(i64, i64)> = VecDeque::new();

    for &seed in seeds {
        reached.insert(seed, 0);
        queue.push_back((seed, 0));
    }

    let placeholders = rel_placeholders();
    let base_param_count = RELATIONSHIPS.len();

    let sql = format!(
        "SELECT source_id, target_id FROM edges \
         WHERE source_type = 'symbol' AND target_type = 'symbol' \
         AND relationship IN ({placeholders}) \
         AND (source_id = ?{} OR target_id = ?{});",
        base_param_count + 1,
        base_param_count + 2
    );

    let mut stmt = conn.prepare(&sql)?;

    while let Some((current, current_depth)) = queue.pop_front() {
        if reached.len() as i64 >= max_nodes {
            break;
        }
        if current_depth >= depth {
            continue;
        }

        let mut params = rel_params();
        params.push(Box::new(current));
        params.push(Box::new(current));
        let param_refs: Vec<&dyn rusqlite::types::ToSql> =
            params.iter().map(|p| p.as_ref()).collect();

        let rows: Vec<(i64, i64)> = stmt
            .query_map(param_refs.as_slice(), |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))
            })?
            .filter_map(|r| r.ok())
            .collect();

        for (source_id, target_id) in rows {
            let neighbor = if source_id == current {
                target_id
            } else {
                source_id
            };
            let next_depth = current_depth + 1;
            let previous = reached.get(&neighbor).copied();
            if previous.is_none() || next_depth < previous.unwrap() {
                reached.insert(neighbor, next_depth);
                if (reached.len() as i64) < max_nodes {
                    queue.push_back((neighbor, next_depth));
                }
            }
        }
    }

    Ok(reached)
}

// ---------------------------------------------------------------------------
// Internal helper: personalized PageRank
// ---------------------------------------------------------------------------

fn personalized_pagerank(
    conn: &Connection,
    seeds: &[i64],
    nodes: &[i64],
    damping: f64,
    iterations: usize,
) -> BombeResult<HashMap<i64, f64>> {
    if nodes.is_empty() {
        return Ok(HashMap::new());
    }

    let node_set: HashSet<i64> = nodes.iter().copied().collect();
    let mut adjacency: HashMap<i64, Vec<i64>> = HashMap::new();
    for &node in nodes {
        adjacency.insert(node, Vec::new());
    }

    // Fetch all relevant edges
    let placeholders = rel_placeholders();
    let sql = format!(
        "SELECT source_id, target_id FROM edges \
         WHERE source_type = 'symbol' AND target_type = 'symbol' \
         AND relationship IN ({placeholders});"
    );
    let mut stmt = conn.prepare(&sql)?;
    let params = rel_params();
    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows: Vec<(i64, i64)> = stmt
        .query_map(param_refs.as_slice(), |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))
        })?
        .filter_map(|r| r.ok())
        .collect();

    for (source, target) in rows {
        if node_set.contains(&source) && node_set.contains(&target) {
            adjacency.entry(source).or_default().push(target);
            adjacency.entry(target).or_default().push(source);
        }
    }

    // Build restart vector
    let seed_set: HashSet<i64> = seeds.iter().copied().collect();
    let seed_count = seed_set.len();
    let mut restart: HashMap<i64, f64> = HashMap::new();
    for &node in nodes {
        let val = if !seed_set.is_empty() && seed_set.contains(&node) {
            1.0 / seed_count as f64
        } else {
            0.0
        };
        restart.insert(node, val);
    }

    let mut scores: HashMap<i64, f64> = restart.clone();

    for _ in 0..iterations {
        let mut next_scores: HashMap<i64, f64> = HashMap::new();
        for &node in nodes {
            next_scores.insert(node, (1.0 - damping) * restart[&node]);
        }
        for (&source, targets) in &adjacency {
            if targets.is_empty() {
                continue;
            }
            let share = damping * scores[&source] / targets.len() as f64;
            for &target in targets {
                *next_scores.entry(target).or_insert(0.0) += share;
            }
        }
        scores = next_scores;
    }

    Ok(scores)
}

// ---------------------------------------------------------------------------
// Internal helper: adjacency for topology ordering
// ---------------------------------------------------------------------------

fn build_adjacency(conn: &Connection, nodes: &[i64]) -> BombeResult<HashMap<i64, HashSet<i64>>> {
    if nodes.is_empty() {
        return Ok(HashMap::new());
    }

    let node_set: HashSet<i64> = nodes.iter().copied().collect();
    let mut adjacency: HashMap<i64, HashSet<i64>> = HashMap::new();
    for &node in nodes {
        adjacency.insert(node, HashSet::new());
    }

    let placeholders = rel_placeholders();
    let sql = format!(
        "SELECT source_id, target_id FROM edges \
         WHERE source_type = 'symbol' AND target_type = 'symbol' \
         AND relationship IN ({placeholders});"
    );
    let mut stmt = conn.prepare(&sql)?;
    let params = rel_params();
    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows: Vec<(i64, i64)> = stmt
        .query_map(param_refs.as_slice(), |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))
        })?
        .filter_map(|r| r.ok())
        .collect();

    for (source, target) in rows {
        if node_set.contains(&source) && node_set.contains(&target) {
            adjacency.entry(source).or_default().insert(target);
            adjacency.entry(target).or_default().insert(source);
        }
    }

    Ok(adjacency)
}

// ---------------------------------------------------------------------------
// Internal: symbol row from DB
// ---------------------------------------------------------------------------

struct SymbolInfo {
    id: i64,
    name: String,
    kind: String,
    qualified_name: String,
    file_path: String,
    start_line: i64,
    end_line: i64,
    signature: String,
    pagerank_score: f64,
}

// ---------------------------------------------------------------------------
// Internal helper: topology ordering
// ---------------------------------------------------------------------------

fn topology_order(
    ranked: &[(f64, SymbolData)],
    seeds: &[i64],
    adjacency: &HashMap<i64, HashSet<i64>>,
) -> Vec<(i64, String)> {
    let score_map: HashMap<i64, f64> = ranked.iter().map(|(score, sym)| (sym.id, *score)).collect();

    // Sort seed ids by score descending
    let seed_set: HashSet<i64> = seeds.iter().copied().collect();
    let mut seed_ids: Vec<i64> = seed_set.iter().copied().collect();
    seed_ids.sort_by(|a, b| {
        let sa = score_map.get(a).copied().unwrap_or(0.0);
        let sb = score_map.get(b).copied().unwrap_or(0.0);
        sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut queue: VecDeque<(i64, String)> = VecDeque::new();
    for seed_id in &seed_ids {
        queue.push_back((*seed_id, "seed".to_string()));
    }

    let mut ordered: Vec<(i64, String)> = Vec::new();
    let mut seen: HashSet<i64> = HashSet::new();

    while let Some((current, reason)) = queue.pop_front() {
        if seen.contains(&current) {
            continue;
        }
        seen.insert(current);
        ordered.push((current, reason));

        // Sort neighbors by score descending
        if let Some(neighbors) = adjacency.get(&current) {
            let mut neighbor_list: Vec<i64> = neighbors.iter().copied().collect();
            neighbor_list.sort_by(|a, b| {
                let sa = score_map.get(a).copied().unwrap_or(0.0);
                let sb = score_map.get(b).copied().unwrap_or(0.0);
                sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
            });
            for neighbor in neighbor_list {
                if !seen.contains(&neighbor) {
                    queue.push_back((neighbor, "graph_neighbor".to_string()));
                }
            }
        }
    }

    // Add remaining symbols not reached by BFS
    let remaining: Vec<(i64, String)> = ranked
        .iter()
        .filter(|(_, sym)| !seen.contains(&sym.id))
        .map(|(_, sym)| (sym.id, "rank_fallback".to_string()))
        .collect();

    ordered.extend(remaining);
    ordered
}

// ---------------------------------------------------------------------------
// Internal helper: quality metrics
// ---------------------------------------------------------------------------

fn quality_metrics(
    included_symbols: &[IncludedSymbol],
    seeds: &[i64],
    token_budget: i64,
    tokens_used: i64,
    adjacency: &HashMap<i64, HashSet<i64>>,
    duplicate_skips: i64,
    redaction_hits: i64,
) -> serde_json::Value {
    if included_symbols.is_empty() {
        return serde_json::json!({
            "seed_hit_rate": 0.0,
            "connectedness": 0.0,
            "token_efficiency": 0.0,
            "avg_depth": 0.0,
            "included_count": 0,
            "dedupe_ratio": 1.0,
            "redaction_hits": redaction_hits,
        });
    }

    let included_ids: HashSet<i64> = included_symbols.iter().map(|s| s.id).collect();
    let seed_set: HashSet<i64> = seeds.iter().copied().collect();
    let included_seed_ids: Vec<i64> = {
        let mut ids: Vec<i64> = included_ids.intersection(&seed_set).copied().collect();
        ids.sort();
        ids
    };
    let seed_hit_rate = included_seed_ids.len() as f64 / (seed_set.len().max(1)) as f64;

    // Connectedness: BFS from included seeds within included set
    let mut connected_ids: HashSet<i64> = HashSet::new();
    let mut bfs_queue: VecDeque<i64> = VecDeque::new();
    for &seed_id in &included_seed_ids {
        bfs_queue.push_back(seed_id);
    }
    while let Some(current) = bfs_queue.pop_front() {
        if connected_ids.contains(&current) {
            continue;
        }
        connected_ids.insert(current);
        if let Some(neighbors) = adjacency.get(&current) {
            for &neighbor in neighbors {
                if included_ids.contains(&neighbor) && !connected_ids.contains(&neighbor) {
                    bfs_queue.push_back(neighbor);
                }
            }
        }
    }
    let connectedness = connected_ids.len() as f64 / (included_ids.len().max(1)) as f64;

    let avg_depth: f64 = included_symbols.iter().map(|s| s.depth as f64).sum::<f64>()
        / (included_symbols.len().max(1)) as f64;
    let token_efficiency = tokens_used as f64 / (token_budget.max(1)) as f64;

    let included_count = included_symbols.len() as i64;
    let dedupe_denom = (included_count + duplicate_skips).max(1);
    let dedupe_ratio = included_count as f64 / dedupe_denom as f64;

    serde_json::json!({
        "seed_hit_rate": round4(seed_hit_rate),
        "connectedness": round4(connectedness),
        "token_efficiency": round4(token_efficiency),
        "avg_depth": round4(avg_depth),
        "included_count": included_count,
        "dedupe_ratio": round4(dedupe_ratio),
        "redaction_hits": redaction_hits,
    })
}

fn round4(v: f64) -> f64 {
    (v * 10000.0).round() / 10000.0
}

// ---------------------------------------------------------------------------
// Internal data structures
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct SymbolData {
    id: i64,
    name: String,
    kind: String,
    qualified_name: String,
    file_path: String,
    start_line: i64,
    end_line: i64,
    signature: String,
    is_seed: bool,
    depth: i64,
}

struct IncludedSymbol {
    id: i64,
    name: String,
    kind: String,
    qualified_name: String,
    file_path: String,
    start_line: i64,
    end_line: i64,
    depth: i64,
    included_as: String,
    source: String,
    selection_reason: String,
}

// ---------------------------------------------------------------------------
// Public implementation (pure Rust, no Python dependency)
// ---------------------------------------------------------------------------

pub fn get_context_impl(
    conn: &Connection,
    query: &str,
    entry_points: &[String],
    token_budget: i64,
    include_signatures_only: bool,
    expansion_depth: i64,
) -> BombeResult<serde_json::Value> {
    // 1. Normalize request
    let normalized_query = truncate_query(query);
    let clamped_entry_points: Vec<String> = entry_points
        .iter()
        .take(MAX_CONTEXT_SEEDS)
        .cloned()
        .collect();
    let clamped_budget = clamp_budget(
        token_budget,
        MIN_CONTEXT_TOKEN_BUDGET,
        MAX_CONTEXT_TOKEN_BUDGET,
    );
    let clamped_depth = clamp_depth(expansion_depth, MAX_CONTEXT_EXPANSION_DEPTH);

    // 2. Get dynamic node cap
    let total_symbols: i64 =
        conn.query_row("SELECT COUNT(*) AS count FROM symbols;", [], |row| {
            row.get(0)
        })?;
    let dynamic_node_cap = adaptive_graph_cap(total_symbols, MAX_GRAPH_VISITED, Some(128));

    // 3. Pick seeds
    let seeds = pick_seeds(conn, &normalized_query, &clamped_entry_points)?;

    // 4. If no seeds, return empty response
    if seeds.is_empty() {
        return Ok(serde_json::json!({
            "query": normalized_query,
            "context_bundle": {
                "summary": "No relevant symbols found.",
                "relationship_map": "",
                "files": [],
                "tokens_used": 0,
                "token_budget": clamped_budget,
                "symbols_included": 0,
                "symbols_available": 0,
            },
        }));
    }

    // 5. Expand from seeds via BFS
    let reached = expand(conn, &seeds, clamped_depth, dynamic_node_cap)?;
    let symbol_ids: Vec<i64> = reached.keys().copied().collect();

    // 6. Compute personalized PageRank
    let ppr_scores = personalized_pagerank(conn, &seeds, &symbol_ids, 0.85, 20)?;

    // 7. Load symbol rows
    let id_placeholders: String = symbol_ids
        .iter()
        .enumerate()
        .map(|(i, _)| format!("?{}", i + 1))
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "SELECT id, name, kind, qualified_name, file_path, start_line, end_line, \
         signature, pagerank_score \
         FROM symbols WHERE id IN ({id_placeholders});"
    );
    let mut stmt = conn.prepare(&sql)?;
    let id_params: Vec<Box<dyn rusqlite::types::ToSql>> = symbol_ids
        .iter()
        .map(|id| Box::new(*id) as Box<dyn rusqlite::types::ToSql>)
        .collect();
    let param_refs: Vec<&dyn rusqlite::types::ToSql> =
        id_params.iter().map(|p| p.as_ref()).collect();

    let symbol_rows: Vec<SymbolInfo> = stmt
        .query_map(param_refs.as_slice(), |row| {
            Ok(SymbolInfo {
                id: row.get(0)?,
                name: row.get(1)?,
                kind: row.get(2)?,
                qualified_name: row.get(3)?,
                file_path: row.get(4)?,
                start_line: row.get(5)?,
                end_line: row.get(6)?,
                signature: row.get::<_, Option<String>>(7)?.unwrap_or_default(),
                pagerank_score: row.get::<_, f64>(8).unwrap_or(0.0),
            })
        })?
        .filter_map(|r| r.ok())
        .collect();

    let terms = query_terms(&normalized_query);
    let seed_set: HashSet<i64> = seeds.iter().copied().collect();

    // 8. Compute ranking scores
    let mut ranked: Vec<(f64, SymbolData)> = Vec::new();
    for row in &symbol_rows {
        let depth = reached.get(&row.id).copied().unwrap_or(0);
        let ppr = ppr_scores.get(&row.id).copied().unwrap_or(0.0);
        let proximity_bonus = match depth {
            0 => 1.0,
            1 => 0.7,
            2 => 0.4,
            _ => 0.25,
        };
        let base_score = ppr * row.pagerank_score.max(1e-9) * proximity_bonus;
        let lexical_relevance =
            symbol_query_relevance(&row.name, &row.qualified_name, &row.signature, &terms);
        let lexical_boost = 1.0 + (0.08 * lexical_relevance as f64).min(0.25);
        let score = base_score * lexical_boost;

        ranked.push((
            score,
            SymbolData {
                id: row.id,
                name: row.name.clone(),
                kind: row.kind.clone(),
                qualified_name: row.qualified_name.clone(),
                file_path: row.file_path.clone(),
                start_line: row.start_line,
                end_line: row.end_line,
                signature: row.signature.clone(),
                is_seed: seed_set.contains(&row.id),
                depth,
            },
        ));
    }

    // Sort by score descending
    ranked.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

    // 9. Build adjacency and topology ordering
    let adjacency = build_adjacency(conn, &symbol_ids)?;
    let topo_order = topology_order(&ranked, &seeds, &adjacency);

    // Build lookup map: symbol_id -> SymbolData
    let ranked_symbols: HashMap<i64, SymbolData> = ranked
        .iter()
        .map(|(_, sym)| (sym.id, sym.clone()))
        .collect();

    // 10. Walk topology order, adding symbols within token budget
    let mut tokens_used: i64 = 0;
    let mut included_symbols: Vec<IncludedSymbol> = Vec::new();
    let mut seen_bundle_keys: HashSet<(String, String, String)> = HashSet::new();
    let mut duplicate_skips: i64 = 0;
    let mut total_redaction_hits: i64 = 0;

    for (symbol_id, topology_reason) in &topo_order {
        if included_symbols.len() as i64 >= dynamic_node_cap {
            break;
        }
        let symbol = match ranked_symbols.get(symbol_id) {
            Some(s) => s,
            None => continue,
        };

        let include_full = symbol.is_seed && !include_signatures_only;
        let mut source: String;
        let mut mode: String;

        if include_full {
            source = source_fragment(&symbol.file_path, symbol.start_line, symbol.end_line);
            mode = "full_source".to_string();
        } else {
            source = symbol.signature.clone();
            mode = "signature_only".to_string();
        }

        // Redact
        let (redacted_source, source_redaction_hits) = redact_sensitive_text(&source);
        source = redacted_source;
        total_redaction_hits += source_redaction_hits;

        // Dedup via bundle key
        let bundle_key = (
            symbol.qualified_name.clone(),
            symbol.file_path.clone(),
            source.clone(),
        );
        if seen_bundle_keys.contains(&bundle_key) {
            duplicate_skips += 1;
            continue;
        }

        let mut symbol_tokens = estimate_tokens(&source, None);

        if tokens_used + symbol_tokens > clamped_budget {
            if mode == "full_source" {
                // Fall back to signature
                source = symbol.signature.clone();
                mode = "signature_only".to_string();
                let fallback_bundle_key = (
                    symbol.qualified_name.clone(),
                    symbol.file_path.clone(),
                    source.clone(),
                );
                if seen_bundle_keys.contains(&fallback_bundle_key) {
                    duplicate_skips += 1;
                    continue;
                }
                symbol_tokens = estimate_tokens(&source, None);
                // Update bundle_key to the fallback
                let bundle_key = fallback_bundle_key;
                if tokens_used + symbol_tokens > clamped_budget {
                    continue;
                }
                seen_bundle_keys.insert(bundle_key);
            } else {
                continue;
            }
        } else {
            seen_bundle_keys.insert(bundle_key);
        }

        tokens_used += symbol_tokens;

        // Build selection reason
        let mut reason_parts: Vec<String> = vec![
            topology_reason.clone(),
            format!("depth={}", symbol.depth),
            format!("mode={mode}"),
        ];
        if symbol.is_seed {
            reason_parts.push("seed_match".to_string());
        }

        included_symbols.push(IncludedSymbol {
            id: symbol.id,
            name: symbol.name.clone(),
            kind: symbol.kind.clone(),
            qualified_name: symbol.qualified_name.clone(),
            file_path: symbol.file_path.clone(),
            start_line: symbol.start_line,
            end_line: symbol.end_line,
            depth: symbol.depth,
            included_as: mode,
            source,
            selection_reason: reason_parts.join(","),
        });
    }

    // 11. Group by file, build file entries
    let mut files: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (idx, sym) in included_symbols.iter().enumerate() {
        files.entry(sym.file_path.clone()).or_default().push(idx);
    }

    let mut file_entries: Vec<serde_json::Value> = Vec::new();
    for (path, indices) in &files {
        let mut symbols_in_file: Vec<&IncludedSymbol> =
            indices.iter().map(|&i| &included_symbols[i]).collect();
        symbols_in_file.sort_by_key(|s| s.start_line);

        let symbol_values: Vec<serde_json::Value> = symbols_in_file
            .iter()
            .map(|sym| {
                serde_json::json!({
                    "id": sym.id,
                    "name": sym.name,
                    "kind": sym.kind,
                    "lines": format!("{}-{}", sym.start_line, sym.end_line),
                    "included_as": sym.included_as,
                    "source": sym.source,
                    "file_path": sym.file_path,
                    "depth": sym.depth,
                    "qualified_name": sym.qualified_name,
                    "selection_reason": sym.selection_reason,
                })
            })
            .collect();

        file_entries.push(serde_json::json!({
            "path": path,
            "symbols": symbol_values,
        }));
    }
    // file_entries is already sorted by path (BTreeMap guarantees order)

    // 12. Build summary and relationship map
    let summary = format!(
        "Selected {} symbols from {} files.",
        included_symbols.len(),
        file_entries.len()
    );
    let relationship_map: String = included_symbols
        .iter()
        .take(8)
        .map(|s| s.name.as_str())
        .collect::<Vec<_>>()
        .join(" -> ");

    // 13. Compute quality metrics
    let qm = quality_metrics(
        &included_symbols,
        &seeds,
        clamped_budget,
        tokens_used,
        &adjacency,
        duplicate_skips,
        total_redaction_hits,
    );

    // 14. Build final payload
    let payload = serde_json::json!({
        "query": normalized_query,
        "context_bundle": {
            "summary": summary,
            "relationship_map": relationship_map,
            "selection_strategy": "seeded_topology_then_rank",
            "quality_metrics": qm,
            "files": file_entries,
            "tokens_used": tokens_used,
            "token_budget": clamped_budget,
            "symbols_included": included_symbols.len(),
            "symbols_available": ranked.len(),
        },
    });

    Ok(payload)
}

// ---------------------------------------------------------------------------
// PyO3 entry point
// ---------------------------------------------------------------------------

/// Context assembly query: seeded BFS expansion + personalized PageRank +
/// topology-aware ordering + token-budget pruning with secret redaction.
///
/// Returns the full context payload as a Python dict (via JSON round-trip).
#[pyfunction]
#[pyo3(signature = (db, query, entry_points=vec![], token_budget=8000, include_signatures_only=false, expansion_depth=2))]
#[allow(clippy::too_many_arguments)]
pub fn get_context(
    py: Python<'_>,
    db: &crate::store::database::Database,
    query: &str,
    entry_points: Vec<String>,
    token_budget: i64,
    include_signatures_only: bool,
    expansion_depth: i64,
) -> PyResult<PyObject> {
    let conn = db.connect_internal()?;
    let result = get_context_impl(
        &conn,
        query,
        &entry_points,
        token_budget,
        include_signatures_only,
        expansion_depth,
    )?;
    let json_str = serde_json::to_string(&result)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let json_module = py.import("json")?;
    json_module
        .call_method1("loads", (json_str,))
        .map(|o| o.into())
}
