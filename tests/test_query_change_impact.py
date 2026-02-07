from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.query.change_impact import change_impact
from bombe.store.database import Database


class QueryChangeImpactTests(unittest.TestCase):
    def test_change_impact_includes_callers_and_type_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('iface.py', 'python', 'h1', 1), ('impl.py', 'python', 'h2', 1), ('api.py', 'python', 'h3', 1);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature)
                    VALUES
                      ('Base', 'pkg.Base', 'class', 'iface.py', 1, 2, 'class Base'),
                      ('Impl', 'pkg.Impl', 'class', 'impl.py', 1, 2, 'class Impl'),
                      ('api', 'pkg.api', 'function', 'api.py', 1, 2, 'def api()');
                    """
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES
                      (?, ?, 'symbol', 'symbol', 'CALLS', 20),
                      (?, ?, 'symbol', 'symbol', 'IMPLEMENTS', 1);
                    """,
                    (ids["pkg.api"], ids["pkg.Base"], ids["pkg.Impl"], ids["pkg.Base"]),
                )
                conn.commit()

            payload = change_impact(db, symbol_name="pkg.Base", change_type="behavior", max_depth=2)
            impact = payload["impact"]
            self.assertEqual(payload["target"]["name"], "Base")
            self.assertEqual(len(impact["direct_callers"]), 1)
            self.assertEqual(impact["direct_callers"][0]["name"], "api")
            self.assertEqual(len(impact["type_dependents"]), 1)
            self.assertEqual(impact["type_dependents"][0]["name"], "Impl")
            self.assertIn(impact["risk_level"], {"low", "medium", "high"})


if __name__ == "__main__":
    unittest.main()
