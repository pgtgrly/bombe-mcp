from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry, register_tools


class FakeServer:
    def __init__(self) -> None:
        self.registered: dict[str, dict[str, object]] = {}

    def register_tool(self, name: str, description: str, input_schema_or_handler, handler=None) -> None:
        if handler is None:
            self.registered[name] = {
                "description": description,
                "input_schema": None,
                "handler": input_schema_or_handler,
            }
            return
        self.registered[name] = {
            "description": description,
            "input_schema": input_schema_or_handler,
            "handler": handler,
        }


class MCPContractTests(unittest.TestCase):
    def test_register_and_call_all_tool_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_file = root / "auth.py"
            source_file.write_text(
                "def authenticate(user):\n    return user\n", encoding="utf-8"
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
                      ('login', 'app.login', 'function', ?, 1, 2, 'def login(user)', 0.6);
                    """,
                    (source_file.as_posix(), source_file.as_posix()),
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 1);
                    """,
                    (ids["app.login"], ids["app.authenticate"]),
                )
                conn.commit()

            fake_server = FakeServer()
            register_tools(fake_server, db, root.as_posix())
            self.assertEqual(set(fake_server.registered.keys()), {
                "search_symbols",
                "get_references",
                "get_context",
                "get_structure",
                "get_blast_radius",
                "trace_data_flow",
                "change_impact",
            })
            self.assertIsNotNone(fake_server.registered["search_symbols"]["input_schema"])

            registry = build_tool_registry(db, root.as_posix())
            search_payload = registry["search_symbols"]["handler"]({"query": "auth"})
            self.assertIn("symbols", search_payload)

            references_payload = registry["get_references"]["handler"](
                {"symbol_name": "authenticate", "direction": "callers", "depth": 1}
            )
            self.assertIn("callers", references_payload)

            context_payload = registry["get_context"]["handler"](
                {"query": "authenticate flow", "token_budget": 100}
            )
            self.assertIn("context_bundle", context_payload)

            structure_payload = registry["get_structure"]["handler"]({"path": ".", "token_budget": 1000})
            self.assertIsInstance(structure_payload, str)

            blast_payload = registry["get_blast_radius"]["handler"](
                {"symbol_name": "authenticate", "max_depth": 2}
            )
            self.assertIn("impact", blast_payload)

            data_flow_payload = registry["trace_data_flow"]["handler"](
                {"symbol_name": "authenticate", "direction": "both", "max_depth": 2}
            )
            self.assertIn("paths", data_flow_payload)

            change_impact_payload = registry["change_impact"]["handler"](
                {"symbol_name": "authenticate", "max_depth": 2}
            )
            self.assertIn("impact", change_impact_payload)


if __name__ == "__main__":
    unittest.main()
