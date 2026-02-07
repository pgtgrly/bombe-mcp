from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.models import FileRecord, ParameterRecord, SymbolRecord
from bombe.store.database import Database


class DatabaseTests(unittest.TestCase):
    def test_init_schema_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "bombe.db"
            db = Database(db_path)
            db.init_schema()
            db.init_schema()

            tables = db.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;"
            )
            table_names = [row["name"] for row in tables]
            self.assertIn("files", table_names)
            self.assertIn("symbols", table_names)
            self.assertIn("edges", table_names)
            self.assertIn("external_deps", table_names)

    def test_replace_file_symbols_persists_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            db.upsert_files(
                [
                    FileRecord(
                        path="src/main.py",
                        language="python",
                        content_hash="hash-1",
                        size_bytes=10,
                    )
                ]
            )
            db.replace_file_symbols(
                file_path="src/main.py",
                symbols=[
                    SymbolRecord(
                        name="run",
                        qualified_name="src.main.run",
                        kind="function",
                        file_path="src/main.py",
                        start_line=1,
                        end_line=3,
                        parameters=[
                            ParameterRecord(name="name", type="str", position=0),
                        ],
                    )
                ],
            )

            rows = db.query("SELECT name, type, position FROM parameters;")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "name")
            self.assertEqual(rows[0]["type"], "str")
            self.assertEqual(rows[0]["position"], 0)

    def test_rename_file_updates_related_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            db.upsert_files(
                [
                    FileRecord(
                        path="src/old.py",
                        language="python",
                        content_hash="hash-1",
                        size_bytes=10,
                    )
                ]
            )
            db.replace_file_symbols(
                file_path="src/old.py",
                symbols=[
                    SymbolRecord(
                        name="run",
                        qualified_name="src.old.run",
                        kind="function",
                        file_path="src/old.py",
                        start_line=1,
                        end_line=2,
                    )
                ],
            )
            db.rename_file("src/old.py", "src/new.py")

            files = db.query("SELECT path FROM files;")
            symbols = db.query("SELECT file_path FROM symbols;")
            self.assertEqual([row["path"] for row in files], ["src/new.py"])
            self.assertEqual([row["file_path"] for row in symbols], ["src/new.py"])


if __name__ == "__main__":
    unittest.main()
