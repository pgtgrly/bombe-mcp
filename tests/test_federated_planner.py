"""Tests for FederatedQueryPlanner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import FileRecord, ShardInfo, SymbolRecord
from bombe.store.database import Database
from bombe.store.sharding.catalog import ShardCatalog
from bombe.store.sharding.router import ShardRouter
from bombe.query.federated.planner import FederatedQueryPlanner


class TestFederatedQueryPlanner(unittest.TestCase):

    def _setup(self, tmp: str) -> tuple[ShardCatalog, ShardRouter, FederatedQueryPlanner]:
        catalog = ShardCatalog(Path(tmp) / "catalog.db")
        catalog.init_schema()
        # Register two shards
        for repo_id, name in [("aaa", "a"), ("bbb", "b")]:
            db_path = Path(tmp) / name / ".bombe" / "bombe.db"
            db_path.parent.mkdir(parents=True)
            db = Database(db_path)
            db.init_schema()
            db.upsert_files([FileRecord(path=f"src/{name}.py", language="python", content_hash=name)])
            db.replace_file_symbols(f"src/{name}.py", [
                SymbolRecord(name=f"func_{name}", qualified_name=f"{name}.func_{name}",
                             kind="function", file_path=f"src/{name}.py",
                             start_line=1, end_line=5),
            ])
            catalog.register_shard(ShardInfo(
                repo_id=repo_id, repo_path=str(Path(tmp) / name),
                db_path=str(db_path),
            ))
            catalog.refresh_exported_symbols(repo_id, db)

        router = ShardRouter(catalog)
        planner = FederatedQueryPlanner(catalog, router)
        return catalog, router, planner

    def test_plan_search_fans_out_to_all(self):
        """plan_search includes all shards."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, planner = self._setup(tmp)
            plan = planner.plan_search("func")
            self.assertEqual(len(plan.shard_ids), 2)
            self.assertEqual(plan.fan_out_strategy, "all")

    def test_plan_references_routes_to_relevant_shards(self):
        """plan_references routes based on symbol location."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, planner = self._setup(tmp)
            plan = planner.plan_references("func_a", "both", 1)
            self.assertIn("aaa", plan.shard_ids)
            self.assertEqual(plan.fan_out_strategy, "routed")

    def test_plan_blast_radius(self):
        """plan_blast_radius returns a plan."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, planner = self._setup(tmp)
            plan = planner.plan_blast_radius("func_a", 3)
            self.assertTrue(len(plan.shard_ids) >= 1)

    def test_plan_context_with_entry_points(self):
        """plan_context routes based on entry points."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, planner = self._setup(tmp)
            plan = planner.plan_context("test", ["func_a"])
            self.assertIn("aaa", plan.shard_ids)

    def test_plan_context_fallback_all_shards(self):
        """plan_context falls back to all shards if no entry points found."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, planner = self._setup(tmp)
            plan = planner.plan_context("test", [])
            self.assertEqual(len(plan.shard_ids), 2)
