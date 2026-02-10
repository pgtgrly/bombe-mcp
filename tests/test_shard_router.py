"""Tests for ShardRouter."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import FileRecord, ShardInfo, SymbolRecord
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.router import ShardRouter


class TestShardRouter(unittest.TestCase):

    def _setup_catalog_and_shards(self, tmp: str) -> tuple[ShardCatalog, str, str]:
        """Helper: create catalog with two shards that have actual DBs."""
        catalog = ShardCatalog(Path(tmp) / "catalog.db")
        catalog.init_schema()

        # Shard A
        db_a_path = Path(tmp) / "a" / ".bombe" / "bombe.db"
        db_a_path.parent.mkdir(parents=True)
        db_a = Database(db_a_path)
        db_a.init_schema()
        db_a.upsert_files([FileRecord(path="src/app.py", language="python", content_hash="aaa")])
        db_a.replace_file_symbols("src/app.py", [
            SymbolRecord(name="main", qualified_name="app.main", kind="function",
                         file_path="src/app.py", start_line=1, end_line=5),
        ])
        catalog.register_shard(ShardInfo(
            repo_id="aaa", repo_path=str(Path(tmp) / "a"),
            db_path=str(db_a_path),
        ))
        catalog.refresh_exported_symbols("aaa", db_a)

        # Shard B
        db_b_path = Path(tmp) / "b" / ".bombe" / "bombe.db"
        db_b_path.parent.mkdir(parents=True)
        db_b = Database(db_b_path)
        db_b.init_schema()
        db_b.upsert_files([FileRecord(path="src/lib.py", language="python", content_hash="bbb")])
        db_b.replace_file_symbols("src/lib.py", [
            SymbolRecord(name="helper", qualified_name="lib.helper", kind="function",
                         file_path="src/lib.py", start_line=1, end_line=3),
        ])
        catalog.register_shard(ShardInfo(
            repo_id="bbb", repo_path=str(Path(tmp) / "b"),
            db_path=str(db_b_path),
        ))
        catalog.refresh_exported_symbols("bbb", db_b)

        return catalog, "aaa", "bbb"

    def test_get_shard_db(self):
        """get_shard_db returns Database for valid shard."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, sid_a, sid_b = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            db = router.get_shard_db(sid_a)
            self.assertIsNotNone(db)
            # Second call should return cached
            db2 = router.get_shard_db(sid_a)
            self.assertIs(db, db2)

    def test_get_shard_db_missing(self):
        """get_shard_db returns None for unknown shard."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, _, _ = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            self.assertIsNone(router.get_shard_db("nonexistent"))

    def test_route_symbol_query(self):
        """route_symbol_query routes to shard containing the symbol."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, sid_a, sid_b = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            # "main" exists only in shard A
            ids = router.route_symbol_query("main")
            self.assertIn(sid_a, ids)

    def test_route_symbol_query_fallback(self):
        """route_symbol_query falls back to all shards for unknown symbol."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, sid_a, sid_b = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            ids = router.route_symbol_query("nonexistent_symbol")
            self.assertEqual(len(ids), 2)  # both shards

    def test_all_shard_ids(self):
        """all_shard_ids returns all enabled shards."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, sid_a, sid_b = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            ids = router.all_shard_ids()
            self.assertEqual(set(ids), {sid_a, sid_b})

    def test_shard_health(self):
        """shard_health reports ok for accessible shards."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, sid_a, sid_b = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            health = router.shard_health()
            self.assertEqual(len(health), 2)
            statuses = {h["repo_id"]: h["status"] for h in health}
            self.assertEqual(statuses[sid_a], "ok")
            self.assertEqual(statuses[sid_b], "ok")

    def test_close_all(self):
        """close_all clears the connection pool."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog, sid_a, _ = self._setup_catalog_and_shards(tmp)
            router = ShardRouter(catalog)
            router.get_shard_db(sid_a)
            router.close_all()
            # Pool should be cleared — next get creates new connection
            self.assertEqual(len(router._connection_pool), 0)
