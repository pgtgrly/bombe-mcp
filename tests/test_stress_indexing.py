from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.models import SymbolSearchRequest
from bombe.query.search import search_symbols
from bombe.store.database import Database


class StressIndexingTests(unittest.TestCase):
    def test_full_index_scales_on_medium_synthetic_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            src = repo_root / "src"
            src.mkdir()
            module_count = 180
            for index in range(module_count):
                (src / f"module_{index}.py").write_text(
                    (
                        f"def helper_{index}():\n"
                        f"    return {index}\n\n"
                        f"def caller_{index}():\n"
                        f"    return helper_{index}()\n"
                    ),
                    encoding="utf-8",
                )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()
            stats = full_index(repo_root, db)

            self.assertEqual(stats.files_indexed, module_count)
            symbol_count = db.query("SELECT COUNT(*) AS count FROM symbols;")[0]["count"]
            edge_count = db.query(
                "SELECT COUNT(*) AS count FROM edges WHERE relationship = 'CALLS';"
            )[0]["count"]
            self.assertGreaterEqual(symbol_count, module_count * 2)
            self.assertGreaterEqual(edge_count, module_count)

            query_result = search_symbols(
                db,
                SymbolSearchRequest(query="caller_179", kind="function", limit=5),
            )
            self.assertGreaterEqual(query_result.total_matches, 1)
            self.assertEqual(query_result.symbols[0]["name"], "caller_179")


if __name__ == "__main__":
    unittest.main()
