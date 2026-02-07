"""Symbol search query backend."""

from __future__ import annotations

from contextlib import closing

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
        SELECT id, name, qualified_name, kind, file_path, start_line, end_line, signature,
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
               s.signature, s.visibility, s.pagerank_score, bm25(symbol_fts) AS rank
        FROM symbol_fts f
        JOIN symbols s ON s.id = f.symbol_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY rank ASC, s.pagerank_score DESC
        LIMIT ?;
    """
    return conn.execute(sql, tuple(params)).fetchall()


def search_symbols(db: Database, req: SymbolSearchRequest) -> SymbolSearchResponse:
    with closing(db.connect()) as conn:
        search_mode = "like"
        try:
            rows = _search_with_fts(conn, req)
            if rows:
                search_mode = "fts"
        except Exception:
            rows = []
        if not rows:
            rows = _search_with_like(conn, req)
            search_mode = "like"

        payload: list[dict[str, object]] = []
        for row in rows:
            symbol_id = int(row["id"])
            callers_count, callees_count = _count_refs(conn, symbol_id)
            file_pattern = req.file_pattern or "*"
            match_reason = (
                f"{search_mode}:query='{req.query}',kind='{req.kind}',file='{file_pattern}'"
            )
            payload.append(
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
                    "match_strategy": search_mode,
                    "match_reason": match_reason,
                }
            )

    return SymbolSearchResponse(symbols=payload, total_matches=len(payload))
