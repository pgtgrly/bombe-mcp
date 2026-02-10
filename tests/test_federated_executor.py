"""Tests for FederatedQueryExecutor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import (
    FileRecord, ShardInfo,
    SymbolRecord, SymbolSearchRequest,
)
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.router import ShardRouter
from bombe.query.federated.planner import FederatedQueryPlanner
from bombe.query.federated.executor import FederatedQueryExecutor


class TestFederatedQueryExecutor(unittest.TestCase):

    def _setup(self, tmp: str) -> FederatedQueryExecutor:
        catalog = ShardCatalog(Path(tmp) / "catalog.db")
        catalog.init_schema()

        # Shard A with a function
        db_a_path = Path(tmp) / "a" / ".bombe" / "bombe.db"
        db_a_path.parent.mkdir(parents=True)
        db_a = Database(db_a_path)
        db_a.init_schema()
        db_a.upsert_files([FileRecord(path="src/app.py", language="python", content_hash="h1")])
        db_a.replace_file_symbols("src/app.py", [
            SymbolRecord(name="main", qualified_name="app.main", kind="function",
                         file_path="src/app.py", start_line=1, end_line=10,
                         signature="def main()"),
        ])
        catalog.register_shard(ShardInfo(
            repo_id="aaa", repo_path=str(Path(tmp) / "a"), db_path=str(db_a_path),
        ))
        catalog.refresh_exported_symbols("aaa", db_a)

        # Shard B with a different function
        db_b_path = Path(tmp) / "b" / ".bombe" / "bombe.db"
        db_b_path.parent.mkdir(parents=True)
        db_b = Database(db_b_path)
        db_b.init_schema()
        db_b.upsert_files([FileRecord(path="src/lib.py", language="python", content_hash="h2")])
        db_b.replace_file_symbols("src/lib.py", [
            SymbolRecord(name="helper", qualified_name="lib.helper", kind="function",
                         file_path="src/lib.py", start_line=1, end_line=5,
                         signature="def helper()"),
        ])
        catalog.register_shard(ShardInfo(
            repo_id="bbb", repo_path=str(Path(tmp) / "b"), db_path=str(db_b_path),
        ))
        catalog.refresh_exported_symbols("bbb", db_b)

        router = ShardRouter(catalog)
        planner = FederatedQueryPlanner(catalog, router)
        return FederatedQueryExecutor(catalog, router, planner)

    def test_federated_search_returns_results_from_both_shards(self):
        """Federated search finds symbols across shards."""
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._setup(tmp)
            req = SymbolSearchRequest(query="main", kind="any", limit=20)
            result = executor.execute_search(req)
            # main is in shard A
            names = [r.get("name") for r in result.results if isinstance(r, dict)]
            self.assertIn("main", names)
            self.assertEqual(result.shards_queried, 2)
            self.assertEqual(result.shards_failed, 0)

    def test_federated_search_shard_reports(self):
        """Federated search includes shard reports."""
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._setup(tmp)
            req = SymbolSearchRequest(query="helper", kind="any", limit=20)
            result = executor.execute_search(req)
            self.assertEqual(len(result.shard_reports), 2)
            for report in result.shard_reports:
                self.assertIn("shard_id", report)
                self.assertIn("status", report)

    def test_federated_search_no_results(self):
        """Federated search with no matches returns empty."""
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._setup(tmp)
            req = SymbolSearchRequest(query="nonexistent_xyz", kind="any", limit=20)
            result = executor.execute_search(req)
            self.assertEqual(result.total_matches, 0)

    def test_execute_references(self):
        """Federated reference query doesn't crash."""
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._setup(tmp)
            result = executor.execute_references("app.main", "both", 1)
            self.assertIsNotNone(result)
            self.assertEqual(result.shards_failed, 0)

    def test_execute_blast_radius(self):
        """Federated blast radius doesn't crash."""
        with tempfile.TemporaryDirectory() as tmp:
            executor = self._setup(tmp)
            result = executor.execute_blast_radius("app.main")
            self.assertIsNotNone(result)
