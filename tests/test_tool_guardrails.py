from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry


class ToolGuardrailTests(unittest.TestCase):
    def _build_registry(self) -> tuple[Database, dict[str, dict[str, object]], Path]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name)
        source = root / "svc.py"
        source.write_text(
            (
                "def a():\n    return b()\n\n"
                "def b():\n    return c()\n\n"
                "def c():\n    return d()\n\n"
                "def d():\n    return 1\n"
            ),
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
                (source.as_posix(),),
            )
            conn.execute(
                """
                INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                VALUES
                  ('a', 'svc.a', 'function', ?, 1, 2, 'def a()', 1.0),
                  ('b', 'svc.b', 'function', ?, 4, 5, 'def b()', 0.9),
                  ('c', 'svc.c', 'function', ?, 7, 8, 'def c()', 0.8),
                  ('d', 'svc.d', 'function', ?, 10, 11, 'def d()', 0.7);
                """,
                (source.as_posix(), source.as_posix(), source.as_posix(), source.as_posix()),
            )
            rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
            ids = {str(row["qualified_name"]): int(row["id"]) for row in rows}
            conn.execute(
                """
                INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                VALUES
                  (?, ?, 'symbol', 'symbol', 'CALLS', 1),
                  (?, ?, 'symbol', 'symbol', 'CALLS', 4),
                  (?, ?, 'symbol', 'symbol', 'CALLS', 7);
                """,
                (ids["svc.a"], ids["svc.b"], ids["svc.b"], ids["svc.c"], ids["svc.c"], ids["svc.d"]),
            )
            conn.commit()
        return db, build_tool_registry(db, root.as_posix()), root

    def test_depth_and_budget_inputs_are_clamped(self) -> None:
        db, registry, _ = self._build_registry()
        _ = db
        refs = registry["get_references"]["handler"](
            {"symbol_name": "svc.a", "direction": "callees", "depth": 999}
        )
        depths = [int(item["depth"]) for item in refs["callees"]]
        self.assertTrue(all(depth <= 6 for depth in depths))

        flow = registry["trace_data_flow"]["handler"](
            {"symbol_name": "svc.b", "direction": "both", "max_depth": 999}
        )
        self.assertEqual(int(flow["max_depth"]), 6)

        impact = registry["change_impact"]["handler"](
            {"symbol_name": "svc.c", "change_type": "behavior", "max_depth": 999}
        )
        self.assertEqual(int(impact["max_depth"]), 6)

        context_hi = registry["get_context"]["handler"](
            {"query": "svc.a", "entry_points": ["svc.a"], "token_budget": 999999}
        )
        self.assertEqual(int(context_hi["context_bundle"]["token_budget"]), 32000)

        context_lo = registry["get_context"]["handler"](
            {"query": "svc.a", "entry_points": ["svc.a"], "token_budget": 1}
        )
        self.assertEqual(int(context_lo["context_bundle"]["token_budget"]), 1)

    def test_search_limit_and_query_length_are_clamped(self) -> None:
        db, registry, _ = self._build_registry()
        _ = db
        payload = registry["search_symbols"]["handler"](
            {"query": "a" * 2000, "kind": "any", "limit": 9999}
        )
        self.assertLessEqual(int(payload["total_matches"]), 100)
        if payload["symbols"]:
            reason = str(payload["symbols"][0]["match_reason"])
            self.assertLessEqual(len(reason), 700)


if __name__ == "__main__":
    unittest.main()
