from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index, incremental_index
from bombe.models import FileChange
from bombe.store.database import Database

from tests.perf.perf_utils import percentile, record_metrics


@unittest.skipUnless(os.getenv("BOMBE_RUN_PERF") == "1", "Perf tests are opt-in.")
class IncrementalPerformanceTests(unittest.TestCase):
    def test_single_file_incremental_under_target(self) -> None:
        baseline_index_ms: list[float] = []
        incremental_ms: list[float] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            for i in range(50):
                (root / "src" / f"file_{i}.py").write_text(
                    "def run():\n    return 1\n",
                    encoding="utf-8",
                )
            db = Database(root / ".bombe" / "bombe.db")
            db.init_schema()
            start = time.perf_counter()
            full_index(root, db)
            baseline_index_ms.append((time.perf_counter() - start) * 1000)
            changed = root / "src" / "file_0.py"
            changed.write_text("def run():\n    return 2\n", encoding="utf-8")
            started = time.perf_counter()
            incremental_index(root, db, [FileChange(status="M", path="src/file_0.py")])
            elapsed_ms = (time.perf_counter() - started) * 1000
            incremental_ms.append(elapsed_ms)
            metrics = {
                "baseline_full_index_ms_p50": percentile(baseline_index_ms, 0.50),
                "baseline_full_index_ms_p95": percentile(baseline_index_ms, 0.95),
                "incremental_ms_p50": percentile(incremental_ms, 0.50),
                "incremental_ms_p95": percentile(incremental_ms, 0.95),
            }
            history_path = record_metrics("incremental", metrics)
            print(
                f"[perf][incremental] baseline_full_index_ms_p50={metrics['baseline_full_index_ms_p50']:.2f} "
                f"baseline_full_index_ms_p95={metrics['baseline_full_index_ms_p95']:.2f} "
                f"incremental_ms_p50={metrics['incremental_ms_p50']:.2f} "
                f"incremental_ms_p95={metrics['incremental_ms_p95']:.2f} "
                f"history={history_path}"
            )
            self.assertLess(elapsed_ms, 500.0)


if __name__ == "__main__":
    unittest.main()
