from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from bombe.indexer.parser import parse_file as parser_parse_file
from bombe.indexer.pipeline import full_index
from bombe.store.database import Database


class IndexerTests(unittest.TestCase):
    def test_full_index_is_idempotent_for_files_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "main.py").write_text(
                "def helper():\n    return 1\n\n"
                "def run():\n    return helper()\n",
                encoding="utf-8",
            )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()

            first_stats = full_index(repo_root=repo_root, db=db)
            second_stats = full_index(repo_root=repo_root, db=db)

            files = db.query("SELECT path, language FROM files ORDER BY path;")
            symbols = db.query("SELECT COUNT(*) AS count FROM symbols;")
            edges = db.query("SELECT COUNT(*) AS count FROM edges WHERE relationship = 'CALLS';")
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0]["path"], "src/main.py")
            self.assertEqual(files[0]["language"], "python")
            self.assertEqual(first_stats.files_indexed, 1)
            self.assertEqual(second_stats.files_indexed, 1)
            self.assertGreaterEqual(first_stats.symbols_indexed, 2)
            self.assertGreaterEqual(symbols[0]["count"], 2)
            self.assertGreaterEqual(edges[0]["count"], 1)

    def test_full_index_records_parse_diagnostic_and_continues_in_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "ok.py").write_text(
                "def keep():\n    return 1\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "broken.py").write_text(
                "def broken():\n    return 2\n",
                encoding="utf-8",
            )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()

            def _patched_parse(path: Path, language: str):
                if path.name == "broken.py":
                    raise RuntimeError("simulated parser crash")
                return parser_parse_file(path, language)

            with mock.patch("bombe.indexer.pipeline.parse_file", side_effect=_patched_parse):
                stats = full_index(repo_root=repo_root, db=db)

            self.assertIsNotNone(stats.run_id)
            self.assertEqual(int(stats.diagnostics_summary["total"]), 1)
            self.assertEqual(int(stats.diagnostics_summary["by_stage"]["parse"]), 1)
            rows = db.list_indexing_diagnostics(limit=10, run_id=stats.run_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0]["stage"]), "parse")
            self.assertEqual(str(rows[0]["file_path"]), "src/broken.py")
            symbols = db.query("SELECT COUNT(*) AS count FROM symbols;")
            self.assertGreaterEqual(int(symbols[0]["count"]), 1)

    def test_full_index_strict_profile_raises_after_recording_parse_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "ok.py").write_text(
                "def keep():\n    return 1\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "broken.py").write_text(
                "def broken():\n    return 2\n",
                encoding="utf-8",
            )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()

            def _patched_parse(path: Path, language: str):
                if path.name == "broken.py":
                    raise RuntimeError("simulated parser crash")
                return parser_parse_file(path, language)

            with mock.patch("bombe.indexer.pipeline.parse_file", side_effect=_patched_parse):
                with mock.patch.dict(os.environ, {"BOMBE_REQUIRE_TREE_SITTER": "1"}, clear=False):
                    with self.assertRaises(RuntimeError):
                        full_index(repo_root=repo_root, db=db)

            rows = db.list_indexing_diagnostics(limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0]["stage"]), "parse")

    def test_full_index_respects_include_and_exclude_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "a.py").write_text(
                "def alpha():\n    return 1\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "b.py").write_text(
                "def beta():\n    return 2\n",
                encoding="utf-8",
            )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()
            stats = full_index(
                repo_root=repo_root,
                db=db,
                include_patterns=["src/*.py"],
                exclude_patterns=["*b.py"],
            )
            self.assertEqual(int(stats.files_indexed), 1)
            files = db.query("SELECT path FROM files ORDER BY path;")
            self.assertEqual([str(row["path"]) for row in files], ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
