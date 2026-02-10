"""Tests for cross-repo import resolver."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import ShardInfo, SymbolRecord, FileRecord, ExternalDepRecord
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.cross_repo_resolver import (
    compute_repo_id,
    post_index_cross_repo_sync,
    resolve_cross_repo_imports,
)


class TestComputeRepoId(unittest.TestCase):

    def test_deterministic(self):
        """Same path always produces same repo_id."""
        p = Path("/tmp/test-repo")
        id1 = compute_repo_id(p)
        id2 = compute_repo_id(p)
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 16)

    def test_different_paths_different_ids(self):
        """Different paths produce different repo_ids."""
        id1 = compute_repo_id(Path("/repo/a"))
        id2 = compute_repo_id(Path("/repo/b"))
        self.assertNotEqual(id1, id2)


class TestResolveCrossRepoImports(unittest.TestCase):

    def test_resolve_python_import(self):
        """Resolve a Python external dep against catalog."""
        with tempfile.TemporaryDirectory() as tmp:
            # Set up shard A (has external dep)
            db_a = Database(Path(tmp) / "a.db")
            db_a.init_schema()
            db_a.upsert_files([FileRecord(path="src/app.py", language="python", content_hash="h1")])
            db_a.replace_file_symbols("src/app.py", [
                SymbolRecord(name="main", qualified_name="app.main", kind="function",
                             file_path="src/app.py", start_line=1, end_line=5),
            ])
            db_a.replace_external_deps("src/app.py", [
                ExternalDepRecord(file_path="src/app.py", import_statement="import util",
                                  module_name="util", line_number=1),
            ])

            # Set up shard B (exports util.helper)
            db_b = Database(Path(tmp) / "b.db")
            db_b.init_schema()
            db_b.upsert_files([FileRecord(path="src/util.py", language="python", content_hash="h2")])
            db_b.replace_file_symbols("src/util.py", [
                SymbolRecord(name="helper", qualified_name="util.helper", kind="function",
                             file_path="src/util.py", start_line=1, end_line=3),
            ])

            # Set up catalog
            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.register_shard(ShardInfo(repo_id="aaa", repo_path="/a", db_path=str(Path(tmp) / "a.db")))
            catalog.register_shard(ShardInfo(repo_id="bbb", repo_path="/b", db_path=str(Path(tmp) / "b.db")))
            catalog.refresh_exported_symbols("bbb", db_b)

            # Resolve
            edges = resolve_cross_repo_imports(catalog, "aaa", db_a)
            self.assertTrue(len(edges) >= 1)
            self.assertEqual(edges[0].source_uri.repo_id, "aaa")
            self.assertEqual(edges[0].target_uri.repo_id, "bbb")
            self.assertEqual(edges[0].relationship, "IMPORTS")

    def test_no_self_edges(self):
        """Cross-repo resolution skips same-repo matches."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "a.db")
            db.init_schema()
            db.upsert_files([FileRecord(path="src/app.py", language="python", content_hash="h1")])
            db.replace_file_symbols("src/app.py", [
                SymbolRecord(name="main", qualified_name="app.main", kind="function",
                             file_path="src/app.py", start_line=1, end_line=5),
            ])
            db.replace_external_deps("src/app.py", [
                ExternalDepRecord(file_path="src/app.py", import_statement="import app",
                                  module_name="app", line_number=1),
            ])

            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            catalog.register_shard(ShardInfo(repo_id="aaa", repo_path="/a", db_path=str(Path(tmp) / "a.db")))
            catalog.refresh_exported_symbols("aaa", db)

            edges = resolve_cross_repo_imports(catalog, "aaa", db)
            self.assertEqual(len(edges), 0)  # no self-edges


class TestPostIndexCrossRepoSync(unittest.TestCase):

    def test_sync_stores_repo_id_and_refreshes(self):
        """post_index_cross_repo_sync sets repo_id and refreshes exports."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "myrepo"
            repo_root.mkdir()
            db = Database(Path(tmp) / "shard.db")
            db.init_schema()
            db.upsert_files([FileRecord(path="src/foo.py", language="python", content_hash="abc")])
            db.replace_file_symbols("src/foo.py", [
                SymbolRecord(name="foo", qualified_name="foo.foo", kind="function",
                             file_path="src/foo.py", start_line=1, end_line=3),
            ])

            catalog = ShardCatalog(Path(tmp) / "catalog.db")
            catalog.init_schema()
            repo_id = compute_repo_id(repo_root)
            catalog.register_shard(ShardInfo(
                repo_id=repo_id, repo_path=str(repo_root),
                db_path=str(Path(tmp) / "shard.db"),
            ))

            result = post_index_cross_repo_sync(repo_root, db, catalog)
            self.assertEqual(result["repo_id"], repo_id)
            self.assertGreater(result["exported_symbols"], 0)
            self.assertEqual(result["symbol_count"], 1)
            # Check repo_id stored in meta
            meta = db.get_repo_meta("repo_id")
            self.assertEqual(meta, repo_id)
