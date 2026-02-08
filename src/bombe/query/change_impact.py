"""Change impact analysis backend with graph-aware dependents."""

from __future__ import annotations

from collections import deque
from contextlib import closing

from bombe.query.guards import MAX_GRAPH_EDGES, MAX_GRAPH_VISITED, MAX_IMPACT_DEPTH, clamp_depth, truncate_query
from bombe.store.database import Database


def _resolve_symbol(conn, symbol_name: str):
    return conn.execute(
        """
        SELECT id, name, qualified_name, file_path
        FROM symbols
        WHERE qualified_name = ? OR name = ?
        ORDER BY pagerank_score DESC
        LIMIT 1;
        """,
        (symbol_name, symbol_name),
    ).fetchone()


def _risk_level(direct: int, transitive: int, type_dependents: int) -> str:
    total = direct + transitive + type_dependents
    if total >= 12:
        return "high"
    if total >= 4:
        return "medium"
    return "low"


def change_impact(
    db: Database,
    symbol_name: str,
    change_type: str = "behavior",
    max_depth: int = 3,
) -> dict[str, object]:
    normalized_symbol = truncate_query(symbol_name)
    bounded_depth = clamp_depth(max_depth, maximum=MAX_IMPACT_DEPTH)
    with closing(db.connect()) as conn:
        target = _resolve_symbol(conn, normalized_symbol)
        if target is None:
            raise ValueError(f"Symbol not found: {normalized_symbol}")

        target_id = int(target["id"])
        queue = deque([(target_id, 0)])
        visited = {target_id}
        direct_callers: list[dict[str, object]] = []
        transitive_callers: list[dict[str, object]] = []

        while queue:
            if len(direct_callers) + len(transitive_callers) >= MAX_GRAPH_EDGES:
                break
            current, depth = queue.popleft()
            if depth >= bounded_depth:
                continue
            rows = conn.execute(
                """
                SELECT e.source_id, e.line_number, s.name, s.qualified_name, s.file_path
                FROM edges e
                JOIN symbols s ON s.id = e.source_id
                WHERE e.relationship = 'CALLS'
                  AND e.target_type = 'symbol'
                  AND e.target_id = ?;
                """,
                (current,),
            ).fetchall()
            for row in rows:
                if len(direct_callers) + len(transitive_callers) >= MAX_GRAPH_EDGES:
                    break
                source_id = int(row["source_id"])
                if source_id in visited:
                    continue
                if len(visited) >= MAX_GRAPH_VISITED:
                    break
                visited.add(source_id)
                next_depth = depth + 1
                item = {
                    "id": source_id,
                    "name": row["name"],
                    "qualified_name": row["qualified_name"],
                    "file_path": row["file_path"],
                    "line": int(row["line_number"]) if row["line_number"] is not None else 0,
                    "depth": next_depth,
                    "impact_reason": f"call_dependency:depth={next_depth}",
                }
                if next_depth == 1:
                    direct_callers.append(item)
                else:
                    transitive_callers.append(item)
                queue.append((source_id, next_depth))

        type_dependents_rows = conn.execute(
            """
            SELECT e.source_id, e.relationship, s.name, s.qualified_name, s.file_path
            FROM edges e
            JOIN symbols s ON s.id = e.source_id
            WHERE e.target_type = 'symbol'
              AND e.target_id = ?
              AND e.relationship IN ('EXTENDS', 'IMPLEMENTS');
            """,
            (target_id,),
        ).fetchall()
        type_dependents = [
            {
                "id": int(row["source_id"]),
                "name": row["name"],
                "qualified_name": row["qualified_name"],
                "file_path": row["file_path"],
                "impact_reason": f"type_dependency:{row['relationship']}",
            }
            for row in type_dependents_rows
        ]

        impacted_files = sorted(
            {
                target["file_path"],
                *[item["file_path"] for item in direct_callers],
                *[item["file_path"] for item in transitive_callers],
                *[item["file_path"] for item in type_dependents],
            }
        )
        risk = _risk_level(len(direct_callers), len(transitive_callers), len(type_dependents))
        summary = (
            f"Impact={risk}; direct={len(direct_callers)}, transitive={len(transitive_callers)}, "
            f"type_dependents={len(type_dependents)}, files={len(impacted_files)}"
        )
        return {
            "target": {
                "id": target_id,
                "name": target["name"],
                "qualified_name": target["qualified_name"],
                "file_path": target["file_path"],
            },
            "change_type": change_type,
            "max_depth": bounded_depth,
            "summary": summary,
            "impact": {
                "direct_callers": direct_callers,
                "transitive_callers": transitive_callers,
                "type_dependents": type_dependents,
                "affected_files": impacted_files,
                "total_affected_symbols": (
                    len(direct_callers) + len(transitive_callers) + len(type_dependents)
                ),
                "risk_level": risk,
            },
        }
