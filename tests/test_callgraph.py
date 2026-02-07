from __future__ import annotations

import tempfile
import unittest
import zlib
from pathlib import Path

from bombe.indexer.callgraph import build_call_edges
from bombe.indexer.parser import parse_file
from bombe.models import SymbolRecord


def symbol_id(qualified_name: str) -> int:
    return int(zlib.crc32(qualified_name.encode("utf-8")) & 0x7FFFFFFF)


class CallGraphTests(unittest.TestCase):
    def test_same_file_resolution_takes_priority(self) -> None:
        source = (
            "def caller():\n"
            "    bar()\n"
            "\n"
            "def bar():\n"
            "    return 1\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "service.py"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "python")
            file_path_str = file_path.as_posix()
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.service.caller",
                    kind="function",
                    file_path=file_path_str,
                    start_line=1,
                    end_line=2,
                ),
                SymbolRecord(
                    name="bar",
                    qualified_name="app.service.bar",
                    kind="function",
                    file_path=file_path_str,
                    start_line=4,
                    end_line=5,
                ),
            ]
            candidates = file_symbols + [
                SymbolRecord(
                    name="bar",
                    qualified_name="other.lib.bar",
                    kind="function",
                    file_path="other/lib.py",
                    start_line=1,
                    end_line=2,
                )
            ]

            edges = build_call_edges(parsed, file_symbols, candidates)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.service.bar"))
            self.assertEqual(edges[0].confidence, 1.0)

    def test_ambiguous_global_resolution_lowers_confidence(self) -> None:
        source = (
            "def caller():\n"
            "    baz()\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "entry.py"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "python")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.entry.caller",
                    kind="function",
                    file_path=file_path.as_posix(),
                    start_line=1,
                    end_line=2,
                )
            ]
            candidates = file_symbols + [
                SymbolRecord(
                    name="baz",
                    qualified_name="pkg.one.baz",
                    kind="function",
                    file_path="pkg/one.py",
                    start_line=1,
                    end_line=2,
                ),
                SymbolRecord(
                    name="baz",
                    qualified_name="pkg.two.baz",
                    kind="function",
                    file_path="pkg/two.py",
                    start_line=1,
                    end_line=2,
                ),
            ]

            edges = build_call_edges(parsed, file_symbols, candidates)
            self.assertEqual(len(edges), 2)
            self.assertTrue(all(edge.confidence == 0.5 for edge in edges))

    def test_import_scoped_resolution_is_preferred(self) -> None:
        source = (
            "from app.auth import util\n"
            "def caller():\n"
            "    util()\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "entry.py"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "python")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.entry.caller",
                    kind="function",
                    file_path=file_path.as_posix(),
                    start_line=2,
                    end_line=3,
                )
            ]
            candidates = file_symbols + [
                SymbolRecord(
                    name="util",
                    qualified_name="app.auth.util",
                    kind="function",
                    file_path="app/auth.py",
                    start_line=1,
                    end_line=2,
                ),
                SymbolRecord(
                    name="util",
                    qualified_name="pkg.other.util",
                    kind="function",
                    file_path="pkg/other.py",
                    start_line=1,
                    end_line=2,
                ),
            ]
            edges = build_call_edges(parsed, file_symbols, candidates)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.auth.util"))
            self.assertEqual(edges[0].confidence, 1.0)

    def test_self_receiver_prefers_method_on_same_class(self) -> None:
        source = (
            "class Service:\n"
            "    def caller(self):\n"
            "        self.render()\n"
            "    def render(self):\n"
            "        return 1\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "service.py"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "python")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.service.Service.caller",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=2,
                    end_line=3,
                ),
                SymbolRecord(
                    name="render",
                    qualified_name="app.service.Service.render",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=4,
                    end_line=5,
                ),
            ]
            candidates = file_symbols + [
                SymbolRecord(
                    name="render",
                    qualified_name="pkg.other.render",
                    kind="function",
                    file_path="pkg/other.py",
                    start_line=1,
                    end_line=2,
                )
            ]

            edges = build_call_edges(parsed, file_symbols, candidates)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.service.Service.render"))
            self.assertEqual(edges[0].confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
