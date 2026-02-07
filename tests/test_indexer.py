from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.store.database import Database


class IndexerTests(unittest.TestCase):
    def test_full_index_is_idempotent_for_files_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "main.py").write_text("print('v1')\n", encoding="utf-8")

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()

            first_stats = full_index(repo_root=repo_root, db=db)
            second_stats = full_index(repo_root=repo_root, db=db)

            files = db.query("SELECT path, language FROM files ORDER BY path;")
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["path"], "src/main.py")
            self.assertEqual(files[0]["language"], "python")
            self.assertEqual(first_stats.files_indexed, 1)
            self.assertEqual(second_stats.files_indexed, 1)


if __name__ == "__main__":
    unittest.main()
