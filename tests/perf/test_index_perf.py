from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.store.database import Database


@unittest.skipUnless(os.getenv("BOMBE_RUN_PERF") == "1", "Perf tests are opt-in.")
class IndexPerformanceTests(unittest.TestCase):
    def test_full_index_medium_fixture_under_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            for i in range(200):
                (root / "src" / f"module_{i}.py").write_text(
                    "def a():\n    return 1\n\ndef b():\n    return a()\n",
                    encoding="utf-8",
                )
            db = Database(root / ".bombe" / "bombe.db")
            db.init_schema()
            started = time.perf_counter()
            full_index(root, db)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 30.0)


if __name__ == "__main__":
    unittest.main()
