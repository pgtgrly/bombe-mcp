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

    def test_method_body_unqualified_call_prefers_same_class_method(self) -> None:
        source = (
            "class Service:\n"
            "    def caller(self):\n"
            "        render()\n"
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
                    qualified_name="pkg.render",
                    kind="function",
                    file_path="pkg/render.py",
                    start_line=1,
                    end_line=2,
                )
            ]

            edges = build_call_edges(parsed, file_symbols, candidates)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.service.Service.render"))
            self.assertEqual(edges[0].confidence, 1.0)

    def test_alias_import_resolution_prefers_original_symbol_name(self) -> None:
        source = (
            "from app.auth import util as helper\n"
            "def caller():\n"
            "    helper()\n"
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
                    name="helper",
                    qualified_name="pkg.helper",
                    kind="function",
                    file_path="pkg/helper.py",
                    start_line=1,
                    end_line=2,
                ),
            ]

            edges = build_call_edges(parsed, file_symbols, candidates)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.auth.util"))
            self.assertEqual(edges[0].confidence, 1.0)

    def test_call_edges_can_use_explicit_symbol_id_lookup(self) -> None:
        source = (
            "def caller():\n"
            "    callee()\n"
            "\n"
            "def callee():\n"
            "    return 1\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "mod.py"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "python")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="pkg.mod.caller",
                    kind="function",
                    file_path=file_path.as_posix(),
                    start_line=1,
                    end_line=2,
                ),
                SymbolRecord(
                    name="callee",
                    qualified_name="pkg.mod.callee",
                    kind="function",
                    file_path=file_path.as_posix(),
                    start_line=4,
                    end_line=5,
                ),
            ]
            lookup = {
                ("pkg.mod.caller", file_path.as_posix()): 101,
                ("pkg.mod.callee", file_path.as_posix()): 202,
            }
            edges = build_call_edges(
                parsed,
                file_symbols,
                file_symbols,
                symbol_id_lookup=lookup,
            )
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].source_id, 101)
            self.assertEqual(edges[0].target_id, 202)

    def test_receiver_type_hint_from_local_instantiation_prefers_method_owner(self) -> None:
        source = (
            "class AuthService:\n"
            "    def validate(self):\n"
            "        return True\n"
            "\n"
            "class AuditService:\n"
            "    def validate(self):\n"
            "        return False\n"
            "\n"
            "def caller():\n"
            "    svc = AuthService()\n"
            "    return svc.validate()\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "service.py"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "python")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.service.caller",
                    kind="function",
                    file_path=file_path.as_posix(),
                    start_line=9,
                    end_line=11,
                ),
                SymbolRecord(
                    name="validate",
                    qualified_name="app.service.AuthService.validate",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=2,
                    end_line=3,
                ),
                SymbolRecord(
                    name="validate",
                    qualified_name="app.service.AuditService.validate",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=6,
                    end_line=7,
                ),
            ]
            edges = build_call_edges(parsed, file_symbols, file_symbols)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.service.AuthService.validate"))

    def test_receiver_type_hint_from_self_member_assignment_prefers_member_type(self) -> None:
        source = (
            "class Client:\n"
            "    def run(self):\n"
            "        return 1\n"
            "\n"
            "class Service:\n"
            "    def __init__(self):\n"
            "        self.client = Client()\n"
            "    def caller(self):\n"
            "        return self.client.run()\n"
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
                    start_line=8,
                    end_line=9,
                ),
                SymbolRecord(
                    name="run",
                    qualified_name="app.service.Client.run",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=2,
                    end_line=3,
                ),
                SymbolRecord(
                    name="run",
                    qualified_name="app.service.Other.run",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=12,
                    end_line=13,
                ),
            ]
            edges = build_call_edges(parsed, file_symbols, file_symbols)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_id, symbol_id("app.service.Client.run"))

    def test_typescript_new_constructor_receiver_prefers_owner_method(self) -> None:
        source = (
            "class SearchService {\n"
            "  run() { return 1; }\n"
            "}\n"
            "class AuditService {\n"
            "  run() { return 0; }\n"
            "}\n"
            "const svc = new SearchService();\n"
            "function caller() {\n"
            "  return svc.run();\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "service.ts"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "typescript")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.service.caller",
                    kind="function",
                    file_path=file_path.as_posix(),
                    start_line=8,
                    end_line=10,
                ),
                SymbolRecord(
                    name="run",
                    qualified_name="app.service.SearchService.run",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=2,
                    end_line=2,
                ),
                SymbolRecord(
                    name="run",
                    qualified_name="app.service.AuditService.run",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=5,
                    end_line=5,
                ),
            ]
            edges = build_call_edges(parsed, file_symbols, file_symbols)
            caller_id = symbol_id("app.service.caller")
            caller_edges = [
                edge for edge in edges if edge.source_id == caller_id and int(edge.line_number or 0) == 9
            ]
            self.assertEqual(len(caller_edges), 1)
            self.assertEqual(caller_edges[0].target_id, symbol_id("app.service.SearchService.run"))

    def test_java_new_constructor_receiver_prefers_owner_method(self) -> None:
        source = (
            "class SearchService {\n"
            "  int run() { return 1; }\n"
            "}\n"
            "class AuditService {\n"
            "  int run() { return 0; }\n"
            "}\n"
            "class Main {\n"
            "  int caller() {\n"
            "    SearchService svc = new SearchService();\n"
            "    return svc.run();\n"
            "  }\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "Main.java"
            file_path.write_text(source, encoding="utf-8")
            parsed = parse_file(file_path, "java")
            file_symbols = [
                SymbolRecord(
                    name="caller",
                    qualified_name="app.main.Main.caller",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=8,
                    end_line=11,
                ),
                SymbolRecord(
                    name="run",
                    qualified_name="app.main.SearchService.run",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=2,
                    end_line=2,
                ),
                SymbolRecord(
                    name="run",
                    qualified_name="app.main.AuditService.run",
                    kind="method",
                    file_path=file_path.as_posix(),
                    start_line=5,
                    end_line=5,
                ),
            ]
            edges = build_call_edges(parsed, file_symbols, file_symbols)
            caller_id = symbol_id("app.main.Main.caller")
            caller_edges = [
                edge for edge in edges if edge.source_id == caller_id and int(edge.line_number or 0) == 10
            ]
            self.assertEqual(len(caller_edges), 1)
            self.assertEqual(caller_edges[0].target_id, symbol_id("app.main.SearchService.run"))


if __name__ == "__main__":
    unittest.main()
