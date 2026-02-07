"""Context assembly backend for task-oriented code retrieval."""

from __future__ import annotations

from collections import deque
from contextlib import closing
from pathlib import Path

from bombe.models import ContextRequest, ContextResponse
from bombe.store.database import Database


RELATIONSHIPS = ("CALLS", "IMPORTS_SYMBOL", "EXTENDS", "IMPLEMENTS", "HAS_METHOD")


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


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


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

    words = [word.strip().lower() for word in req.query.split() if word.strip()]
    if not words:
        return []
    clauses = " OR ".join(["LOWER(name) LIKE ?"] * len(words))
    params = tuple(f"%{word}%" for word in words)
    rows = conn.execute(
        f"""
        SELECT id
        FROM symbols
        WHERE {clauses}
        ORDER BY pagerank_score DESC
        LIMIT 5;
        """,
        params,
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _expand(conn, seeds: list[int], depth: int) -> dict[int, tuple[int, float]]:
    reached: dict[int, tuple[int, float]] = {seed: (0, 1.0) for seed in seeds}
    queue = deque((seed, 0) for seed in seeds)
    rel_placeholders = ", ".join("?" for _ in RELATIONSHIPS)

    while queue:
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
            score = 1.0 * (0.7 ** next_depth)
            previous = reached.get(neighbor)
            if previous is None or next_depth < previous[0]:
                reached[neighbor] = (next_depth, score)
                queue.append((neighbor, next_depth))
    return reached


def get_context(db: Database, req: ContextRequest) -> ContextResponse:
    with closing(db.connect()) as conn:
        seeds = _pick_seeds(conn, req)
        if not seeds:
            return ContextResponse(
                payload={
                    "query": req.query,
                    "context_bundle": {
                        "summary": "No relevant symbols found.",
                        "relationship_map": "",
                        "files": [],
                        "tokens_used": 0,
                        "token_budget": req.token_budget,
                        "symbols_included": 0,
                        "symbols_available": 0,
                    },
                }
            )

        reached = _expand(conn, seeds, req.expansion_depth)
        symbol_ids = tuple(reached.keys())
        placeholders = ", ".join("?" for _ in symbol_ids)
        symbol_rows = conn.execute(
            f"""
            SELECT id, name, kind, qualified_name, file_path, start_line, end_line, signature, pagerank_score
            FROM symbols
            WHERE id IN ({placeholders});
            """,
            symbol_ids,
        ).fetchall()

        ranked: list[tuple[float, dict[str, object]]] = []
        for row in symbol_rows:
            symbol_id = int(row["id"])
            depth, ppr = reached[symbol_id]
            proximity_bonus = {0: 1.0, 1: 0.7, 2: 0.4}.get(depth, 0.25)
            score = ppr * float(row["pagerank_score"] or 0.0) * proximity_bonus
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

        tokens_used = 0
        included_symbols: list[dict[str, object]] = []
        for _, symbol in ranked:
            include_full = bool(symbol["is_seed"]) and not req.include_signatures_only
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
            symbol_tokens = _approx_tokens(source)
            if tokens_used + symbol_tokens > req.token_budget:
                if mode == "full_source":
                    source = str(symbol["signature"])
                    mode = "signature_only"
                    symbol_tokens = _approx_tokens(source)
                if tokens_used + symbol_tokens > req.token_budget:
                    continue
            tokens_used += symbol_tokens
            included_symbols.append(
                {
                    "name": symbol["name"],
                    "kind": symbol["kind"],
                    "lines": f"{symbol['start_line']}-{symbol['end_line']}",
                    "included_as": mode,
                    "source": source,
                    "file_path": symbol["file_path"],
                    "depth": symbol["depth"],
                }
            )

        files: dict[str, list[dict[str, object]]] = {}
        for symbol in included_symbols:
            files.setdefault(str(symbol["file_path"]), []).append(symbol)
        file_entries = [{"path": path, "symbols": symbols} for path, symbols in files.items()]

        summary = f"Selected {len(included_symbols)} symbols from {len(file_entries)} files."
        relationship_map = " -> ".join(str(symbol["name"]) for symbol in included_symbols[:6])

        payload = {
            "query": req.query,
            "context_bundle": {
                "summary": summary,
                "relationship_map": relationship_map,
                "files": file_entries,
                "tokens_used": tokens_used,
                "token_budget": req.token_budget,
                "symbols_included": len(included_symbols),
                "symbols_available": len(ranked),
            },
        }
        return ContextResponse(payload=payload)
