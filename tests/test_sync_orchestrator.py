from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.models import FileChange
from bombe.store.database import Database
from bombe.sync.orchestrator import run_sync_cycle
from bombe.sync.transport import FileControlPlaneTransport


class SyncOrchestratorTests(unittest.TestCase):
    def test_run_sync_cycle_persists_queue_pins_and_breaker_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text(
                "def helper():\n    return 1\n\n"
                "def run():\n    return helper()\n",
                encoding="utf-8",
            )
            db = Database(root / ".bombe" / "bombe.db")
            db.init_schema()
            full_index(root, db)

            report = run_sync_cycle(
                repo_root=root,
                db=db,
                transport=FileControlPlaneTransport(root / ".bombe" / "control-plane"),
                changes=[FileChange(status="M", path="src/main.py")],
                timeout_seconds=0.25,
            )

            self.assertTrue(bool(report.queue_id))
            self.assertEqual(report.push["reason"], "pushed")
            self.assertEqual(report.pull["reason"], "pulled")
            self.assertTrue(bool(report.pinned_artifact_id))

            queue_rows = db.query("SELECT status FROM sync_queue ORDER BY id DESC LIMIT 1;")
            self.assertEqual(queue_rows[0]["status"], "pushed")

            pin_rows = db.query("SELECT artifact_id FROM artifact_pins ORDER BY pinned_at DESC LIMIT 1;")
            self.assertEqual(pin_rows[0]["artifact_id"], report.pinned_artifact_id)

            breaker = db.get_circuit_breaker_state(report.repo_id)
            self.assertIsNotNone(breaker)
            self.assertEqual(breaker["state"], "closed")
            self.assertEqual(int(breaker["failure_count"]), 0)

            event_rows = db.query("SELECT event_type FROM sync_events ORDER BY id;")
            self.assertGreaterEqual(len(event_rows), 2)
            self.assertEqual(event_rows[0]["event_type"], "sync_push")


if __name__ == "__main__":
    unittest.main()
