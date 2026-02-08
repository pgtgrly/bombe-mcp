from __future__ import annotations

import random
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry


class QueryFuzzTests(unittest.TestCase):
    def test_tool_handlers_accept_randomized_valid_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_file = root / "service.py"
            source_file.write_text(
                "def alpha(user):\n    return user\n\n"
                "def beta(user):\n    return alpha(user)\n",
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
                      ('alpha', 'svc.alpha', 'function', ?, 1, 2, 'def alpha(user)', 0.9),
                      ('beta', 'svc.beta', 'function', ?, 4, 5, 'def beta(user)', 0.7);
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
                    (ids["svc.beta"], ids["svc.alpha"]),
                )
                conn.commit()

            registry = build_tool_registry(db, root.as_posix())
            randomizer = random.Random(42)
            symbol_choices = ["alpha", "beta", "svc.alpha", "svc.beta"]

            for _ in range(80):
                query_text = randomizer.choice(["auth", "alpha", "beta", "flow", "impact"])
                symbol_name = randomizer.choice(symbol_choices)

                search_payload = registry["search_symbols"]["handler"](
                    {
                        "query": query_text,
                        "limit": randomizer.randint(1, 25),
                        "offset": randomizer.randint(0, 2),
                    }
                )
                self.assertIn("symbols", search_payload)

                references_payload = registry["get_references"]["handler"](
                    {
                        "symbol_name": symbol_name,
                        "direction": randomizer.choice(["callers", "callees", "both"]),
                        "depth": randomizer.randint(1, 3),
                    }
                )
                self.assertIn("target_symbol", references_payload)

                context_payload = registry["get_context"]["handler"](
                    {
                        "query": query_text,
                        "token_budget": randomizer.randint(80, 400),
                        "expansion_depth": randomizer.randint(1, 3),
                    }
                )
                self.assertIn("context_bundle", context_payload)

                blast_payload = registry["get_blast_radius"]["handler"](
                    {
                        "symbol_name": symbol_name,
                        "change_type": randomizer.choice(["signature", "behavior", "delete"]),
                        "max_depth": randomizer.randint(1, 3),
                    }
                )
                self.assertIn("impact", blast_payload)

                flow_payload = registry["trace_data_flow"]["handler"](
                    {
                        "symbol_name": symbol_name,
                        "direction": randomizer.choice(["upstream", "downstream", "both"]),
                        "max_depth": randomizer.randint(1, 4),
                    }
                )
                self.assertIn("paths", flow_payload)

                impact_payload = registry["change_impact"]["handler"](
                    {
                        "symbol_name": symbol_name,
                        "change_type": randomizer.choice(["signature", "behavior", "delete"]),
                        "max_depth": randomizer.randint(1, 4),
                    }
                )
                self.assertIn("impact", impact_payload)


if __name__ == "__main__":
    unittest.main()
