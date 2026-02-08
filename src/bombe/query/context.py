"""Context assembly backend for task-oriented code retrieval."""

from __future__ import annotations

import re
from collections import deque
from contextlib import closing
from pathlib import Path

from bombe.models import ContextRequest, ContextResponse
from bombe.query.guards import (
    MAX_CONTEXT_EXPANSION_DEPTH,
    MAX_CONTEXT_SEEDS,
    MAX_CONTEXT_TOKEN_BUDGET,
    MAX_GRAPH_VISITED,
    MIN_CONTEXT_TOKEN_BUDGET,
    clamp_budget,
    clamp_depth,
    truncate_query,
)
from bombe.store.database import Database
from bombe.query.tokenizer import estimate_tokens


RELATIONSHIPS = ("CALLS", "IMPORTS_SYMBOL", "EXTENDS", "IMPLEMENTS", "HAS_METHOD")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def _resolve_path(file_path: str) -> Path:
    path = Path(file_path)
    return path if path.is_absolute() else Path.cwd() / path


def _source_fragment(file_path: str, start_line: int, end_line: int) -> str:
    path = _resolve_path(file_path)
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    start_idx = max(start_line - 1, 0)
    end_idx = min(end_line, len(lines))
    return "\n".join(lines[start_idx:end_idx])


def _query_terms(query: str) -> set[str]:
    terms = {match.group(0).lower() for match in WORD_RE.finditer(query)}
    return {term for term in terms if len(term) >= 2}


def _symbol_query_relevance(
    name: str,
    qualified_name: str,
    signature: str,
    query_terms: set[str],
) -> int:
    if not query_terms:
        return 0
    haystacks = [name.lower(), qualified_name.lower(), signature.lower()]
    score = 0
    for term in query_terms:
        for haystack in haystacks:
            if term in haystack:
                score += 1
                break
    return score


