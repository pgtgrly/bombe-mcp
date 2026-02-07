from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.parser import parse_file


class ParserTests(unittest.TestCase):
    def test_parse_python_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "main.py"
            path.write_text("def run():\n    return 1\n", encoding="utf-8")
            parsed = parse_file(path, "python")
            self.assertEqual(parsed.language, "python")
            self.assertIsNotNone(parsed.tree)
            self.assertIn("def run", parsed.source)

    def test_parse_non_python_languages(self) -> None:
        fixtures = {
            "java": "class A {}",
            "typescript": "function run() { return 1; }",
            "go": "package main\nfunc run() int { return 1 }\n",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            for language, source in fixtures.items():
                path = Path(tmpdir) / f"sample.{language}"
                path.write_text(source, encoding="utf-8")
                parsed = parse_file(path, language)
                self.assertEqual(parsed.language, language)
                self.assertEqual(parsed.source, source)
                self.assertIsNone(parsed.tree)


if __name__ == "__main__":
    unittest.main()
