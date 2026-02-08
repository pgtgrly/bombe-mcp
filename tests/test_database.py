from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
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
            version = db.query(
                "SELECT value FROM repo_meta WHERE key = 'schema_version';"
            )
            self.assertEqual(version[0]["value"], "3")

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

    def test_init_schema_migrates_schema_version_and_rebuilds_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            db.upsert_files(
                [
                    FileRecord(
                        path="src/mod.py",
                        language="python",
                        content_hash="hash-1",
                        size_bytes=10,
                    )
                ]
            )
            db.replace_file_symbols(
                file_path="src/mod.py",
                symbols=[
                    SymbolRecord(
                        name="run",
                        qualified_name="src.mod.run",
                        kind="function",
                        file_path="src/mod.py",
                        start_line=1,
                        end_line=2,
                        signature="def run()",
                        docstring="execute",
                    )
                ],
            )
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO repo_meta(key, value)
                    VALUES('schema_version', '1')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                    """
                )
                try:
                    conn.execute("DELETE FROM symbol_fts;")
                    conn.execute(
                        """
                        INSERT INTO symbol_fts(symbol_id, name, qualified_name, docstring, signature)
                        VALUES (999, 'stale', 'stale', '', '');
                        """
                    )
                except sqlite3.OperationalError:
                    pass
                conn.commit()

            db.init_schema()
            version = db.query("SELECT value FROM repo_meta WHERE key = 'schema_version';")
            self.assertEqual(version[0]["value"], "3")
            fts_table = db.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'symbol_fts';"
            )
            if fts_table:
                rows = db.query("SELECT name, qualified_name FROM symbol_fts;")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["name"], "run")
                self.assertEqual(rows[0]["qualified_name"], "src.mod.run")

    def test_init_schema_migrates_from_v2_to_v3_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO repo_meta(key, value)
                    VALUES('schema_version', '2')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                    """
                )
                conn.execute("DROP INDEX IF EXISTS idx_edges_file_line;")
                conn.commit()

            db.init_schema()
            version = db.query("SELECT value FROM repo_meta WHERE key = 'schema_version';")
            self.assertEqual(version[0]["value"], "3")
            index_rows = db.query(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_edges_file_line';"
            )
            self.assertEqual(len(index_rows), 1)


if __name__ == "__main__":
    unittest.main()
