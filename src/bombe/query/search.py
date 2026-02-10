"""Symbol search query backend."""

from __future__ import annotations

from contextlib import closing

from bombe.query.guards import MAX_SEARCH_LIMIT, clamp_limit, truncate_query
from bombe.query.hybrid import rank_symbol
from bombe.models import SymbolSearchRequest, SymbolSearchResponse
from bombe.store.database import Database


def _count_refs(conn, symbol_id: int) -> tuple[int, int]:
    callers_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edges
        WHERE relationship = 'CALLS' AND target_type = 'symbol' AND target_id = ?;
        """,
        (symbol_id,),
    ).fetchone()["count"]
    callees_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edges
        WHERE relationship = 'CALLS' AND source_type = 'symbol' AND source_id = ?;
        """,
        (symbol_id,),
    ).fetchone()["count"]
    return int(callers_count), int(callees_count)


def _search_with_like(conn, req: SymbolSearchRequest):
    query_value = f"%{req.query.lower()}%"
    params: list[object] = [query_value]
    where_clauses = ["(LOWER(name) LIKE ? OR LOWER(qualified_name) LIKE ?)"]
    params.append(query_value)

    if req.kind != "any":
        where_clauses.append("kind = ?")
        params.append(req.kind)
    if req.file_pattern:
        where_clauses.append("file_path LIKE ?")
        params.append(req.file_pattern.replace("*", "%"))

    sql = f"""
        SELECT id, name, qualified_name, kind, file_path, start_line, end_line, signature, docstring,
               visibility, pagerank_score
        FROM symbols
        WHERE {' AND '.join(where_clauses)}
        ORDER BY pagerank_score DESC, name ASC
        LIMIT ?;
    """
    params.append(req.limit)
    return conn.execute(sql, tuple(params)).fetchall()


def _search_with_fts(conn, req: SymbolSearchRequest):
    query = req.query.strip()
    if not query:
        return []
    params: list[object] = [query]
    where_clauses = ["symbol_fts MATCH ?"]
    if req.kind != "any":
        where_clauses.append("s.kind = ?")
        params.append(req.kind)
    if req.file_pattern:
        where_clauses.append("s.file_path LIKE ?")
        params.append(req.file_pattern.replace("*", "%"))
    params.append(req.limit)

    sql = f"""
        SELECT s.id, s.name, s.qualified_name, s.kind, s.file_path, s.start_line, s.end_line,
               s.signature, s.docstring, s.visibility, s.pagerank_score, bm25(symbol_fts) AS rank
        FROM symbol_fts f
        JOIN symbols s ON s.id = f.symbol_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY rank ASC, s.pagerank_score DESC
        LIMIT ?;
    """
    return conn.execute(sql, tuple(params)).fetchall()


def search_symbols(db: Database, req: SymbolSearchRequest) -> SymbolSearchResponse:
    normalized_request = SymbolSearchRequest(
        query=truncate_query(req.query),
        kind=req.kind,
        file_pattern=req.file_pattern,
        limit=clamp_limit(req.limit, maximum=MAX_SEARCH_LIMIT),
    )
    with closing(db.connect()) as conn:
        search_mode = "like"
        try:
            fts_rows = _search_with_fts(
                conn,
                SymbolSearchRequest(
                    query=normalized_request.query,
                    kind=normalized_request.kind,
                    file_pattern=normalized_request.file_pattern,
                    limit=clamp_limit(normalized_request.limit * 3, maximum=MAX_SEARCH_LIMIT),
                ),
            )
        except Exception:
            fts_rows = []
        like_rows = _search_with_like(
            conn,
            SymbolSearchRequest(
                query=normalized_request.query,
                kind=normalized_request.kind,
                file_pattern=normalized_request.file_pattern,
                limit=clamp_limit(normalized_request.limit * 3, maximum=MAX_SEARCH_LIMIT),
            ),
        )
        combined: dict[int, object] = {}
        strategy_by_id: dict[int, str] = {}
        for row in like_rows:
            symbol_id = int(row["id"])
            combined[symbol_id] = row
            strategy_by_id[symbol_id] = "like"
        for row in fts_rows:
            symbol_id = int(row["id"])
            combined[symbol_id] = row
            strategy_by_id[symbol_id] = "fts"
        search_mode = "fts" if fts_rows else "like"
        rows = list(combined.values())

        scored_payload: list[tuple[float, dict[str, object]]] = []
        for row in rows:
            symbol_id = int(row["id"])
            callers_count, callees_count = _count_refs(conn, symbol_id)
            file_pattern = normalized_request.file_pattern or "*"
            strategy = strategy_by_id.get(symbol_id, search_mode)
            ranking_score = rank_symbol(
                query=normalized_request.query,
                name=str(row["name"]),
                qualified_name=str(row["qualified_name"]),
                signature=str(row["signature"]) if row["signature"] is not None else None,
                docstring=str(row["docstring"]) if row["docstring"] is not None else None,
                pagerank=float(row["pagerank_score"] or 0.0),
                callers=callers_count,
                callees=callees_count,
            )
            match_reason = (
                f"{search_mode}:query='{normalized_request.query}',kind='{normalized_request.kind}',file='{file_pattern}'"
            )
            scored_payload.append(
                (
                    float(ranking_score),
                    {
                        "name": row["name"],
                        "qualified_name": row["qualified_name"],
                        "kind": row["kind"],
                        "file_path": row["file_path"],
                        "start_line": row["start_line"],
                        "end_line": row["end_line"],
                        "signature": row["signature"],
                        "visibility": row["visibility"],
                        "importance_score": row["pagerank_score"],
                        "callers_count": callers_count,
                        "callees_count": callees_count,
                        "match_strategy": strategy,
                        "match_reason": match_reason,
                    },
                )
            )
        scored_payload.sort(
            key=lambda item: (
                -float(item[0]),
                str(item[1]["qualified_name"]),
                str(item[1]["file_path"]),
            )
        )
        payload = [item[1] for item in scored_payload[: normalized_request.limit]]

    return SymbolSearchResponse(symbols=payload, total_matches=len(payload))
