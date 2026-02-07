from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index, incremental_index
from bombe.models import FileChange
from bombe.store.database import Database


@unittest.skipUnless(os.getenv("BOMBE_RUN_PERF") == "1", "Perf tests are opt-in.")
class IncrementalPerformanceTests(unittest.TestCase):
    def test_single_file_incremental_under_target(self) -> None:
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
            full_index(root, db)
            changed = root / "src" / "file_0.py"
            changed.write_text("def run():\n    return 2\n", encoding="utf-8")
            started = time.perf_counter()
            incremental_index(root, db, [FileChange(status="M", path="src/file_0.py")])
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertLess(elapsed_ms, 500.0)


if __name__ == "__main__":
    unittest.main()
