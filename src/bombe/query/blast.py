"""Blast radius impact analysis backend."""

from __future__ import annotations

from collections import deque
from contextlib import closing

from bombe.models import BlastRadiusRequest, BlastRadiusResponse
from bombe.store.database import Database


def _resolve_symbol(conn, symbol_name: str):
    row = conn.execute(
        """
        SELECT id, name, file_path
        FROM symbols
        WHERE qualified_name = ? OR name = ?
        ORDER BY pagerank_score DESC
        LIMIT 1;
        """,
        (symbol_name, symbol_name),
    ).fetchone()
    return row


def _risk_level(direct: int, transitive: int) -> str:
    total = direct + transitive
    if total >= 10:
        return "high"
    if total >= 3:
        return "medium"
    return "low"


def get_blast_radius(db: Database, req: BlastRadiusRequest) -> BlastRadiusResponse:
    with closing(db.connect()) as conn:
        target_row = _resolve_symbol(conn, req.symbol_name)
        if target_row is None:
            raise ValueError(f"Symbol not found: {req.symbol_name}")

        target_id = int(target_row["id"])
        queue = deque([(target_id, 0)])
        visited = {target_id}
        direct_callers: list[dict[str, object]] = []
        transitive_callers: list[dict[str, object]] = []

        while queue:
            current, depth = queue.popleft()
            if depth >= req.max_depth:
                continue
            rows = conn.execute(
                """
                SELECT e.source_id, e.line_number, s.name, s.file_path
                FROM edges e
                JOIN symbols s ON s.id = e.source_id
                WHERE e.relationship = 'CALLS'
                  AND e.target_type = 'symbol'
                  AND e.target_id = ?;
                """,
                (current,),
            ).fetchall()
            for row in rows:
                source_id = int(row["source_id"])
                if source_id in visited:
                    continue
                visited.add(source_id)
                next_depth = depth + 1
                item = {
                    "name": row["name"],
                    "file": row["file_path"],
                    "line": int(row["line_number"]) if row["line_number"] is not None else 0,
                }
                if next_depth == 1:
                    direct_callers.append(item)
                else:
                    transitive_callers.append({**item, "depth": next_depth})
                queue.append((source_id, next_depth))

        affected_files = sorted(
            {
                target_row["file_path"],
                *[item["file"] for item in direct_callers],
                *[item["file"] for item in transitive_callers],
            }
        )
        risk = _risk_level(len(direct_callers), len(transitive_callers))
        summary = (
            f"{risk} - {len(direct_callers)} direct callers, "
            f"{len(transitive_callers)} transitive dependents"
        )

        payload = {
            "target": {
                "name": target_row["name"],
                "file_path": target_row["file_path"],
            },
            "change_type": req.change_type,
            "impact": {
                "direct_callers": direct_callers,
                "transitive_callers": transitive_callers,
                "affected_files": affected_files,
                "total_affected_symbols": len(direct_callers) + len(transitive_callers),
                "total_affected_files": len(affected_files),
                "risk_assessment": summary,
            },
        }

    return BlastRadiusResponse(payload=payload)
