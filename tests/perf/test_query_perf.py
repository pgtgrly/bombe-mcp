from __future__ import annotations

import os
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from bombe.models import ContextRequest, ReferenceRequest, SymbolSearchRequest
from bombe.query.context import get_context
from bombe.query.references import get_references
from bombe.query.search import search_symbols
from bombe.store.database import Database


@unittest.skipUnless(os.getenv("BOMBE_RUN_PERF") == "1", "Perf tests are opt-in.")
class QueryPerformanceTests(unittest.TestCase):
    def test_query_latency_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    "INSERT INTO files(path, language, content_hash, size_bytes) VALUES ('a.py', 'python', 'h1', 1);"
                )
                for i in range(200):
                    conn.execute(
                        """
                        INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                        VALUES (?, ?, 'function', 'a.py', 1, 2, ?, ?);
                        """,
                        (f"func_{i}", f"pkg.func_{i}", f"def func_{i}()", 1.0 / (i + 1)),
                    )
                conn.commit()

            start = time.perf_counter()
            search_symbols(db, SymbolSearchRequest(query="func", limit=20))
            search_ms = (time.perf_counter() - start) * 1000

            start = time.perf_counter()
            get_references(db, ReferenceRequest(symbol_name="func_1", direction="both", depth=1))
            refs_ms = (time.perf_counter() - start) * 1000

            start = time.perf_counter()
            get_context(db, ContextRequest(query="func_1 flow", token_budget=500))
            ctx_ms = (time.perf_counter() - start) * 1000

            self.assertLess(search_ms, 20.0)
            self.assertLess(refs_ms, 120.0)
            self.assertLess(ctx_ms, 700.0)


if __name__ == "__main__":
    unittest.main()
