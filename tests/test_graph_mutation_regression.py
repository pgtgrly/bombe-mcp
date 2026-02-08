from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry


class GraphMutationRegressionTests(unittest.TestCase):
    def test_tools_tolerate_dangling_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_file = root / "service.py"
            source_file.write_text(
                "def alpha():\n    return 1\n\ndef beta():\n    return alpha()\n",
                encoding="utf-8",
            )
            db = Database(root / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES (?, 'python', 'h1', 1);
                    """,
                    (source_file.as_posix(),),
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                    VALUES
                      ('alpha', 'svc.alpha', 'function', ?, 1, 2, 'def alpha()', 0.8),
                      ('beta', 'svc.beta', 'function', ?, 4, 5, 'def beta()', 0.6);
                    """,
                    (source_file.as_posix(), source_file.as_posix()),
                )
                ids = {
                    str(row["qualified_name"]): int(row["id"])
                    for row in conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                }
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 4);
                    """,
                    (ids["svc.beta"], ids["svc.alpha"]),
                )
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (9999, ?, 'symbol', 'symbol', 'CALLS', 9);
                    """,
                    (ids["svc.alpha"],),
                )
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, 9999, 'symbol', 'symbol', 'CALLS', 10);
                    """,
                    (ids["svc.beta"],),
                )
                conn.commit()

            registry = build_tool_registry(db, root.as_posix())
            references_payload = registry["get_references"]["handler"](
                {"symbol_name": "alpha", "direction": "both", "depth": 2}
            )
            self.assertIn("target_symbol", references_payload)
            blast_payload = registry["get_blast_radius"]["handler"](
                {"symbol_name": "alpha", "change_type": "behavior", "max_depth": 2}
            )
            self.assertIn("impact", blast_payload)
            flow_payload = registry["trace_data_flow"]["handler"](
                {"symbol_name": "alpha", "direction": "both", "max_depth": 2}
            )
            self.assertIn("paths", flow_payload)


if __name__ == "__main__":
    unittest.main()
