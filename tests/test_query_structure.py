from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bombe.models import StructureRequest
from bombe.query.structure import get_structure
from bombe.store.database import Database


class QueryStructureTests(unittest.TestCase):
    def test_get_structure_includes_ranked_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO files(path, language, content_hash, size_bytes)
                    VALUES ('src/auth.py', 'python', 'h1', 1);
                    """
                )
                conn.execute(
                    """
                    INSERT INTO symbols(name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score)
                    VALUES
                      ('authenticate', 'app.authenticate', 'function', 'src/auth.py', 1, 2, 'def authenticate(user)', 0.9),
                      ('authorize', 'app.authorize', 'function', 'src/auth.py', 4, 5, 'def authorize(user)', 0.6);
                    """
                )
                conn.commit()

            output = get_structure(
                db,
                StructureRequest(path="src", token_budget=1000, include_signatures=True),
            )
            self.assertIn("src/auth.py", output)
            self.assertIn("def authenticate(user)", output)
            self.assertIn("[rank:1]", output)


if __name__ == "__main__":
    unittest.main()
