from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.models import BlastRadiusRequest
from bombe.query.blast import get_blast_radius
from bombe.store.database import Database


class QueryBlastTests(unittest.TestCase):
    def test_get_blast_radius_returns_direct_and_transitive_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('a.py', 'python', 'h1', 1), ('b.py', 'python', 'h2', 1), ('c.py', 'python', 'h3', 1);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line)
                    VALUES
                      ('target', 'pkg.target', 'function', 'a.py', 1, 2),
                      ('direct', 'pkg.direct', 'function', 'b.py', 1, 2),
                      ('transitive', 'pkg.transitive', 'function', 'c.py', 1, 2);
                    """
                )
                rows = conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
                ids = {row["qualified_name"]: int(row["id"]) for row in rows}
                conn.execute(
                    """
                    INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                    VALUES
                      (?, ?, 'symbol', 'symbol', 'CALLS', 10),
                      (?, ?, 'symbol', 'symbol', 'CALLS', 20);
                    """,
                    (
                        ids["pkg.direct"],
                        ids["pkg.target"],
                        ids["pkg.transitive"],
                        ids["pkg.direct"],
                    ),
                )
                conn.commit()

            response = get_blast_radius(
                db,
                BlastRadiusRequest(symbol_name="pkg.target", change_type="signature", max_depth=3),
            )
            impact = response.payload["impact"]
            self.assertEqual(len(impact["direct_callers"]), 1)
            self.assertEqual(len(impact["transitive_callers"]), 1)
            self.assertIn("b.py", impact["affected_files"])
            self.assertIn("c.py", impact["affected_files"])


if __name__ == "__main__":
    unittest.main()
