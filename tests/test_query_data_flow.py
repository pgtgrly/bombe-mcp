from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.query.data_flow import trace_data_flow
from bombe.store.database import Database


class QueryDataFlowTests(unittest.TestCase):
    def test_trace_data_flow_returns_upstream_and_downstream_paths(self) -> None:
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
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature)
                    VALUES
                      ('a', 'pkg.a', 'function', 'a.py', 1, 2, 'def a()'),
                      ('b', 'pkg.b', 'function', 'b.py', 1, 2, 'def b()'),
                      ('c', 'pkg.c', 'function', 'c.py', 1, 2, 'def c()');
                    """
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES
                      (?, ?, 'symbol', 'symbol', 'CALLS', 10),
                      (?, ?, 'symbol', 'symbol', 'CALLS', 20);
                    """,
                    (ids["pkg.a"], ids["pkg.b"], ids["pkg.b"], ids["pkg.c"]),
                )
                conn.commit()

            payload = trace_data_flow(db, symbol_name="pkg.b", direction="both", max_depth=2)
            self.assertIn("paths", payload)
            self.assertEqual(payload["target"]["name"], "b")
            path_pairs = {(item["from_id"], item["to_id"]) for item in payload["paths"]}
            self.assertIn((ids["pkg.a"], ids["pkg.b"]), path_pairs)
            self.assertIn((ids["pkg.b"], ids["pkg.c"]), path_pairs)


if __name__ == "__main__":
    unittest.main()
