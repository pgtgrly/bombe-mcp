from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.store.database import Database
from bombe.ui_api import build_inspector_bundle, build_symbol_explanation


class UiApiTests(unittest.TestCase):
    def test_build_inspector_bundle_shapes_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/main.py', 'python', 'h1', 1);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES ('run', 'app.main.run', 'function', 'src/main.py', 1, 2, 0.5);
                    """
                )
                conn.commit()
            bundle = build_inspector_bundle(db, node_limit=20, edge_limit=20, diagnostics_limit=10)
            self.assertIn("nodes", bundle)
            self.assertIn("edges", bundle)
            self.assertIn("diagnostics_summary", bundle)
            self.assertIn("diagnostics", bundle)
            self.assertIn("hot_paths", bundle)
            self.assertIn("explainer", bundle)
            self.assertIn("limits", bundle)
            self.assertGreaterEqual(len(bundle["nodes"]), 1)

    def test_bundle_limits_metadata_present(self) -> None:
        """Verify limits metadata reports totals for large-graph safeguards."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/app.py', 'python', 'h2', 100);
                    """
                )
                for i in range(5):
                    conn.execute(
                        """
                        INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                        VALUES (?, ?, 'function', 'src/app.py', ?, ?, ?);
                        """,
                        (f"fn{i}", f"app.fn{i}", i * 10 + 1, i * 10 + 5, 0.1 * (5 - i)),
                    )
                conn.commit()

            bundle = build_inspector_bundle(db, node_limit=3, edge_limit=10, diagnostics_limit=5)
            limits = bundle["limits"]
            self.assertEqual(limits["node_limit"], 3)
            self.assertEqual(limits["nodes_total"], 5)
            # Only 3 nodes returned despite 5 existing
            self.assertEqual(len(bundle["nodes"]), 3)

    def test_explainer_included_in_bundle(self) -> None:
        """Verify explainer index is populated in the bundle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/core.py', 'python', 'h3', 50);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES ('main', 'core.main', 'function', 'src/core.py', 1, 10, 0.9);
                    """
                )
                conn.commit()

            bundle = build_inspector_bundle(db, node_limit=10, edge_limit=10, diagnostics_limit=5)
            explainer = bundle["explainer"]
            self.assertIsInstance(explainer, dict)
            self.assertGreaterEqual(len(explainer), 1)

            # Check explainer entry shape
            first_key = next(iter(explainer))
            entry = explainer[first_key]
            self.assertIn("rank", entry)
            self.assertIn("score", entry)
            self.assertIn("inbound", entry)
            self.assertIn("outbound", entry)
            self.assertIn("reasons", entry)
            self.assertIsInstance(entry["reasons"], list)
            self.assertGreater(len(entry["reasons"]), 0)


class SymbolExplainerTests(unittest.TestCase):
    def _make_db(self, tmpdir: str) -> Database:
        db = Database(Path(tmpdir) / "bombe.db")
        db.init_schema()
        return db

    def test_explain_missing_symbol_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._make_db(tmpdir)
            result = build_symbol_explanation(db, 9999)
            self.assertIn("error", result)

    def test_explain_single_orphan_symbol(self) -> None:
        """A symbol with no edges should report as orphan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._make_db(tmpdir)
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/orphan.py', 'python', 'h4', 20);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES (1, 'lonely', 'orphan.lonely', 'function', 'src/orphan.py', 1, 3, 0.01);
                    """
                )
                conn.commit()

            result = build_symbol_explanation(db, 1)
            self.assertNotIn("error", result)
            self.assertEqual(result["inbound_count"], 0)
            self.assertEqual(result["outbound_count"], 0)
            self.assertEqual(result["rank_position"], 1)
            self.assertTrue(any("orphan" in r.lower() for r in result["reasons"]))

    def test_explain_heavily_referenced_symbol(self) -> None:
        """A symbol with many inbound edges should report as heavily referenced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._make_db(tmpdir)
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/hub.py', 'python', 'h5', 100);
                    """
                )
                # Target symbol
                conn.execute(
                    """
                    INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES (100, 'hub', 'hub.hub', 'function', 'src/hub.py', 1, 5, 0.95);
                    """
                )
                # Create 12 caller symbols + edges
                for i in range(12):
                    conn.execute(
                        """
                        INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                        VALUES (?, ?, ?, 'function', 'src/hub.py', ?, ?, ?);
                        """,
                        (200 + i, f"caller{i}", f"hub.caller{i}", 10 + i, 12 + i, 0.01),
                    )
                    conn.execute(
                        """
                        INSERT INTO edges(source_id, target_id, source_type, target_type, relationship)
                        VALUES (?, 100, 'symbol', 'symbol', 'calls');
                        """,
                        (200 + i,),
                    )
                conn.commit()

            result = build_symbol_explanation(db, 100)
            self.assertNotIn("error", result)
            self.assertGreaterEqual(result["inbound_count"], 10)
            self.assertTrue(any("heavily" in r.lower() for r in result["reasons"]))
            self.assertEqual(result["rank_position"], 1)  # highest pagerank

    def test_explain_returns_correct_rank_position(self) -> None:
        """Rank position should correctly reflect ordering by PageRank."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._make_db(tmpdir)
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/rank.py', 'python', 'h6', 50);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES (1, 'top', 'rank.top', 'function', 'src/rank.py', 1, 2, 0.9);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES (2, 'mid', 'rank.mid', 'function', 'src/rank.py', 3, 4, 0.5);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES (3, 'low', 'rank.low', 'function', 'src/rank.py', 5, 6, 0.1);
                    """
                )
                conn.commit()

            top = build_symbol_explanation(db, 1)
            mid = build_symbol_explanation(db, 2)
            low = build_symbol_explanation(db, 3)

            self.assertEqual(top["rank_position"], 1)
            self.assertEqual(mid["rank_position"], 2)
            self.assertEqual(low["rank_position"], 3)
            self.assertEqual(top["total_symbols"], 3)

    def test_snapshot_explainer_payload_keys(self) -> None:
        """Snapshot test: verify the exact set of keys in an explanation payload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._make_db(tmpdir)
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/snap.py', 'python', 'h7', 10);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(id, name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                    VALUES (1, 'snap', 'snap.snap', 'function', 'src/snap.py', 1, 2, 0.5);
                    """
                )
                conn.commit()

            result = build_symbol_explanation(db, 1)
            expected_keys = {
                "symbol", "rank_position", "total_symbols", "pagerank_score",
                "inbound_count", "outbound_count", "inbound", "outbound", "reasons",
            }
            self.assertEqual(set(result.keys()), expected_keys)


class LoadTests(unittest.TestCase):
    """Performance safeguard tests for large graph scenarios."""

    def test_large_graph_bundle_respects_limits(self) -> None:
        """Even with many symbols, the bundle should never exceed configured limits."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/big.py', 'python', 'hbig', 5000);
                    """
                )
                for i in range(200):
                    conn.execute(
                        """
                        INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                        VALUES (?, ?, 'function', 'src/big.py', ?, ?, ?);
                        """,
                        (f"fn{i}", f"big.fn{i}", i * 2 + 1, i * 2 + 2, round(1.0 / (i + 1), 6)),
                    )
                # Add edges between consecutive symbols
                for i in range(199):
                    conn.execute(
                        """
                        INSERT INTO edges(source_id, target_id, source_type, target_type, relationship)
                        VALUES (?, ?, 'symbol', 'symbol', 'calls');
                        """,
                        (i + 1, i + 2),
                    )
                conn.commit()

            node_limit = 30
            edge_limit = 50
            bundle = build_inspector_bundle(db, node_limit=node_limit, edge_limit=edge_limit, diagnostics_limit=10)

            self.assertLessEqual(len(bundle["nodes"]), node_limit)
            self.assertLessEqual(len(bundle["edges"]), edge_limit)
            self.assertEqual(bundle["limits"]["nodes_total"], 200)
            self.assertEqual(bundle["limits"]["edges_total"], 199)
            self.assertLessEqual(len(bundle["explainer"]), node_limit)

    def test_explainer_does_not_exceed_limit(self) -> None:
        """Explainer index should not generate more entries than the limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/perf.py', 'python', 'hperf', 1000);
                    """
                )
                for i in range(100):
                    conn.execute(
                        """
                        INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, pagerank_score)
                        VALUES (?, ?, 'function', 'src/perf.py', ?, ?, ?);
                        """,
                        (f"perf{i}", f"perf.perf{i}", i + 1, i + 2, round(1.0 / (i + 1), 6)),
                    )
                conn.commit()

            # node_limit=100, but explainer limit should cap at min(node_limit, 50) = 50
            bundle = build_inspector_bundle(db, node_limit=100, edge_limit=100, diagnostics_limit=5)
            self.assertLessEqual(len(bundle["explainer"]), 50)


if __name__ == "__main__":
    unittest.main()
