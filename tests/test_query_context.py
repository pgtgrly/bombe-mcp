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
            files = bundle["files"]
            symbol_names = [
                symbol["name"]
                for entry in files
                for symbol in entry["symbols"]
            ]
            self.assertIn("authenticate", symbol_names)


if __name__ == "__main__":
    unittest.main()
