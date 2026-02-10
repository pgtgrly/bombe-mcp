"""Tests for ShardCatalog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import CrossRepoEdge, FileRecord, GlobalSymbolURI, ShardInfo
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog


class TestShardCatalog(unittest.TestCase):

    def test_init_schema_idempotent(self):
        """init_schema can be called multiple times safely."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.init_schema()  # should not raise

    def test_register_and_list_shards(self):
        """Register shards and list them."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            s1 = ShardInfo(repo_id="aaa", repo_path="/repo/a", db_path="/repo/a/.bombe/bombe.db")
            s2 = ShardInfo(repo_id="bbb", repo_path="/repo/b", db_path="/repo/b/.bombe/bombe.db")
            catalog.register_shard(s1)
            catalog.register_shard(s2)
            shards = catalog.list_shards(enabled_only=True)
            self.assertEqual(len(shards), 2)
            ids = {s.repo_id for s in shards}
            self.assertEqual(ids, {"aaa", "bbb"})

    def test_unregister_shard(self):
        """Unregister removes shard and related data."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.register_shard(ShardInfo(repo_id="aaa", repo_path="/a", db_path="/a/db"))
            catalog.unregister_shard("aaa")
            self.assertIsNone(catalog.get_shard("aaa"))

    def test_get_shard(self):
        """Get shard by repo_id."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.register_shard(ShardInfo(repo_id="aaa", repo_path="/a", db_path="/a/db"))
            shard = catalog.get_shard("aaa")
            self.assertIsNotNone(shard)
            self.assertEqual(shard.repo_id, "aaa")
            self.assertIsNone(catalog.get_shard("nonexistent"))

    def test_update_shard_stats(self):
        """Update shard statistics."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.register_shard(ShardInfo(repo_id="aaa", repo_path="/a", db_path="/a/db"))
            catalog.update_shard_stats("aaa", symbol_count=100, edge_count=50)
            shard = catalog.get_shard("aaa")
            self.assertEqual(shard.symbol_count, 100)
            self.assertEqual(shard.edge_count, 50)

    def test_upsert_and_query_cross_repo_edges(self):
        """Upsert and query cross-repo edges."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            edge = CrossRepoEdge(
                source_uri=GlobalSymbolURI("aaa", "foo.bar", "src/foo.py"),
                target_uri=GlobalSymbolURI("bbb", "lib.util", "src/util.py"),
                relationship="IMPORTS",
                confidence=0.8,
            )
            catalog.upsert_cross_repo_edges([edge])
            outgoing = catalog.get_cross_repo_edges_from("aaa", "foo.bar")
            self.assertEqual(len(outgoing), 1)
            self.assertEqual(outgoing[0].target_uri.repo_id, "bbb")
            incoming = catalog.get_cross_repo_edges_to("bbb", "lib.util")
            self.assertEqual(len(incoming), 1)

    def test_upsert_cross_repo_edges_deduplication(self):
        """Duplicate edges are deduplicated."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            edge = CrossRepoEdge(
                source_uri=GlobalSymbolURI("aaa", "foo.bar", "src/foo.py"),
                target_uri=GlobalSymbolURI("bbb", "lib.util", "src/util.py"),
                relationship="IMPORTS",
            )
            catalog.upsert_cross_repo_edges([edge, edge])
            catalog.upsert_cross_repo_edges([edge])  # again
            outgoing = catalog.get_cross_repo_edges_from("aaa", "foo.bar")
            self.assertEqual(len(outgoing), 1)

    def test_delete_cross_repo_edges_for_repo(self):
        """Delete all edges involving a repo."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            edge = CrossRepoEdge(
                source_uri=GlobalSymbolURI("aaa", "foo", "f.py"),
                target_uri=GlobalSymbolURI("bbb", "bar", "b.py"),
                relationship="IMPORTS",
            )
            catalog.upsert_cross_repo_edges([edge])
            deleted = catalog.delete_cross_repo_edges_for_repo("aaa")
            self.assertEqual(deleted, 1)
            self.assertEqual(len(catalog.get_cross_repo_edges_from("aaa", "foo")), 0)

    def test_refresh_and_search_exported_symbols(self):
        """Refresh exported symbols from a shard DB and search them."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a shard DB with symbols
            shard_db = Database(Path(tmp) / "shard.db")
            shard_db.init_schema()
            from bombe.models import SymbolRecord
            shard_db.upsert_files([FileRecord(path="src/foo.py", language="python", content_hash="aaa")])
            shard_db.replace_file_symbols("src/foo.py", [
                SymbolRecord(
                    name="my_func",
                    qualified_name="foo.my_func",
                    kind="function",
                    file_path="src/foo.py",
                    start_line=1,
                    end_line=5,
                ),
            ])

            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            count = catalog.refresh_exported_symbols("aaa", shard_db)
            self.assertEqual(count, 1)

            results = catalog.search_exported_symbols("my_func")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["repo_id"], "aaa")

    def test_resolve_external_import_python(self):
        """resolve_external_import finds Python modules."""
        with tempfile.TemporaryDirectory() as tmp:
            shard_db = Database(Path(tmp) / "shard.db")
            shard_db.init_schema()
            from bombe.models import SymbolRecord
            shard_db.upsert_files([FileRecord(path="src/util.py", language="python", content_hash="bbb")])
            shard_db.replace_file_symbols("src/util.py", [
                SymbolRecord(
                    name="helper",
                    qualified_name="util.helper",
                    kind="function",
                    file_path="src/util.py",
                    start_line=1,
                    end_line=3,
                ),
            ])
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.refresh_exported_symbols("bbb", shard_db)

            matches = catalog.resolve_external_import("util", "python")
            self.assertTrue(len(matches) >= 1)
            self.assertEqual(matches[0]["repo_id"], "bbb")
