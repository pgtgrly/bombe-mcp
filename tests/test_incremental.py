from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index, incremental_index
from bombe.models import FileChange
from bombe.store.database import Database


class IncrementalIndexerTests(unittest.TestCase):
    def test_incremental_index_updates_only_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            file_a = repo_root / "src" / "a.py"
            file_b = repo_root / "src" / "b.py"
            file_a.write_text(
                "def alpha():\n    return 1\n",
                encoding="utf-8",
            )
            file_b.write_text(
                "def beta():\n    return 2\n",
                encoding="utf-8",
            )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()
            full_index(repo_root, db)

            before = db.query("SELECT path, content_hash FROM files ORDER BY path;")
            hash_before = {row["path"]: row["content_hash"] for row in before}

            file_a.write_text(
                "def alpha():\n    return 3\n",
                encoding="utf-8",
            )
            incremental_index(
                repo_root,
                db,
                changes=[FileChange(status="M", path="src/a.py")],
            )

            after = db.query("SELECT path, content_hash FROM files ORDER BY path;")
            hash_after = {row["path"]: row["content_hash"] for row in after}
            self.assertNotEqual(hash_before["src/a.py"], hash_after["src/a.py"])
            self.assertEqual(hash_before["src/b.py"], hash_after["src/b.py"])
            symbols = db.query("SELECT COUNT(*) AS count FROM symbols;")
            self.assertGreaterEqual(symbols[0]["count"], 2)


if __name__ == "__main__":
    unittest.main()
