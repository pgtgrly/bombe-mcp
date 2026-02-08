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
            self.assertEqual(set(search_payload.keys()), {"symbols", "total_matches"})
            if search_payload["symbols"]:
                self.assertEqual(
                    set(search_payload["symbols"][0].keys()),
                    {
                        "name",
                        "qualified_name",
                        "kind",
                        "file_path",
                        "start_line",
                        "end_line",
                        "signature",
                        "visibility",
                        "importance_score",
                        "callers_count",
                        "callees_count",
                        "match_strategy",
                        "match_reason",
                    },
                )

            references_payload = registry["get_references"]["handler"](
                {"symbol_name": "authenticate", "direction": "callers", "depth": 1}
            )
            self.assertEqual(
                set(references_payload.keys()),
                {"target_symbol", "callers", "callees", "implementors", "supers"},
            )
            if references_payload["callers"]:
                self.assertEqual(
                    set(references_payload["callers"][0].keys()),
                    {"name", "file_path", "line", "depth", "reference_reason"},
                )

            context_payload = registry["get_context"]["handler"](
                {"query": "authenticate flow", "token_budget": 100}
            )
            self.assertEqual(set(context_payload.keys()), {"query", "context_bundle"})
            self.assertEqual(
                set(context_payload["context_bundle"].keys()),
                {
                    "summary",
                    "relationship_map",
                    "selection_strategy",
                    "quality_metrics",
                    "files",
                    "tokens_used",
                    "token_budget",
                    "symbols_included",
                    "symbols_available",
                },
            )

            structure_payload = registry["get_structure"]["handler"]({"path": ".", "token_budget": 1000})
            self.assertIsInstance(structure_payload, str)

            blast_payload = registry["get_blast_radius"]["handler"](
                {"symbol_name": "authenticate", "max_depth": 2}
            )
            self.assertEqual(set(blast_payload.keys()), {"target", "change_type", "impact"})
            self.assertEqual(
                set(blast_payload["impact"].keys()),
                {
                    "direct_callers",
                    "transitive_callers",
                    "affected_files",
                    "total_affected_symbols",
                    "total_affected_files",
                    "risk_assessment",
                },
            )

            data_flow_payload = registry["trace_data_flow"]["handler"](
                {"symbol_name": "authenticate", "direction": "both", "max_depth": 2}
            )
            self.assertEqual(
                set(data_flow_payload.keys()),
                {"target", "direction", "max_depth", "summary", "nodes", "paths"},
            )
            if data_flow_payload["paths"]:
                self.assertEqual(
                    set(data_flow_payload["paths"][0].keys()),
                    {"from_id", "from_name", "to_id", "to_name", "line", "depth", "relationship"},
                )

            change_impact_payload = registry["change_impact"]["handler"](
                {"symbol_name": "authenticate", "max_depth": 2}
            )
            self.assertEqual(
                set(change_impact_payload.keys()),
                {"target", "change_type", "max_depth", "summary", "impact"},
            )
            self.assertEqual(
                set(change_impact_payload["impact"].keys()),
                {
                    "direct_callers",
                    "transitive_callers",
                    "type_dependents",
                    "affected_files",
                    "total_affected_symbols",
                    "risk_level",
                },
            )

            metric_rows = db.query(
                """
                SELECT tool_name, success
                FROM tool_metrics
                ORDER BY id;
                """
            )
            self.assertGreaterEqual(len(metric_rows), 7)
            self.assertTrue(all(int(row["success"]) == 1 for row in metric_rows))


if __name__ == "__main__":
    unittest.main()
