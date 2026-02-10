"""End-to-end correctness tests for cross-repo federated queries."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import (
    ExternalDepRecord, FileRecord, ShardInfo, SymbolRecord, SymbolSearchRequest,
)
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.cross_repo_resolver import (
    compute_repo_id,
    post_index_cross_repo_sync,
)
from bombe.store.sharding.router import ShardRouter
from bombe.query.federated.planner import FederatedQueryPlanner
from bombe.query.federated.executor import FederatedQueryExecutor


class TestCrossRepoEndToEnd(unittest.TestCase):
    """End-to-end: set up repos with cross-repo imports, index, sync, query."""

    def _build_repos(self, tmp: str) -> tuple[FederatedQueryExecutor, ShardCatalog]:
        """Build two repos: app imports lib.helper from lib repo."""
        catalog = ShardCatalog(Path(tmp) / "catalog.db")
        catalog.init_schema()

        # Repo: lib (exports lib.helper)
        lib_root = Path(tmp) / "lib"
        lib_root.mkdir()
        db_lib_path = Path(tmp) / "lib" / ".bombe" / "bombe.db"
        db_lib_path.parent.mkdir(parents=True)
        db_lib = Database(db_lib_path)
        db_lib.init_schema()
        db_lib.upsert_files([FileRecord(path="src/helper.py", language="python", content_hash="h1")])
        db_lib.replace_file_symbols("src/helper.py", [
            SymbolRecord(name="helper", qualified_name="lib.helper", kind="function",
                         file_path="src/helper.py", start_line=1, end_line=10,
                         signature="def helper(x)"),
        ])
        lib_id = compute_repo_id(lib_root)
        catalog.register_shard(ShardInfo(
            repo_id=lib_id, repo_path=str(lib_root), db_path=str(db_lib_path),
        ))
        post_index_cross_repo_sync(lib_root, db_lib, catalog)

        # Repo: app (imports lib.helper)
        app_root = Path(tmp) / "app"
        app_root.mkdir()
        db_app_path = Path(tmp) / "app" / ".bombe" / "bombe.db"
        db_app_path.parent.mkdir(parents=True)
        db_app = Database(db_app_path)
        db_app.init_schema()
        db_app.upsert_files([FileRecord(path="src/main.py", language="python", content_hash="h2")])
        db_app.replace_file_symbols("src/main.py", [
            SymbolRecord(name="run", qualified_name="app.run", kind="function",
                         file_path="src/main.py", start_line=1, end_line=8,
                         signature="def run()"),
        ])
        db_app.replace_external_deps("src/main.py", [
            ExternalDepRecord(file_path="src/main.py", import_statement="import lib.helper",
                              module_name="lib.helper", line_number=1),
        ])
        app_id = compute_repo_id(app_root)
        catalog.register_shard(ShardInfo(
            repo_id=app_id, repo_path=str(app_root), db_path=str(db_app_path),
        ))
        post_index_cross_repo_sync(app_root, db_app, catalog)

        router = ShardRouter(catalog)
        planner = FederatedQueryPlanner(catalog, router)
        executor = FederatedQueryExecutor(catalog, router, planner)
        return executor, catalog

    def test_cross_repo_edges_created(self):
        """post_index_cross_repo_sync creates cross-repo edges."""
        with tempfile.TemporaryDirectory() as tmp:
            _, catalog = self._build_repos(tmp)
            count = catalog.query("SELECT COUNT(*) AS cnt FROM cross_repo_edges;")
            self.assertGreater(int(count[0]["cnt"]), 0)

    def test_federated_search_finds_symbols_across_repos(self):
        """Federated search returns symbols from both repos."""
        with tempfile.TemporaryDirectory() as tmp:
            executor, _ = self._build_repos(tmp)
            result = executor.execute_search(
                SymbolSearchRequest(query="helper", kind="any", limit=20)
            )
            names = [r.get("name") for r in result.results if isinstance(r, dict)]
            self.assertIn("helper", names)

    def test_federated_search_deterministic(self):
        """Same query twice produces same results."""
        with tempfile.TemporaryDirectory() as tmp:
            executor, _ = self._build_repos(tmp)
            req = SymbolSearchRequest(query="run", kind="any", limit=20)
            r1 = executor.execute_search(req)
            r2 = executor.execute_search(req)
            self.assertEqual(r1.total_matches, r2.total_matches)
            self.assertEqual(len(r1.results), len(r2.results))

    def test_exported_symbols_populated(self):
        """Catalog has exported symbols from both repos."""
        with tempfile.TemporaryDirectory() as tmp:
            _, catalog = self._build_repos(tmp)
            count = catalog.query("SELECT COUNT(*) AS cnt FROM exported_symbols;")
            self.assertGreaterEqual(int(count[0]["cnt"]), 2)

    def test_get_cross_repo_edges_from_catalog(self):
        """Can query cross-repo edges from the catalog."""
        with tempfile.TemporaryDirectory() as tmp:
            _, catalog = self._build_repos(tmp)
            edges = catalog.query("SELECT * FROM cross_repo_edges;")
            self.assertTrue(len(edges) >= 1)
            self.assertEqual(edges[0]["relationship"], "IMPORTS")
