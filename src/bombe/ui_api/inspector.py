"""Inspector payload assembly for graph and diagnostics visualization."""

from __future__ import annotations

from typing import Any

from bombe.store.database import Database


def build_inspector_bundle(
    db: Database,
    *,
    node_limit: int = 300,
    edge_limit: int = 500,
    diagnostics_limit: int = 50,
) -> dict[str, Any]:
    nodes = db.query(
        """
        SELECT id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score
        FROM symbols
        ORDER BY pagerank_score DESC, id ASC
        LIMIT ?;
        """,
        (max(1, node_limit),),
    )
    node_ids = [int(row["id"]) for row in nodes]
    if node_ids:
        placeholders = ", ".join("?" for _ in node_ids)
        edges = db.query(
            f"""
            SELECT
                e.source_id,
                e.target_id,
                e.relationship,
                e.line_number,
                e.confidence
            FROM edges e
            WHERE e.source_id IN ({placeholders}) AND e.target_id IN ({placeholders})
            ORDER BY e.id ASC
            LIMIT ?;
            """,
            tuple([*node_ids, *node_ids, max(1, edge_limit)]),
        )
    else:
        edges = []

    diagnostics_summary = db.summarize_indexing_diagnostics()
    diagnostics = db.list_indexing_diagnostics(limit=max(1, diagnostics_limit))
    hot_paths = db.query(
        """
        SELECT s.name, s.qualified_name, s.file_path, s.pagerank_score
        FROM symbols s
        ORDER BY s.pagerank_score DESC, s.id ASC
        LIMIT 20;
        """
    )
    explainer_index = build_explainer_index(db, node_ids=node_ids, limit=min(node_limit, 50))
    return {
        "nodes": nodes,
        "edges": edges,
        "diagnostics_summary": diagnostics_summary,
        "diagnostics": diagnostics,
        "hot_paths": hot_paths,
        "explainer": explainer_index,
        "limits": {
            "node_limit": node_limit,
            "edge_limit": edge_limit,
            "diagnostics_limit": diagnostics_limit,
            "nodes_total": _count_table(db, "symbols"),
            "edges_total": _count_table(db, "edges"),
        },
    }


def _count_table(db: Database, table: str) -> int:
    """Return total row count for a table (safe against injection via allowlist)."""
    allowed = {"symbols", "edges", "files"}
    if table not in allowed:
        return 0
    rows = db.query(f"SELECT COUNT(*) AS cnt FROM {table};")
    return int(rows[0]["cnt"]) if rows else 0


def build_symbol_explanation(
    db: Database,
    symbol_id: int,
) -> dict[str, Any]:
    """Explain why a symbol ranks where it does.

    Returns inbound/outbound edge counts, callers, callees,
    PageRank score, rank position, and a human-readable summary.
    """
    symbol_rows = db.query(
        """
        SELECT id, name, qualified_name, kind, file_path,
               start_line, end_line, pagerank_score
        FROM symbols WHERE id = ?;
        """,
        (symbol_id,),
    )
    if not symbol_rows:
        return {"error": f"Symbol {symbol_id} not found."}

    symbol = symbol_rows[0]
    score = float(symbol.get("pagerank_score", 0) or 0)

    # Rank position
    rank_rows = db.query(
        """
        SELECT COUNT(*) AS higher
        FROM symbols
        WHERE pagerank_score > ?;
        """,
        (score,),
    )
    rank_position = int(rank_rows[0]["higher"]) + 1 if rank_rows else 0

    total_symbols_rows = db.query("SELECT COUNT(*) AS cnt FROM symbols;")
    total_symbols = int(total_symbols_rows[0]["cnt"]) if total_symbols_rows else 0

    # Inbound edges (who references this symbol)
    inbound = db.query(
        """
        SELECT s.name, s.qualified_name, s.kind, s.file_path, e.relationship, e.confidence
        FROM edges e
        JOIN symbols s ON s.id = e.source_id
        WHERE e.target_id = ?
        ORDER BY e.confidence DESC, s.pagerank_score DESC
        LIMIT 20;
        """,
        (symbol_id,),
    )

    # Outbound edges (what this symbol references)
    outbound = db.query(
        """
        SELECT s.name, s.qualified_name, s.kind, s.file_path, e.relationship, e.confidence
        FROM edges e
        JOIN symbols s ON s.id = e.target_id
        WHERE e.source_id = ?
        ORDER BY e.confidence DESC, s.pagerank_score DESC
        LIMIT 20;
        """,
        (symbol_id,),
    )

    # Build human-readable reason
    reasons: list[str] = []
    if len(inbound) == 0:
        reasons.append("No inbound references (potential orphan).")
    elif len(inbound) >= 10:
        reasons.append(f"Heavily referenced ({len(inbound)}+ callers), boosting PageRank.")
    else:
        reasons.append(f"Referenced by {len(inbound)} symbol(s).")

    if len(outbound) == 0:
        reasons.append("Leaf node — calls nothing.")
    else:
        reasons.append(f"Depends on {len(outbound)} symbol(s).")

    if rank_position <= 5:
        reasons.append(f"Top-{rank_position} most important symbol in the graph.")
    elif rank_position <= 20:
        reasons.append(f"Ranked #{rank_position} out of {total_symbols} symbols.")

    return {
        "symbol": symbol,
        "rank_position": rank_position,
        "total_symbols": total_symbols,
        "pagerank_score": score,
        "inbound_count": len(inbound),
        "outbound_count": len(outbound),
        "inbound": inbound,
        "outbound": outbound,
        "reasons": reasons,
    }


def build_explainer_index(
    db: Database,
    *,
    node_ids: list[int] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Build explanations for top-N symbols by PageRank.

    Returns a dict keyed by symbol ID string, each value being
    a compact explanation payload.
    """
    if node_ids is None:
        rows = db.query(
            """
            SELECT id FROM symbols
            ORDER BY pagerank_score DESC, id ASC
            LIMIT ?;
            """,
            (max(1, limit),),
        )
        node_ids = [int(r["id"]) for r in rows]

    target_ids = node_ids[:limit]
    index: dict[str, Any] = {}
    for sid in target_ids:
        explanation = build_symbol_explanation(db, sid)
        if "error" not in explanation:
            index[str(sid)] = {
                "rank": explanation["rank_position"],
                "score": explanation["pagerank_score"],
                "inbound": explanation["inbound_count"],
                "outbound": explanation["outbound_count"],
                "reasons": explanation["reasons"],
            }
    return index
