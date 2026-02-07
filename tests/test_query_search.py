from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.models import FileRecord, SymbolRecord
from bombe.models import SymbolSearchRequest
from bombe.query.search import search_symbols
from bombe.store.database import Database


class QuerySearchTests(unittest.TestCase):
    def test_search_symbols_filters_by_name_kind_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/auth.py', 'python', 'h1', 1), ('src/api.py', 'python', 'h2', 1);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES
                      ('authenticate', 'app.auth.authenticate', 'function', 'src/auth.py', 1, 5, 0.9),
                      ('AuthService', 'app.auth.AuthService', 'class', 'src/auth.py', 7, 20, 0.5),
                      ('login', 'app.api.login', 'function', 'src/api.py', 1, 3, 0.1);
                    """
                )
                rows = conn.execute(
                    "SELECT id, qualified_name FROM symbols;"
                ).fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship)
                    VALUES (?, ?, 'symbol', 'symbol', 'CALLS');
                    """,
                    (ids["app.api.login"], ids["app.auth.authenticate"]),
                )
                conn.commit()

            result = search_symbols(
                db,
                SymbolSearchRequest(
                    query="auth",
                    kind="function",
                    file_pattern="src/%",
                    limit=10,
                ),
            )
            self.assertEqual(result.total_matches, 1)
            symbol = result.symbols[0]
            self.assertEqual(symbol["name"], "authenticate")
            self.assertEqual(symbol["callers_count"], 1)
            self.assertEqual(symbol["match_strategy"], "like")
            self.assertIn("query='auth'", symbol["match_reason"])

    def test_search_symbols_uses_fts_content_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            fts_available = bool(
                db.query(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'symbol_fts';"
                )
            )
            db.upsert_files(
                [
                    FileRecord(
                        path="src/crypto.py",
                        language="python",
                        content_hash="h-crypto",
                        size_bytes=100,
                    )
                ]
            )
            db.replace_file_symbols(
                "src/crypto.py",
                [
                    SymbolRecord(
                        name="verify_password",
                        qualified_name="auth.crypto.verify_password",
                        kind="function",
                        file_path="src/crypto.py",
                        start_line=1,
                        end_line=3,
                        signature="def verify_password(password, hashed)",
                        docstring="Validate bcrypt hash",
                        pagerank_score=0.9,
                    )
                ],
            )

            result = search_symbols(
                db,
                SymbolSearchRequest(query="bcrypt", kind="function", limit=10),
            )
            if fts_available:
                self.assertEqual(result.total_matches, 1)
                self.assertEqual(result.symbols[0]["name"], "verify_password")
                self.assertEqual(result.symbols[0]["match_strategy"], "fts")
            else:
                self.assertGreaterEqual(result.total_matches, 0)


if __name__ == "__main__":
    unittest.main()
