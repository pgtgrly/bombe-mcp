"""Reference traversal backend for callers/callees queries."""

from __future__ import annotations

from collections import deque
from contextlib import closing
from pathlib import Path

from bombe.models import ReferenceRequest, ReferenceResponse
from bombe.query.guards import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_VISITED,
    MAX_REFERENCE_DEPTH,
    clamp_depth,
    truncate_query,
)
from bombe.store.database import Database


def _resolve_symbol_id(conn, symbol_name: str) -> int | None:
    exact = conn.execute(
        "SELECT id FROM symbols WHERE qualified_name = ? LIMIT 1;", (symbol_name,)
    ).fetchone()
    if exact:
        return int(exact["id"])
    by_name = conn.execute(
        "SELECT id FROM symbols WHERE name = ? ORDER BY pagerank_score DESC LIMIT 1;",
        (symbol_name,),
    ).fetchone()
    if by_name:
        return int(by_name["id"])
    return None


def _load_symbol(conn, symbol_id: int) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT id, name, file_path, signature, start_line, end_line, qualified_name
        FROM symbols
        WHERE id = ?;
        """,
        (symbol_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Symbol id not found: {symbol_id}")
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "file_path": row["file_path"],
        "signature": row["signature"],
        "start_line": int(row["start_line"]),
        "end_line": int(row["end_line"]),
        "qualified_name": row["qualified_name"],
    }


def _read_source(file_path: str, start_line: int, end_line: int) -> str:
    path = Path(file_path)
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    begin = max(start_line - 1, 0)
    end = min(end_line, len(lines))
    return "\n".join(lines[begin:end])


def _walk(
    conn,
    start_id: int,
    direction: str,
    depth: int,
    max_edges: int = MAX_GRAPH_EDGES,
    max_visited: int = MAX_GRAPH_VISITED,
) -> list[tuple[int, int, int, str]]:
    queue = deque([(start_id, 0)])
    visited = {start_id}
    edges: list[tuple[int, int, int, str]] = []

    while queue:
        if len(edges) >= max_edges or len(visited) >= max_visited:
            break
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue

        if direction == "callers":
            rows = conn.execute(
                """
                SELECT source_id AS next_id, line_number, 'CALLS' AS relationship
                FROM edges
                WHERE relationship = 'CALLS'
                  AND target_type = 'symbol'
                  AND target_id = ?;
                """,
                (current,),
            ).fetchall()
        elif direction == "callees":
            rows = conn.execute(
                """
                SELECT target_id AS next_id, line_number, 'CALLS' AS relationship
                FROM edges
                WHERE relationship = 'CALLS'
                  AND source_type = 'symbol'
                  AND source_id = ?;
                """,
                (current,),
            ).fetchall()
        elif direction == "implementors":
            rows = conn.execute(
                """
                SELECT source_id AS next_id, line_number, relationship
                FROM edges
                WHERE relationship = 'IMPLEMENTS'
                  AND target_type = 'symbol'
                  AND target_id = ?;
                """,
                (current,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT target_id AS next_id, line_number, relationship
                FROM edges
                WHERE relationship IN ('EXTENDS', 'IMPLEMENTS')
                  AND source_type = 'symbol'
                  AND source_id = ?;
                """,
                (current,),
            ).fetchall()

        for row in rows:
            if len(edges) >= max_edges:
                break
            next_id = int(row["next_id"])
            line_number = int(row["line_number"]) if row["line_number"] is not None else 0
            relationship = str(row["relationship"])
            next_depth = current_depth + 1
            edges.append((next_id, line_number, next_depth, relationship))
            if next_id not in visited and len(visited) < max_visited:
                visited.add(next_id)
                queue.append((next_id, next_depth))

    return edges


def get_references(db: Database, req: ReferenceRequest) -> ReferenceResponse:
    normalized_symbol = truncate_query(req.symbol_name)
    bounded_depth = clamp_depth(req.depth, maximum=MAX_REFERENCE_DEPTH)
    with closing(db.connect()) as conn:
        symbol_id = _resolve_symbol_id(conn, normalized_symbol)
        if symbol_id is None:
            raise ValueError(f"Symbol not found: {normalized_symbol}")

        target = _load_symbol(conn, symbol_id)
        payload: dict[str, object] = {
            "target_symbol": {
                "name": target["name"],
                "file_path": target["file_path"],
                "signature": target["signature"],
            },
            "callers": [],
            "callees": [],
            "implementors": [],
            "supers": [],
        }

        directions = []
        if req.direction in {"callers", "both"}:
            directions.append("callers")
        if req.direction in {"callees", "both"}:
            directions.append("callees")
        if req.direction == "implementors":
            directions.append("implementors")
        if req.direction == "supers":
            directions.append("supers")

        for direction in directions:
            entries = _walk(conn, symbol_id, direction, bounded_depth)
            results: list[dict[str, object]] = []
            for next_id, line_number, depth, relationship in entries:
                info = _load_symbol(conn, next_id)
                item: dict[str, object] = {
                    "name": info["name"],
                    "file_path": info["file_path"],
                    "line": line_number,
                    "depth": depth,
                    "reference_reason": f"{direction}:{relationship}:depth={depth}",
                }
                if req.include_source:
                    item["source"] = _read_source(
                        str(info["file_path"]),
                        int(info["start_line"]),
                        int(info["end_line"]),
                    )
                results.append(item)
            payload[direction] = results

    return ReferenceResponse(payload=payload)