def _pick_seeds(conn, req: ContextRequest) -> list[int]:
    if req.entry_points:
        seeds: list[int] = []
        for entry in req.entry_points:
            row = conn.execute(
                """
                SELECT id
                FROM symbols
                WHERE qualified_name = ? OR name = ?
                ORDER BY pagerank_score DESC
                LIMIT 1;
                """,
                (entry, entry),
            ).fetchone()
            if row:
                seeds.append(int(row["id"]))
        if seeds:
            return seeds

    query_text = req.query.strip()
    try:
        rows = conn.execute(
            """
            SELECT s.id
            FROM symbol_fts
            JOIN symbols s ON s.id = symbol_fts.symbol_id
            WHERE symbol_fts MATCH ?
            ORDER BY bm25(symbol_fts), s.pagerank_score DESC
            LIMIT 8;
            """,
            (query_text,),
        ).fetchall()
        if rows:
            return [int(row["id"]) for row in rows]
    except Exception:
        pass

    words = [word.strip().lower() for word in query_text.split() if word.strip()]
    if not words:
        return []
    clauses = " OR ".join(["LOWER(name) LIKE ?" for _ in words] + ["LOWER(qualified_name) LIKE ?" for _ in words])
    params = tuple([f"%{word}%" for word in words] + [f"%{word}%" for word in words])
    rows = conn.execute(
        f"""
        SELECT id
        FROM symbols
        WHERE {clauses}
        ORDER BY pagerank_score DESC
        LIMIT 8;
        """,
        params,
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _expand(conn, seeds: list[int], depth: int, max_nodes: int = MAX_GRAPH_VISITED) -> dict[int, int]:
    reached: dict[int, int] = {seed: 0 for seed in seeds}
    queue = deque((seed, 0) for seed in seeds)
    rel_placeholders = ", ".join("?" for _ in RELATIONSHIPS)

    while queue:
        if len(reached) >= max_nodes:
            break
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        rows = conn.execute(
            f"""
            SELECT source_id, target_id
            FROM edges
            WHERE source_type = 'symbol'
              AND target_type = 'symbol'
              AND relationship IN ({rel_placeholders})
              AND (source_id = ? OR target_id = ?);
            """,
            RELATIONSHIPS + (current, current),
        ).fetchall()
        for row in rows:
            neighbor = int(row["target_id"]) if int(row["source_id"]) == current else int(row["source_id"])
            next_depth = current_depth + 1
            previous = reached.get(neighbor)
            if previous is None or next_depth < previous:
                reached[neighbor] = next_depth
                if len(reached) < max_nodes:
                    queue.append((neighbor, next_depth))
    return reached


def _personalized_pagerank(
    conn,
    seeds: list[int],
    nodes: list[int],
    damping: float = 0.85,
    iterations: int = 20,
) -> dict[int, float]:
    if not nodes:
        return {}
    rel_placeholders = ", ".join("?" for _ in RELATIONSHIPS)
    node_set = set(nodes)
    adjacency: dict[int, list[int]] = {node: [] for node in nodes}
    rows = conn.execute(
        f"""
        SELECT source_id, target_id
        FROM edges
        WHERE source_type = 'symbol'
          AND target_type = 'symbol'
          AND relationship IN ({rel_placeholders});
        """,
        RELATIONSHIPS,
    ).fetchall()
    for row in rows:
        source = int(row["source_id"])
        target = int(row["target_id"])
        if source in node_set and target in node_set:
            adjacency[source].append(target)
            adjacency[target].append(source)

    seed_set = set(seeds)
    restart = {
        node: (1.0 / len(seed_set)) if node in seed_set and seed_set else 0.0
        for node in nodes
    }
    scores = {node: restart[node] for node in nodes}

    for _ in range(iterations):
        next_scores = {node: (1.0 - damping) * restart[node] for node in nodes}
        for source, targets in adjacency.items():
            if not targets:
                continue
            share = damping * scores[source] / len(targets)
            for target in targets:
                next_scores[target] += share
        scores = next_scores
    return scores


def _adjacency(conn, nodes: list[int]) -> dict[int, set[int]]:
    if not nodes:
        return {}
    rel_placeholders = ", ".join("?" for _ in RELATIONSHIPS)
    node_set = set(nodes)
    adjacency: dict[int, set[int]] = {node: set() for node in nodes}
    rows = conn.execute(
        f"""
        SELECT source_id, target_id
        FROM edges
        WHERE source_type = 'symbol'
          AND target_type = 'symbol'
          AND relationship IN ({rel_placeholders});
        """,
        RELATIONSHIPS,
    ).fetchall()
    for row in rows:
        source = int(row["source_id"])
        target = int(row["target_id"])
        if source in node_set and target in node_set:
            adjacency[source].add(target)
            adjacency[target].add(source)
    return adjacency


def _topology_order(
    ranked: list[tuple[float, dict[str, object]]],
    seeds: list[int],
    adjacency: dict[int, set[int]],
) -> list[tuple[int, str]]:
    score_map = {int(symbol["id"]): score for score, symbol in ranked}
    seed_ids = sorted(set(seeds), key=lambda symbol_id: score_map.get(symbol_id, 0.0), reverse=True)
    queue = deque((seed_id, "seed") for seed_id in seed_ids)
    ordered: list[tuple[int, str]] = []
    seen: set[int] = set()

    while queue:
        current, reason = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        ordered.append((current, reason))
        neighbors = sorted(
            adjacency.get(current, set()),
            key=lambda symbol_id: score_map.get(symbol_id, 0.0),
            reverse=True,
        )
        for neighbor in neighbors:
            if neighbor not in seen:
                queue.append((neighbor, "graph_neighbor"))

    remaining = [
        (int(symbol["id"]), "rank_fallback")
        for _, symbol in ranked
        if int(symbol["id"]) not in seen
    ]
    return ordered + remaining


def _quality_metrics(
    included_symbols: list[dict[str, object]],
    seeds: list[int],
    token_budget: int,
    tokens_used: int,
    adjacency: dict[int, set[int]],
    duplicate_skips: int = 0,
) -> dict[str, object]:
    if not included_symbols:
        return {
            "seed_hit_rate": 0.0,
            "connectedness": 0.0,
            "token_efficiency": 0.0,
            "avg_depth": 0.0,
            "included_count": 0,
            "dedupe_ratio": 1.0,
        }

    included_ids = {int(symbol["id"]) for symbol in included_symbols}
    seed_set = set(seeds)
    included_seed_ids = sorted(included_ids & seed_set)
    seed_hit_rate = len(included_seed_ids) / max(1, len(seed_set))

    connected_ids: set[int] = set()
    queue = deque(included_seed_ids)
    while queue:
        current = queue.popleft()
        if current in connected_ids:
            continue
        connected_ids.add(current)
        for neighbor in adjacency.get(current, set()):
            if neighbor in included_ids and neighbor not in connected_ids:
                queue.append(neighbor)
    connectedness = len(connected_ids) / max(1, len(included_ids))

    avg_depth = sum(int(symbol["depth"]) for symbol in included_symbols) / max(1, len(included_symbols))
    token_efficiency = tokens_used / max(1, token_budget)

    return {
        "seed_hit_rate": round(seed_hit_rate, 4),
        "connectedness": round(connectedness, 4),
        "token_efficiency": round(token_efficiency, 4),
        "avg_depth": round(avg_depth, 4),
        "included_count": len(included_symbols),
        "dedupe_ratio": round(
            len(included_symbols) / max(1, len(included_symbols) + duplicate_skips),
            4,
        ),
    }


def get_context(db: Database, req: ContextRequest) -> ContextResponse:
    normalized_request = ContextRequest(
        query=truncate_query(req.query),
        entry_points=req.entry_points[:MAX_CONTEXT_SEEDS],
        token_budget=clamp_budget(
            req.token_budget,
            minimum=MIN_CONTEXT_TOKEN_BUDGET,
            maximum=MAX_CONTEXT_TOKEN_BUDGET,
        ),
        include_signatures_only=req.include_signatures_only,
        expansion_depth=clamp_depth(req.expansion_depth, maximum=MAX_CONTEXT_EXPANSION_DEPTH),
    )
    with closing(db.connect()) as conn:
        seeds = _pick_seeds(conn, normalized_request)
        if not seeds:
            return ContextResponse(
                payload={
                    "query": normalized_request.query,
                    "context_bundle": {
                        "summary": "No relevant symbols found.",
                        "relationship_map": "",
                        "files": [],
                        "tokens_used": 0,
                        "token_budget": normalized_request.token_budget,
                        "symbols_included": 0,
                        "symbols_available": 0,
                    },
                }
            )

        reached = _expand(
            conn,
            seeds,
            normalized_request.expansion_depth,
            max_nodes=MAX_GRAPH_VISITED,
        )
        symbol_ids = tuple(reached.keys())
        placeholders = ", ".join("?" for _ in symbol_ids)
        ppr_scores = _personalized_pagerank(conn, seeds, list(symbol_ids))
        symbol_rows = conn.execute(
            f"""
            SELECT id, name, kind, qualified_name, file_path, start_line, end_line, signature, pagerank_score
            FROM symbols
            WHERE id IN ({placeholders});
            """,
            symbol_ids,
        ).fetchall()
        query_terms = _query_terms(normalized_request.query)

        ranked: list[tuple[float, dict[str, object]]] = []
        for row in symbol_rows:
            symbol_id = int(row["id"])
            depth = reached[symbol_id]
            ppr = ppr_scores.get(symbol_id, 0.0)
            proximity_bonus = {0: 1.0, 1: 0.7, 2: 0.4}.get(depth, 0.25)
            base_score = ppr * max(float(row["pagerank_score"] or 0.0), 1e-9) * proximity_bonus
            lexical_relevance = _symbol_query_relevance(
                name=str(row["name"]),
                qualified_name=str(row["qualified_name"]),
                signature=str(row["signature"] or ""),
                query_terms=query_terms,
            )
            lexical_boost = 1.0 + min(0.25, 0.08 * lexical_relevance)
            score = base_score * lexical_boost
            ranked.append(
                (
                    score,
                    {
                        "id": symbol_id,
                        "name": row["name"],
                        "kind": row["kind"],
                        "qualified_name": row["qualified_name"],
                        "file_path": row["file_path"],
                        "start_line": int(row["start_line"]),
                        "end_line": int(row["end_line"]),
                        "signature": row["signature"] or "",
                        "is_seed": symbol_id in seeds,
                        "depth": depth,
                    },
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        adjacency = _adjacency(conn, list(symbol_ids))
        topology_order = _topology_order(ranked, seeds, adjacency)
        ranked_symbols = {int(symbol["id"]): symbol for _, symbol in ranked}

        tokens_used = 0
        included_symbols: list[dict[str, object]] = []
        seen_bundle_keys: set[tuple[str, str, str]] = set()
        duplicate_skips = 0
        for symbol_id, topology_reason in topology_order:
            if len(included_symbols) >= MAX_GRAPH_VISITED:
                break
            symbol = ranked_symbols[symbol_id]
            include_full = bool(symbol["is_seed"]) and not normalized_request.include_signatures_only
            source = ""
            mode = "signature_only"
            if include_full:
                source = _source_fragment(
                    str(symbol["file_path"]),
                    int(symbol["start_line"]),
                    int(symbol["end_line"]),
                )
                mode = "full_source"
            if not include_full:
                source = str(symbol["signature"])
            bundle_key = (
                str(symbol["qualified_name"]),
                str(symbol["file_path"]),
                source,
            )
            if bundle_key in seen_bundle_keys:
                duplicate_skips += 1
                continue
            symbol_tokens = estimate_tokens(source)
            if tokens_used + symbol_tokens > normalized_request.token_budget:
                if mode == "full_source":
                    source = str(symbol["signature"])
                    mode = "signature_only"
                    bundle_key = (
                        str(symbol["qualified_name"]),
                        str(symbol["file_path"]),
                        source,
                    )
                    if bundle_key in seen_bundle_keys:
                        duplicate_skips += 1
                        continue
                    symbol_tokens = estimate_tokens(source)
                if tokens_used + symbol_tokens > normalized_request.token_budget:
                    continue
            tokens_used += symbol_tokens
            seen_bundle_keys.add(bundle_key)
            reason_parts = [topology_reason, f"depth={symbol['depth']}", f"mode={mode}"]
            if symbol["is_seed"]:
                reason_parts.append("seed_match")
            included_symbols.append(
                {
                    "id": symbol["id"],
                    "name": symbol["name"],
                    "kind": symbol["kind"],
                    "lines": f"{symbol['start_line']}-{symbol['end_line']}",
                    "included_as": mode,
                    "source": source,
                    "file_path": symbol["file_path"],
                    "depth": symbol["depth"],
                    "qualified_name": symbol["qualified_name"],
                    "selection_reason": ",".join(reason_parts),
                }
            )

        files: dict[str, list[dict[str, object]]] = {}
        for symbol in included_symbols:
            files.setdefault(str(symbol["file_path"]), []).append(symbol)
        file_entries = []
        for path, symbols in files.items():
            symbols.sort(key=lambda item: int(str(item["lines"]).split("-")[0]))
            file_entries.append({"path": path, "symbols": symbols})
        file_entries.sort(key=lambda item: item["path"])

        summary = f"Selected {len(included_symbols)} symbols from {len(file_entries)} files."
        relationship_map = " -> ".join(str(symbol["name"]) for symbol in included_symbols[:8])
        quality_metrics = _quality_metrics(
            included_symbols=included_symbols,
            seeds=seeds,
            token_budget=normalized_request.token_budget,
            tokens_used=tokens_used,
            adjacency=adjacency,
            duplicate_skips=duplicate_skips,
        )

        payload = {
            "query": normalized_request.query,
            "context_bundle": {
                "summary": summary,
                "relationship_map": relationship_map,
                "selection_strategy": "seeded_topology_then_rank",
                "quality_metrics": quality_metrics,
                "files": file_entries,
                "tokens_used": tokens_used,
                "token_budget": normalized_request.token_budget,
                "symbols_included": len(included_symbols),
                "symbols_available": len(ranked),
            },
        }
        return ContextResponse(payload=payload)
