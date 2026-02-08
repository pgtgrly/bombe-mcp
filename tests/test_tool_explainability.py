from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry


class ToolExplainabilityTests(unittest.TestCase):
    def _registry(self) -> dict[str, dict[str, object]]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        root = Path(tmpdir.name)
        source_file = root / "auth.py"
        source_file.write_text(
            (
                "def authenticate(user):\n"
                "    return user\n\n"
                "def login(user):\n"
                "    return authenticate(user)\n"
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
                (source_file.as_posix(),),
            )
            conn.execute(
                """
                INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                VALUES
                  ('authenticate', 'app.authenticate', 'function', ?, 1, 2, 'def authenticate(user)', 0.9),
                  ('login', 'app.login', 'function', ?, 4, 5, 'def login(user)', 0.7);
                """,
                (source_file.as_posix(), source_file.as_posix()),
            )
            rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
            ids = {str(row["qualified_name"]): int(row["id"]) for row in rows}
            conn.execute(
                """
                INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 4);
                """,
                (ids["app.login"], ids["app.authenticate"]),
            )
            conn.commit()
        return build_tool_registry(db, root.as_posix())

    def test_include_explanations_adds_reasoning_payload(self) -> None:
        registry = self._registry()
        search = registry["search_symbols"]["handler"](
            {"query": "auth", "include_explanations": True, "include_plan": True}
        )
        refs = registry["get_references"]["handler"](
            {
                "symbol_name": "app.authenticate",
                "direction": "callers",
                "depth": 1,
                "include_explanations": True,
                "include_plan": True,
            }
        )
        context = registry["get_context"]["handler"](
            {
                "query": "authenticate flow",
                "entry_points": ["app.authenticate"],
                "include_explanations": True,
                "include_plan": True,
            }
        )
        blast = registry["get_blast_radius"]["handler"](
            {
                "symbol_name": "app.authenticate",
                "max_depth": 2,
                "include_explanations": True,
                "include_plan": True,
            }
        )
        flow = registry["trace_data_flow"]["handler"](
            {
                "symbol_name": "app.authenticate",
                "direction": "both",
                "max_depth": 2,
                "include_explanations": True,
                "include_plan": True,
            }
        )
        impact = registry["change_impact"]["handler"](
            {
                "symbol_name": "app.authenticate",
                "max_depth": 2,
                "include_explanations": True,
                "include_plan": True,
            }
        )
        structure = registry["get_structure"]["handler"]({"path": ".", "include_explanations": True})

        self.assertIn("explanations", search)
        self.assertIn("planner_trace", search)
        self.assertIn("explanations", refs)
        self.assertIn("planner_trace", refs)
        self.assertIn("explanations", context)
        self.assertIn("planner_trace", context)
        self.assertIn("explanations", blast)
        self.assertIn("planner_trace", blast)
        self.assertIn("explanations", flow)
        self.assertIn("planner_trace", flow)
        self.assertIn("explanations", impact)
        self.assertIn("planner_trace", impact)
        self.assertTrue(str(structure).startswith("# structure_explanations"))


if __name__ == "__main__":
    unittest.main()
