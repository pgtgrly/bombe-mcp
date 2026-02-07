from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.indexer.pagerank import recompute_pagerank
from bombe.store.database import Database


class PageRankTests(unittest.TestCase):
    def test_recompute_pagerank_scores_connected_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('a.py', 'python', 'h1', 1), ('b.py', 'python', 'h2', 1), ('c.py', 'python', 'h3', 1);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line)
                    VALUES
                      ('a', 'pkg.a', 'function', 'a.py', 1, 2),
                      ('b', 'pkg.b', 'function', 'b.py', 1, 2),
                      ('c', 'pkg.c', 'function', 'c.py', 1, 2);
                    """
                )
                rows = conn.execute(
                    "SELECT id, qualified_name FROM symbols ORDER BY id;"
                ).fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS'),
                           (?, ?, 'symbol', 'symbol', 'CALLS');
                    """,
                    (ids["pkg.a"], ids["pkg.b"], ids["pkg.c"], ids["pkg.b"]),
                )
                conn.commit()

            recompute_pagerank(db)
            ranked = db.query(
                "SELECT qualified_name, pagerank_score FROM symbols ORDER BY pagerank_score DESC;"
            )
            self.assertEqual(ranked[0]["qualified_name"], "pkg.b")
            self.assertGreater(ranked[0]["pagerank_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
