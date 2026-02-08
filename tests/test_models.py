from __future__ import annotations

import unittest

from bombe.models import (
    ARTIFACT_SCHEMA_VERSION,
    DELTA_SCHEMA_VERSION,
    MCP_CONTRACT_VERSION,
    EdgeContractRecord,
    EdgeRecord,
    FileRecord,
    SymbolKey,
    SymbolRecord,
)


class ModelTests(unittest.TestCase):
    def test_file_record_fields(self) -> None:
        record = FileRecord(
            path="src/main.py",
            language="python",
            content_hash="abc123",
            size_bytes=100,
        )
        self.assertEqual(record.path, "src/main.py")
        self.assertEqual(record.language, "python")

    def test_symbol_record_defaults(self) -> None:
        symbol = SymbolRecord(
            name="run",
            qualified_name="pkg.run",
            kind="function",
            file_path="src/main.py",
            start_line=1,
            end_line=10,
        )
        self.assertEqual(symbol.pagerank_score, 0.0)
        self.assertEqual(symbol.parameters, [])

    def test_edge_record_confidence_default(self) -> None:
        edge = EdgeRecord(
            source_id=1,
            target_id=2,
            source_type="symbol",
            target_type="symbol",
            relationship="CALLS",
        )
        self.assertEqual(edge.confidence, 1.0)

    def test_symbol_key_is_deterministic(self) -> None:
        symbol = SymbolRecord(
            name="run",
            qualified_name="pkg.run",
            kind="function",
            file_path="src/main.py",
            start_line=2,
            end_line=5,
            signature="def run(user: str) -> bool",
        )
        first = SymbolKey.from_symbol(symbol)
        second = SymbolKey.from_symbol(symbol)
        self.assertEqual(first, second)
        self.assertEqual(first.signature_hash, second.signature_hash)

    def test_edge_contract_record_builds_edge_key(self) -> None:
        source = SymbolKey.from_fields(
            qualified_name="pkg.a",
            file_path="src/a.py",
            start_line=1,
            end_line=2,
            signature="def a()",
        )
        target = SymbolKey.from_fields(
            qualified_name="pkg.b",
            file_path="src/b.py",
            start_line=3,
            end_line=4,
            signature="def b()",
        )
        edge = EdgeContractRecord(
            source=source,
            target=target,
            relationship="CALLS",
            line_number=20,
        )
        self.assertEqual(edge.key().relationship, "CALLS")
        self.assertEqual(edge.as_tuple()[2], "CALLS")
        self.assertEqual(edge.as_tuple()[3], 20)

    def test_contract_versions_are_positive(self) -> None:
        self.assertGreaterEqual(DELTA_SCHEMA_VERSION, 1)
        self.assertGreaterEqual(ARTIFACT_SCHEMA_VERSION, 1)
        self.assertGreaterEqual(MCP_CONTRACT_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
