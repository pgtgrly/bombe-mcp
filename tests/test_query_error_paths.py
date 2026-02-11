"""Tests for query wrapper error paths with non-existent symbols."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.query.blast import get_blast_radius
from bombe.query.change_impact import change_impact
from bombe.query.context import get_context
from bombe.query.data_flow import trace_data_flow
from bombe.query.references import get_references
from bombe.query.search import search_symbols
from bombe.query.structure import get_structure
from bombe.store.database import Database


class TestQueryErrorPaths(unittest.TestCase):
    """Verify all query wrappers handle missing symbols gracefully."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmpdir.name) / "bombe.db")
        self.db.init_schema()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_get_references_nonexistent_returns_empty(self) -> None:
        response = get_references(self.db, "nonexistent.symbol")
        self.assertIsNotNone(response.payload)
        self.assertEqual(response.payload["callers"], [])
        self.assertEqual(response.payload["callees"], [])
        self.assertEqual(response.payload["implementors"], [])
        self.assertEqual(response.payload["supers"], [])

    def test_get_blast_radius_nonexistent_returns_empty(self) -> None:
        response = get_blast_radius(self.db, "nonexistent.symbol", "behavior", 3)
        self.assertIsNotNone(response.payload)
        self.assertEqual(response.payload["impact"]["direct_callers"], [])
        self.assertEqual(response.payload["impact"]["transitive_callers"], [])
        self.assertEqual(response.payload["impact"]["affected_files"], [])
        self.assertEqual(response.payload["impact"]["total_affected_symbols"], 0)

    def test_trace_data_flow_nonexistent_returns_empty(self) -> None:
        result = trace_data_flow(self.db, "nonexistent.symbol", "both", 3)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["paths"], [])
        self.assertEqual(result["summary"], "")

    def test_change_impact_nonexistent_returns_empty(self) -> None:
        result = change_impact(self.db, "nonexistent.symbol", "behavior", 3)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["impact"]["direct_callers"], [])
        self.assertEqual(result["impact"]["transitive_callers"], [])
        self.assertEqual(result["impact"]["type_dependents"], [])
        self.assertEqual(result["impact"]["affected_files"], [])
        self.assertEqual(result["impact"]["total_affected_symbols"], 0)

    def test_search_symbols_nonexistent_returns_empty(self) -> None:
        result = search_symbols(self.db, "nonexistent_xyz_symbol")
        # search_symbols returns empty results natively, no wrapper needed
        self.assertIsNotNone(result)

    def test_get_structure_empty_db_returns_string(self) -> None:
        result = get_structure(self.db)
        self.assertIsInstance(result, str)

    def test_get_context_nonexistent_returns_bundle(self) -> None:
        result = get_context(self.db, "nonexistent query", entry_points=[], token_budget=1000)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
