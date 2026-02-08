from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.tools.definitions import build_tool_registry


class ToolMetricsResilienceTests(unittest.TestCase):
    def test_tool_response_survives_metric_persistence_failure(self) -> None:
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

            def _failing_metric_write(*args, **kwargs) -> None:
                del args, kwargs
                raise RuntimeError("metric backend unavailable")

            db.record_tool_metric = _failing_metric_write  # type: ignore[assignment]
            payload = registry["search_symbols"]["handler"]({"query": "auth"})
            self.assertIn("symbols", payload)
            self.assertIn("total_matches", payload)


if __name__ == "__main__":
    unittest.main()
