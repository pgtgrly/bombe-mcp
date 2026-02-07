"""PageRank computation over symbol graph edges."""

from __future__ import annotations

from contextlib import closing

from bombe.store.database import Database


PAGERANK_RELATIONSHIPS = ("CALLS", "IMPORTS_SYMBOL", "EXTENDS", "IMPLEMENTS")


def recompute_pagerank(db: Database, damping: float = 0.85, epsilon: float = 1e-6) -> None:
    with closing(db.connect()) as conn:
        symbol_rows = conn.execute("SELECT id FROM symbols ORDER BY id;").fetchall()
        symbol_ids = [int(row["id"]) for row in symbol_rows]
        if not symbol_ids:
            return

        adjacency: dict[int, list[int]] = {symbol_id: [] for symbol_id in symbol_ids}
        placeholders = ", ".join("?" for _ in PAGERANK_RELATIONSHIPS)
        edge_rows = conn.execute(
            f"""
            SELECT source_id, target_id
            FROM edges
            WHERE source_type = 'symbol'
              AND target_type = 'symbol'
              AND relationship IN ({placeholders});
            """,
            PAGERANK_RELATIONSHIPS,
        ).fetchall()

        for row in edge_rows:
            source = int(row["source_id"])
            target = int(row["target_id"])
            if source in adjacency and target in adjacency:
                adjacency[source].append(target)

        node_count = len(symbol_ids)
        base_score = 1.0 / node_count
        scores = {symbol_id: base_score for symbol_id in symbol_ids}

        delta = 1.0
        while delta > epsilon:
            next_scores = {
                symbol_id: (1.0 - damping) / node_count for symbol_id in symbol_ids
            }
            dangling = [sid for sid, targets in adjacency.items() if not targets]
            dangling_mass = sum(scores[sid] for sid in dangling)
            dangling_contrib = damping * dangling_mass / node_count
            for symbol_id in symbol_ids:
                next_scores[symbol_id] += dangling_contrib

            for source, targets in adjacency.items():
                if not targets:
                    continue
                share = damping * scores[source] / len(targets)
                for target in targets:
                    next_scores[target] += share

            delta = sum(abs(next_scores[sid] - scores[sid]) for sid in symbol_ids)
            scores = next_scores

        conn.executemany(
            "UPDATE symbols SET pagerank_score = ? WHERE id = ?;",
            [(scores[symbol_id], symbol_id) for symbol_id in symbol_ids],
        )
        conn.commit()
