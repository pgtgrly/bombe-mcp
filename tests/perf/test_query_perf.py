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


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[max(0, min(index, len(ordered) - 1))]


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

            search_latencies: list[float] = []
            refs_latencies: list[float] = []
            context_latencies: list[float] = []
            for _ in range(20):
                start = time.perf_counter()
                search_symbols(db, SymbolSearchRequest(query="func", limit=20))
                search_latencies.append((time.perf_counter() - start) * 1000)

                start = time.perf_counter()
                get_references(db, ReferenceRequest(symbol_name="func_1", direction="both", depth=1))
                refs_latencies.append((time.perf_counter() - start) * 1000)

                start = time.perf_counter()
                get_context(db, ContextRequest(query="func_1 flow", token_budget=500))
                context_latencies.append((time.perf_counter() - start) * 1000)

            search_p50 = _percentile(search_latencies, 0.50)
            search_p95 = _percentile(search_latencies, 0.95)
            refs_p50 = _percentile(refs_latencies, 0.50)
            refs_p95 = _percentile(refs_latencies, 0.95)
            ctx_p50 = _percentile(context_latencies, 0.50)
            ctx_p95 = _percentile(context_latencies, 0.95)
            print(
                "[perf][query] "
                f"search_ms_p50={search_p50:.2f} search_ms_p95={search_p95:.2f} "
                f"references_ms_p50={refs_p50:.2f} references_ms_p95={refs_p95:.2f} "
                f"context_ms_p50={ctx_p50:.2f} context_ms_p95={ctx_p95:.2f}"
            )

            self.assertLess(search_p95, 20.0)
            self.assertLess(refs_p95, 120.0)
            self.assertLess(ctx_p95, 700.0)


if __name__ == "__main__":
    unittest.main()
