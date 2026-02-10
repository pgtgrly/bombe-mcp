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
            self.assertEqual(version[0]["value"], "7")

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

    def test_replace_file_symbols_dedupes_duplicate_qualified_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            db.upsert_files(
                [
                    FileRecord(
                        path="src/dup.java",
                        language="java",
                        content_hash="hash-dup",
                        size_bytes=20,
                    )
                ]
            )
            db.replace_file_symbols(
                file_path="src/dup.java",
                symbols=[
                    SymbolRecord(
                        name="run",
                        qualified_name="pkg.Service.run",
                        kind="method",
                        file_path="src/dup.java",
                        start_line=10,
                        end_line=11,
                        signature="void run()",
                    ),
                    SymbolRecord(
                        name="run",
                        qualified_name="pkg.Service.run",
                        kind="method",
                        file_path="src/dup.java",
                        start_line=20,
                        end_line=21,
                        signature="void run(int retries)",
                    ),
                ],
            )

            rows = db.query(
                """
                SELECT qualified_name
                FROM symbols
                WHERE file_path = 'src/dup.java';
                """
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(str(rows[0]["qualified_name"]), "pkg.Service.run")

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
            self.assertEqual(version[0]["value"], "7")
            fts_table = db.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'symbol_fts';"
            )
            if fts_table:
                rows = db.query("SELECT name, qualified_name FROM symbol_fts;")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["name"], "run")
                self.assertEqual(rows[0]["qualified_name"], "src.mod.run")

    def test_init_schema_migrates_from_v4_to_v5_state_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO repo_meta(key, value)
                    VALUES('schema_version', '4')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                    """
                )
                conn.execute("DROP TABLE IF EXISTS trusted_signing_keys;")
                conn.commit()

            db.init_schema()
            version = db.query("SELECT value FROM repo_meta WHERE key = 'schema_version';")
            self.assertEqual(version[0]["value"], "7")
            table_rows = db.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'trusted_signing_keys';"
            )
            self.assertEqual(len(table_rows), 1)
            migration_rows = db.query(
                """
                SELECT from_version, to_version, status
                FROM migration_history
                WHERE to_version = 5
                ORDER BY id DESC
                LIMIT 1;
                """
            )
            self.assertEqual(len(migration_rows), 1)
            self.assertEqual(int(migration_rows[0]["from_version"]), 4)
            self.assertEqual(str(migration_rows[0]["status"]), "success")

    def test_init_schema_migrates_from_v5_to_v6_diagnostics_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO repo_meta(key, value)
                    VALUES('schema_version', '5')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                    """
                )
                conn.execute("DROP TABLE IF EXISTS indexing_diagnostics;")
                conn.commit()

            db.init_schema()
            version = db.query("SELECT value FROM repo_meta WHERE key = 'schema_version';")
            self.assertEqual(version[0]["value"], "7")
            table_rows = db.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'indexing_diagnostics';"
            )
            self.assertEqual(len(table_rows), 1)
            migration_rows = db.query(
                """
                SELECT from_version, to_version, status
                FROM migration_history
                WHERE to_version = 6
                ORDER BY id DESC
                LIMIT 1;
                """
            )
            self.assertEqual(len(migration_rows), 1)
            self.assertEqual(int(migration_rows[0]["from_version"]), 5)
            self.assertEqual(str(migration_rows[0]["status"]), "success")

    def test_backup_and_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db = Database(tmp / "bombe.db")
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
            backup = db.backup_to(tmp / "backup" / "bombe-backup.db")

            db.delete_file_graph("src/main.py")
            rows_after_delete = db.query("SELECT COUNT(*) AS count FROM files;")
            self.assertEqual(int(rows_after_delete[0]["count"]), 0)

            db.restore_from(backup)
            rows_after_restore = db.query("SELECT COUNT(*) AS count FROM files;")
            self.assertEqual(int(rows_after_restore[0]["count"]), 1)

    def test_sync_state_and_metrics_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()
            queue_id = db.enqueue_sync_delta("repo", "snap_1", '{"delta":"ok"}')
            pending = db.list_pending_sync_deltas("repo", limit=5)
            self.assertEqual(len(pending), 1)
            self.assertEqual(int(pending[0]["id"]), queue_id)

            db.mark_sync_delta_status(queue_id, status="pushed", last_error=None)
            pushed_rows = db.query(
                "SELECT status, attempt_count FROM sync_queue WHERE id = ?;",
                (queue_id,),
            )
            self.assertEqual(str(pushed_rows[0]["status"]), "pushed")
            self.assertEqual(int(pushed_rows[0]["attempt_count"]), 1)

            db.set_artifact_pin("repo", "snap_1", "artifact_1")
            self.assertEqual(db.get_artifact_pin("repo", "snap_1"), "artifact_1")

            db.quarantine_artifact("artifact_bad", "checksum_mismatch")
            self.assertTrue(db.is_artifact_quarantined("artifact_bad"))

            db.set_circuit_breaker_state("repo", state="open", failure_count=3, opened_at_utc="2026-01-01T00:00:00Z")
            breaker = db.get_circuit_breaker_state("repo")
            self.assertIsNotNone(breaker)
            self.assertEqual(breaker["state"], "open")
            self.assertEqual(int(breaker["failure_count"]), 3)

            db.record_sync_event("repo", "INFO", "sync_cycle", {"mode": "hybrid"})
            event_rows = db.query("SELECT event_type FROM sync_events ORDER BY id DESC LIMIT 1;")
            self.assertEqual(str(event_rows[0]["event_type"]), "sync_cycle")

            db.record_tool_metric(
                tool_name="search_symbols",
                latency_ms=12.5,
                success=True,
                mode="local",
                repo_id="repo",
                result_size=3,
                error_message=None,
            )
            metrics = db.recent_tool_metrics("search_symbols", limit=1)
            self.assertEqual(len(metrics), 1)
            self.assertEqual(str(metrics[0]["tool_name"]), "search_symbols")
            self.assertEqual(int(metrics[0]["result_size"]), 3)

            cache_epoch = db.get_cache_epoch()
            self.assertGreaterEqual(cache_epoch, 1)
            bumped_epoch = db.bump_cache_epoch()
            self.assertGreater(bumped_epoch, cache_epoch)

            db.set_trusted_signing_key(
                repo_id="repo",
                key_id="main",
                algorithm="hmac-sha256",
                public_key="secret-key",
                purpose="default",
                active=True,
            )
            key = db.get_trusted_signing_key("repo", "main")
            self.assertIsNotNone(key)
            self.assertEqual(key["algorithm"], "hmac-sha256")
            keys = db.list_trusted_signing_keys("repo", active_only=True)
            self.assertEqual(len(keys), 1)

            with closing(db.connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO sync_queue(repo_id, local_snapshot, payload_json, status)
                    VALUES ('repo', 'snap_2', '{}', 'bogus');
                    """
                )
                conn.commit()
            fixed = db.normalize_sync_queue_statuses()
            self.assertGreaterEqual(fixed, 1)

    def test_indexing_diagnostics_persistence_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "bombe.db")
            db.init_schema()

            db.record_indexing_diagnostic(
                run_id="run_a",
                stage="parse",
                category="syntax_error",
                message="line 10",
                hint="Fix invalid syntax and retry indexing.",
                file_path="src/a.py",
                language="python",
                severity="error",
            )
            db.record_indexing_diagnostic(
                run_id="run_a",
                stage="extract",
                category="extractor_failure",
                message="unexpected ast shape",
                hint="Check extractor support for this construct.",
                file_path="src/a.py",
                language="python",
                severity="warning",
            )
            db.record_indexing_diagnostic(
                run_id="run_b",
                stage="parse",
                category="parser_unavailable",
                message="missing backend",
                hint="Install compatible tree-sitter parser runtime.",
                file_path="src/b.go",
                language="go",
                severity="error",
            )

            latest = db.list_indexing_diagnostics(limit=2)
            self.assertEqual(len(latest), 2)
            self.assertEqual(str(latest[0]["run_id"]), "run_b")
            paged = db.list_indexing_diagnostics(limit=1, offset=1)
            self.assertEqual(len(paged), 1)
            self.assertEqual(str(paged[0]["run_id"]), "run_a")

            parse_only = db.list_indexing_diagnostics(limit=10, run_id="run_a", stage="parse")
            self.assertEqual(len(parse_only), 1)
            self.assertEqual(str(parse_only[0]["category"]), "syntax_error")

            error_only = db.list_indexing_diagnostics(limit=10, severity="error")
            self.assertEqual(len(error_only), 2)

            all_summary = db.summarize_indexing_diagnostics()
            self.assertEqual(int(all_summary["total"]), 3)
            self.assertEqual(int(all_summary["by_stage"]["parse"]), 2)
            self.assertEqual(int(all_summary["by_category"]["syntax_error"]), 1)
            self.assertEqual(int(all_summary["by_severity"]["error"]), 2)
            self.assertEqual(str(all_summary["latest_run_id"]), "run_b")

            run_summary = db.summarize_indexing_diagnostics(run_id="run_a")
            self.assertEqual(int(run_summary["total"]), 2)
            self.assertEqual(int(run_summary["by_stage"]["extract"]), 1)
            self.assertEqual(int(run_summary["by_severity"]["warning"]), 1)

            deleted_one = db.clear_indexing_diagnostics(run_id="run_a")
            self.assertEqual(deleted_one, 2)
            remaining = db.list_indexing_diagnostics(limit=10)
            self.assertEqual(len(remaining), 1)

            deleted_rest = db.clear_indexing_diagnostics()
            self.assertEqual(deleted_rest, 1)
            self.assertEqual(db.list_indexing_diagnostics(limit=10), [])


if __name__ == "__main__":
    unittest.main()
