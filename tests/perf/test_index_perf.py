from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.store.database import Database


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[max(0, min(index, len(ordered) - 1))]


@unittest.skipUnless(os.getenv("BOMBE_RUN_PERF") == "1", "Perf tests are opt-in.")
class IndexPerformanceTests(unittest.TestCase):
    def test_full_index_medium_fixture_under_target(self) -> None:
        schema_init_ms: list[float] = []
        index_ms: list[float] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            for i in range(200):
                (root / "src" / f"module_{i}.py").write_text(
                    "def a():\n    return 1\n\ndef b():\n    return a()\n",
                    encoding="utf-8",
                )
            db = Database(root / ".bombe" / "bombe.db")
            started = time.perf_counter()
            db.init_schema()
            schema_init_ms.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            full_index(root, db)
            elapsed = time.perf_counter() - started
            index_ms.append(elapsed * 1000)
            print(
                f"[perf][index] schema_init_ms_p50={_percentile(schema_init_ms, 0.50):.2f} "
                f"schema_init_ms_p95={_percentile(schema_init_ms, 0.95):.2f} "
                f"full_index_ms_p50={_percentile(index_ms, 0.50):.2f} "
                f"full_index_ms_p95={_percentile(index_ms, 0.95):.2f}"
            )
            self.assertLess(elapsed, 30.0)


if __name__ == "__main__":
    unittest.main()
