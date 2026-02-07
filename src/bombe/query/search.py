"""Symbol search query backend."""

from __future__ import annotations

from contextlib import closing

from bombe.models import SymbolSearchRequest, SymbolSearchResponse
from bombe.store.database import Database


def search_symbols(db: Database, req: SymbolSearchRequest) -> SymbolSearchResponse:
    query_value = f"%{req.query.lower()}%"
    params: list[object] = [query_value]
    where_clauses = ["LOWER(name) LIKE ?"]

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

    with closing(db.connect()) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        payload: list[dict[str, object]] = []
        for row in rows:
            symbol_id = int(row["id"])
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
                }
            )

    return SymbolSearchResponse(symbols=payload, total_matches=len(payload))
