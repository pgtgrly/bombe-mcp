from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry


class ToolCacheModeTests(unittest.TestCase):
    def test_repeated_query_records_cache_hit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_file = root / "auth.py"
            source_file.write_text(
                "def authenticate(user):\n    return user\n",
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
                    VALUES ('authenticate', 'app.authenticate', 'function', ?, 1, 2, 'def authenticate(user)', 0.9);
                    """,
                    (source_file.as_posix(),),
                )
                conn.commit()

            registry = build_tool_registry(db, root.as_posix())
            search_handler = registry["search_symbols"]["handler"]
            first_payload = search_handler({"query": "auth", "limit": 5})
            second_payload = search_handler({"query": "auth", "limit": 5})
            self.assertEqual(first_payload, second_payload)

            rows = db.query(
                """
                SELECT mode
                FROM tool_metrics
                WHERE tool_name = 'search_symbols'
                ORDER BY id ASC;
                """
            )
            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(str(rows[0]["mode"]), "cache_miss")
            self.assertEqual(str(rows[1]["mode"]), "cache_hit")


if __name__ == "__main__":
    unittest.main()
