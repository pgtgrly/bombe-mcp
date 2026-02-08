from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.store.database import Database


class MultiLanguageRegressionTests(unittest.TestCase):
    def test_full_index_handles_mixed_language_repo_with_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "py").mkdir()
            (repo_root / "ts").mkdir()
            (repo_root / "go").mkdir()
            (repo_root / "java" / "com" / "example").mkdir(parents=True)

            (repo_root / "py" / "a.py").write_text(
                "from py.b import run_b\n\ndef run_a():\n    return run_b()\n",
                encoding="utf-8",
            )
            (repo_root / "py" / "b.py").write_text(
                "from py.a import run_a\n\ndef run_b():\n    return run_a()\n",
                encoding="utf-8",
            )

            (repo_root / "ts" / "index.ts").write_text(
                "import { util } from './util';\nexport function start(): number { return util(); }\n",
                encoding="utf-8",
            )
            (repo_root / "ts" / "util.ts").write_text(
                "import { start } from './index';\nexport function util(): number { return start(); }\n",
                encoding="utf-8",
            )

            (repo_root / "go.mod").write_text("module example.com/mixed\n", encoding="utf-8")
            (repo_root / "go" / "main.go").write_text(
                'package go\nimport "example.com/mixed/go/util"\nfunc main() int { return util.Run() }\n',
                encoding="utf-8",
            )
            (repo_root / "go" / "util.go").write_text(
                "package util\nfunc Run() int { return 1 }\n",
                encoding="utf-8",
            )

            (repo_root / "java" / "com" / "example" / "Main.java").write_text(
                (
                    "package com.example;\n"
                    "import com.example.Helper;\n"
                    "public class Main { public static int run() { return Helper.run(); } }\n"
                ),
                encoding="utf-8",
            )
            (repo_root / "java" / "com" / "example" / "Helper.java").write_text(
                "package com.example;\npublic class Helper { public static int run() { return 1; } }\n",
                encoding="utf-8",
            )

            db = Database(repo_root / ".bombe" / "bombe.db")
            db.init_schema()
            stats = full_index(repo_root, db)

            self.assertGreaterEqual(stats.files_indexed, 8)
            symbol_count = db.query("SELECT COUNT(*) AS count FROM symbols;")[0]["count"]
            call_edges = db.query(
                "SELECT COUNT(*) AS count FROM edges WHERE relationship = 'CALLS';"
            )[0]["count"]
            import_edges = db.query(
                "SELECT COUNT(*) AS count FROM edges WHERE relationship = 'IMPORTS';"
            )[0]["count"]
            self.assertGreater(symbol_count, 0)
            self.assertGreater(call_edges, 0)
            self.assertGreater(import_edges, 0)

            dedupe = db.query(
                """
                SELECT COUNT(*) AS count
                FROM (
                    SELECT source_id, target_id, source_type, target_type, relationship, COUNT(*) AS dup_count
                    FROM edges
                    GROUP BY source_id, target_id, source_type, target_type, relationship
                    HAVING dup_count > 1
                );
                """
            )[0]["count"]
            self.assertEqual(dedupe, 0)


if __name__ == "__main__":
    unittest.main()
