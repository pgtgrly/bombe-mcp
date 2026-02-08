from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from bombe.indexer.semantic import backend_statuses, load_receiver_type_hints


class SemanticTests(unittest.TestCase):
    def test_backend_statuses_shape(self) -> None:
        statuses = backend_statuses()
        self.assertGreaterEqual(len(statuses), 1)
        first = statuses[0]
        self.assertIn("backend", first)
        self.assertIn("available", first)
        self.assertIn("executable", first)

    def test_load_receiver_type_hints_from_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            semantic_dir = repo_root / ".bombe" / "semantic" / "src"
            semantic_dir.mkdir(parents=True, exist_ok=True)
            hint_file = semantic_dir / "main.py.hints.json"
            hint_file.write_text(
                json.dumps(
                    {
                        "receiver_hints": [
                            {
                                "receiver": "svc",
                                "owner_type": "SearchService",
                                "line_start": 10,
                                "line_end": 12,
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            hints = load_receiver_type_hints(repo_root, "src/main.py")
            self.assertIn((10, "svc"), hints)
            self.assertIn((12, "svc"), hints)
            self.assertIn("SearchService", hints[(10, "svc")])

    def test_load_receiver_type_hints_from_global_file_with_normalized_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            hints_file = repo_root / "semantic-hints.json"
            hints_file.write_text(
                json.dumps(
                    {
                        "files": {
                            "src/main.py": {
                                "receiver_hints": [
                                    {
                                        "receiver": "svc",
                                        "owner_type": "SearchService",
                                        "line": 7,
                                    }
                                ]
                            }
                        }
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            previous = os.environ.get("BOMBE_SEMANTIC_HINTS_FILE")
            os.environ["BOMBE_SEMANTIC_HINTS_FILE"] = hints_file.as_posix()
            try:
                hints = load_receiver_type_hints(repo_root, "/src\\main.py")
            finally:
                if previous is None:
                    os.environ.pop("BOMBE_SEMANTIC_HINTS_FILE", None)
                else:
                    os.environ["BOMBE_SEMANTIC_HINTS_FILE"] = previous
            self.assertIn((7, "svc"), hints)
            self.assertIn("SearchService", hints[(7, "svc")])


if __name__ == "__main__":
    unittest.main()
