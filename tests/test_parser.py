from __future__ import annotations

import importlib.util
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
                if importlib.util.find_spec("tree_sitter_languages") is None:
                    self.assertIsNone(parsed.tree)
                else:
                    self.assertIsNotNone(parsed.tree)

    def test_parse_unsupported_language_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "main.rb"
            path.write_text("puts 'hi'\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_file(path, "ruby")

    def test_parse_invalid_utf8_uses_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.py"
            path.write_bytes(b"def run():\n    return '\\xff'\n")
            parsed = parse_file(path, "python")
            self.assertIn("def run", parsed.source)

    def test_parse_python_syntax_error_returns_no_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "broken.py"
            path.write_text("def bad(:\n    pass\n", encoding="utf-8")
            parsed = parse_file(path, "python")
            self.assertIsNone(parsed.tree)


if __name__ == "__main__":
    unittest.main()
