"""Data flow tracing backend for callgraph traversal."""

from __future__ import annotations

from collections import deque
from contextlib import closing

from bombe.query.guards import MAX_FLOW_DEPTH, MAX_GRAPH_EDGES, MAX_GRAPH_VISITED, clamp_depth, truncate_query
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


def trace_data_flow(
    db: Database,
    symbol_name: str,
    direction: str = "both",
    max_depth: int = 3,
) -> dict[str, object]:
    normalized_symbol = truncate_query(symbol_name)
    bounded_depth = clamp_depth(max_depth, maximum=MAX_FLOW_DEPTH)
    with closing(db.connect()) as conn:
        target = _resolve_symbol(conn, normalized_symbol)
        if target is None:
            raise ValueError(f"Symbol not found: {normalized_symbol}")

        target_id = int(target["id"])
        queue = deque([(target_id, 0, "target")])
        seen = {(target_id, "target")}
        paths: list[dict[str, object]] = []
        nodes: dict[int, dict[str, object]] = {
            target_id: {
                "id": target_id,
                "name": target["name"],
                "qualified_name": target["qualified_name"],
                "file_path": target["file_path"],
                "role": "target",
            }
        }

        while queue:
            if len(paths) >= MAX_GRAPH_EDGES or len(nodes) >= MAX_GRAPH_VISITED:
                break
            current_id, depth, _role = queue.popleft()
            if depth >= bounded_depth:
                continue
            current_name = str(nodes.get(current_id, {}).get("name", ""))

            if direction in {"upstream", "both"}:
                upstream_rows = conn.execute(
                    """
                    SELECT e.source_id AS neighbor_id, e.line_number, s.name, s.qualified_name, s.file_path
                    FROM edges e
                    JOIN symbols s ON s.id = e.source_id
                    WHERE e.relationship = 'CALLS'
                      AND e.target_type = 'symbol'
                      AND e.target_id = ?;
                    """,
                    (current_id,),
                ).fetchall()
                for row in upstream_rows:
                    if len(paths) >= MAX_GRAPH_EDGES or len(nodes) >= MAX_GRAPH_VISITED:
                        break
                    neighbor_id = int(row["neighbor_id"])
                    nodes.setdefault(
                        neighbor_id,
                        {
                            "id": neighbor_id,
                            "name": row["name"],
                            "qualified_name": row["qualified_name"],
                            "file_path": row["file_path"],
                            "role": "upstream",
                        },
                    )
                    paths.append(
                        {
                            "from_id": neighbor_id,
                            "from_name": row["name"],
                            "to_id": current_id,
                            "to_name": current_name,
                            "line": int(row["line_number"]) if row["line_number"] is not None else 0,
                            "depth": depth + 1,
                            "relationship": "CALLS",
                        }
                    )
                    queue_key = (neighbor_id, "upstream")
                    if queue_key not in seen:
                        seen.add(queue_key)
                        queue.append((neighbor_id, depth + 1, "upstream"))

            if direction in {"downstream", "both"}:
                downstream_rows = conn.execute(
                    """
                    SELECT e.target_id AS neighbor_id, e.line_number, s.name, s.qualified_name, s.file_path
                    FROM edges e
                    JOIN symbols s ON s.id = e.target_id
                    WHERE e.relationship = 'CALLS'
                      AND e.source_type = 'symbol'
                      AND e.source_id = ?;
                    """,
                    (current_id,),
                ).fetchall()
                for row in downstream_rows:
                    if len(paths) >= MAX_GRAPH_EDGES or len(nodes) >= MAX_GRAPH_VISITED:
                        break
                    neighbor_id = int(row["neighbor_id"])
                    nodes.setdefault(
                        neighbor_id,
                        {
                            "id": neighbor_id,
                            "name": row["name"],
                            "qualified_name": row["qualified_name"],
                            "file_path": row["file_path"],
                            "role": "downstream",
                        },
                    )
                    paths.append(
                        {
                            "from_id": current_id,
                            "from_name": current_name,
                            "to_id": neighbor_id,
                            "to_name": row["name"],
                            "line": int(row["line_number"]) if row["line_number"] is not None else 0,
                            "depth": depth + 1,
                            "relationship": "CALLS",
                        }
                    )
                    queue_key = (neighbor_id, "downstream")
                    if queue_key not in seen:
                        seen.add(queue_key)
                        queue.append((neighbor_id, depth + 1, "downstream"))

        sorted_paths = sorted(
            paths,
            key=lambda item: (int(item["depth"]), int(item["line"]), int(item.get("from_id", 0))),
        )
        node_list = sorted(nodes.values(), key=lambda item: (str(item["file_path"]), str(item["name"])))
        summary = (
            f"Traced {len(sorted_paths)} call edges across {len(node_list)} symbols "
            f"(direction={direction}, depth<={bounded_depth})."
        )
        return {
            "target": {
                "id": target_id,
                "name": target["name"],
                "qualified_name": target["qualified_name"],
                "file_path": target["file_path"],
            },
            "direction": direction,
            "max_depth": bounded_depth,
            "summary": summary,
            "nodes": node_list,
            "paths": sorted_paths,
        }
