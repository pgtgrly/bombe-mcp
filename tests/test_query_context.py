from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.models import ContextRequest
from bombe.query.context import get_context
from bombe.store.database import Database


class QueryContextTests(unittest.TestCase):
    def test_get_context_respects_token_budget_and_includes_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            auth_file = root / "auth.py"
            helper_file = root / "helper.py"
            auth_file.write_text(
                "def authenticate(user, password):\n    return user == password\n",
                encoding="utf-8",
            )
            helper_file.write_text(
                "def hash_password(password):\n    return password\n",
                encoding="utf-8",
            )

            db = Database(root / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES (?, 'python', 'h1', 10), (?, 'python', 'h2', 10);
                    """,
                    (auth_file.as_posix(), helper_file.as_posix()),
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                    VALUES
                      ('authenticate', 'app.auth.authenticate', 'function', ?, 1, 2, 'def authenticate(user, password)', 0.9),
                      ('hash_password', 'app.auth.hash_password', 'function', ?, 1, 2, 'def hash_password(password)', 0.7);
                    """,
                    (auth_file.as_posix(), helper_file.as_posix()),
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 1);
                    """,
                    (ids["app.auth.authenticate"], ids["app.auth.hash_password"]),
                )
                conn.commit()

            response = get_context(
                db,
                ContextRequest(query="authenticate flow", token_budget=20, expansion_depth=2),
            )
            bundle = response.payload["context_bundle"]
            self.assertLessEqual(bundle["tokens_used"], bundle["token_budget"])
            self.assertGreaterEqual(bundle["symbols_included"], 1)
            self.assertEqual(bundle["selection_strategy"], "seeded_topology_then_rank")
            metrics = bundle["quality_metrics"]
            self.assertGreaterEqual(metrics["seed_hit_rate"], 0.0)
            self.assertLessEqual(metrics["seed_hit_rate"], 1.0)
            self.assertGreaterEqual(metrics["connectedness"], 0.0)
            self.assertLessEqual(metrics["connectedness"], 1.0)
            self.assertGreaterEqual(metrics["token_efficiency"], 0.0)
            self.assertLessEqual(metrics["token_efficiency"], 1.0)
            files = bundle["files"]
            symbol_names = [
                symbol["name"]
                for entry in files
                for symbol in entry["symbols"]
            ]
            self.assertIn("authenticate", symbol_names)
            first_symbol = files[0]["symbols"][0]
            self.assertIn("selection_reason", first_symbol)

    def test_get_context_uses_entry_points_as_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            file_path = root / "service.py"
            file_path.write_text(
                "def alpha():\n    return 1\n\ndef beta():\n    return alpha()\n",
                encoding="utf-8",
            )
            db = Database(root / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    "INSERT INTO files(path, language, content_hash, size_bytes) VALUES (?, 'python', 'h1', 10);",
                    (file_path.as_posix(),),
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                    VALUES
                      ('alpha', 'svc.alpha', 'function', ?, 1, 2, 'def alpha()', 0.4),
                      ('beta', 'svc.beta', 'function', ?, 4, 5, 'def beta()', 0.9);
                    """,
                    (file_path.as_posix(), file_path.as_posix()),
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 4);
                    """,
                    (ids["svc.beta"], ids["svc.alpha"]),
                )
                conn.commit()

            response = get_context(
                db,
                ContextRequest(
                    query="something unrelated",
                    entry_points=["svc.alpha"],
                    token_budget=100,
                    expansion_depth=1,
                ),
            )
            bundle = response.payload["context_bundle"]
            names = [symbol["name"] for file in bundle["files"] for symbol in file["symbols"]]
            self.assertIn("alpha", names)

    def test_get_context_prefers_connected_symbols_under_tight_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            file_path = root / "graph.py"
            file_path.write_text(
                "def alpha():\n    return beta()\n\ndef beta():\n    return gamma()\n\ndef gamma():\n    return 2\n",
                encoding="utf-8",
            )
            db = Database(root / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    "INSERT INTO files(path, language, content_hash, size_bytes) VALUES (?, 'python', 'h1', 10);",
                    (file_path.as_posix(),),
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                    VALUES
                      ('alpha', 'svc.alpha', 'function', ?, 1, 2, 'def alpha()', 0.4),
                      ('beta', 'svc.beta', 'function', ?, 4, 5, 'def beta()', 0.1),
                      ('gamma', 'svc.gamma', 'function', ?, 7, 8, 'def gamma()', 9.9);
                    """,
                    (file_path.as_posix(), file_path.as_posix(), file_path.as_posix()),
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 1);
                    """,
                    (ids["svc.alpha"], ids["svc.beta"]),
                )
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS', 4);
                    """,
                    (ids["svc.beta"], ids["svc.gamma"]),
                )
                conn.commit()

            response = get_context(
                db,
                ContextRequest(
                    query="graph flow",
                    entry_points=["svc.alpha"],
                    token_budget=5,
                    expansion_depth=2,
                ),
            )
            names = [
                symbol["name"]
                for file in response.payload["context_bundle"]["files"]
                for symbol in file["symbols"]
            ]
            self.assertIn("alpha", names)
            self.assertIn("beta", names)
            self.assertNotIn("gamma", names)
            metrics = response.payload["context_bundle"]["quality_metrics"]
            self.assertGreater(metrics["connectedness"], 0.0)


if __name__ == "__main__":
    unittest.main()
