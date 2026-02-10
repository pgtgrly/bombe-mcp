"""Performance benchmarks for cross-repo sharding."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from bombe.models import FileRecord, ShardInfo, SymbolRecord, SymbolSearchRequest
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.cross_repo_resolver import compute_repo_id, post_index_cross_repo_sync
from bombe.store.sharding.router import ShardRouter
from bombe.query.federated.planner import FederatedQueryPlanner
from bombe.query.federated.executor import FederatedQueryExecutor


@unittest.skipUnless(os.environ.get("BOMBE_RUN_PERF") == "1", "BOMBE_RUN_PERF not set")
class TestShardPerf(unittest.TestCase):
    """Performance benchmarks for sharding operations."""

    def _create_shard(self, tmp: str, name: str, symbol_count: int) -> tuple[str, Database]:
        """Create a shard with N symbols."""
        db_path = Path(tmp) / name / ".bombe" / "bombe.db"
        db_path.parent.mkdir(parents=True)
        db = Database(db_path)
        db.init_schema()
        file_path = f"src/{name}.py"
        db.upsert_files([FileRecord(path=file_path, language="python", content_hash=f"h_{name}")])
        symbols = [
            SymbolRecord(
                name=f"func_{i}",
                qualified_name=f"{name}.func_{i}",
                kind="function",
                file_path=file_path,
                start_line=i * 10 + 1,
                end_line=i * 10 + 9,
            )
            for i in range(symbol_count)
        ]
        db.replace_file_symbols(file_path, symbols)
        return str(db_path), db

    def test_catalog_refresh_throughput(self):
        """Measure exported symbol refresh throughput."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            db_path, db = self._create_shard(tmp, "big", 1000)
            catalog.register_shard(ShardInfo(
                repo_id="big", repo_path=str(Path(tmp) / "big"), db_path=db_path,
            ))
            start = time.perf_counter()
            count = catalog.refresh_exported_symbols("big", db)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.assertEqual(count, 1000)
            # Should complete in under 5 seconds
            self.assertLess(elapsed_ms, 5000, f"Refresh took {elapsed_ms:.0f}ms")

    def test_federated_search_latency(self):
        """Measure federated search latency across multiple shards."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()

            for i in range(4):
                name = f"repo_{i}"
                db_path, db = self._create_shard(tmp, name, 200)
                repo_id = f"repo{i:04d}"
                catalog.register_shard(ShardInfo(
                    repo_id=repo_id, repo_path=str(Path(tmp) / name), db_path=db_path,
                ))
                catalog.refresh_exported_symbols(repo_id, db)

            router = ShardRouter(catalog)
            planner = FederatedQueryPlanner(catalog, router)
            executor = FederatedQueryExecutor(catalog, router, planner)

            start = time.perf_counter()
            result = executor.execute_search(
                SymbolSearchRequest(query="func_1", kind="any", limit=20)
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            self.assertGreater(result.total_matches, 0)
            self.assertEqual(result.shards_queried, 4)
            # Should complete in under 5 seconds
            self.assertLess(elapsed_ms, 5000, f"Search took {elapsed_ms:.0f}ms")

    def test_cross_repo_resolution_throughput(self):
        """Measure cross-repo import resolution throughput."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()

            # Create lib shard with symbols
            lib_path, lib_db = self._create_shard(tmp, "lib", 500)
            catalog.register_shard(ShardInfo(
                repo_id="lib0", repo_path=str(Path(tmp) / "lib"), db_path=lib_path,
            ))
            catalog.refresh_exported_symbols("lib0", lib_db)

            # Create app shard with many external deps
            app_root = Path(tmp) / "app"
            app_root.mkdir(parents=True, exist_ok=True)
            db_app_path = app_root / ".bombe" / "bombe.db"
            db_app_path.parent.mkdir(parents=True, exist_ok=True)
            db_app = Database(db_app_path)
            db_app.init_schema()
            db_app.upsert_files([FileRecord(path="src/main.py", language="python", content_hash="h_app")])
            db_app.replace_file_symbols("src/main.py", [
                SymbolRecord(name="main", qualified_name="app.main", kind="function",
                             file_path="src/main.py", start_line=1, end_line=5),
            ])
            from bombe.models import ExternalDepRecord
            deps = [
                ExternalDepRecord(
                    file_path="src/main.py",
                    import_statement=f"import lib.func_{i}",
                    module_name=f"lib.func_{i}",
                    line_number=i + 1,
                )
                for i in range(100)
            ]
            db_app.replace_external_deps("src/main.py", deps)

            app_id = compute_repo_id(app_root)
            catalog.register_shard(ShardInfo(
                repo_id=app_id, repo_path=str(app_root), db_path=str(db_app_path),
            ))

            start = time.perf_counter()
            result = post_index_cross_repo_sync(app_root, db_app, catalog)
            elapsed_ms = (time.perf_counter() - start) * 1000

            self.assertGreater(result["exported_symbols"], 0)
            # Should complete in under 10 seconds
            self.assertLess(elapsed_ms, 10000, f"Sync took {elapsed_ms:.0f}ms")
