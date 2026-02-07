from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.models import ReferenceRequest
from bombe.query.references import get_references
from bombe.store.database import Database


class QueryReferencesTests(unittest.TestCase):
    def test_get_references_traverses_callers_and_callees(self) -> None:
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
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature)
                    VALUES
                      ('a', 'pkg.a', 'function', 'a.py', 1, 2, 'def a()'),
                      ('b', 'pkg.b', 'function', 'b.py', 1, 2, 'def b()'),
                      ('c', 'pkg.c', 'function', 'c.py', 1, 2, 'def c()');
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
                    (ids["pkg.a"], ids["pkg.b"], ids["pkg.b"], ids["pkg.c"]),
                )
                conn.commit()

            response = get_references(
                db,
                ReferenceRequest(symbol_name="pkg.b", direction="both", depth=2),
            )
            callers = response.payload["callers"]
            callees = response.payload["callees"]
            self.assertEqual(len(callers), 1)
            self.assertEqual(callers[0]["name"], "a")
            self.assertEqual(len(callees), 1)
            self.assertEqual(callees[0]["name"], "c")


if __name__ == "__main__":
    unittest.main()
