from __future__ import annotations

import unittest

from bombe.models import EdgeRecord, FileRecord, SymbolRecord


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


if __name__ == "__main__":
    unittest.main()
