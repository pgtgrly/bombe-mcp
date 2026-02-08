from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.release.gates import evaluate_release_gates, load_history


def _good_entry(suite: str, metrics: dict[str, float]) -> dict[str, object]:
    return {"timestamp_utc": "2026-02-08T00:00:00Z", "suite": suite, "metrics": metrics}


class ReleaseGateTests(unittest.TestCase):
    def test_evaluate_release_gates_passes_for_valid_metrics(self) -> None:
        entries = [
            _good_entry("index", {"full_index_ms_p95": 1200.0}),
            _good_entry("incremental", {"incremental_ms_p95": 90.0}),
            _good_entry(
                "query",
                {
                    "search_ms_p95": 5.0,
                    "references_ms_p95": 40.0,
                    "context_ms_p95": 220.0,
                },
            ),
            _good_entry(
                "workflow_gates",
                {
                    "workflow_a_flow_precision": 1.0,
                    "workflow_a_latency_ms_p95": 120.0,
                    "workflow_b_direct_recall": 1.0,
                    "workflow_b_transitive_precision": 1.0,
                    "workflow_b_latency_ms_p95": 180.0,
                    "workflow_c_top5_hit_rate": 1.0,
                    "workflow_c_latency_ms_p95": 240.0,
                    "workflow_d_seed_hit_rate": 1.0,
                    "workflow_d_connectedness": 1.0,
                    "workflow_d_latency_ms_p95": 260.0,
                },
            ),
        ]
        self.assertEqual([], evaluate_release_gates(entries))

    def test_evaluate_release_gates_reports_violations(self) -> None:
        entries = [
            _good_entry("index", {"full_index_ms_p95": 40000.0}),
            _good_entry("incremental", {"incremental_ms_p95": 1000.0}),
            _good_entry(
                "query",
                {
                    "search_ms_p95": 30.0,
                    "references_ms_p95": 140.0,
                    "context_ms_p95": 900.0,
                },
            ),
            _good_entry(
                "workflow_gates",
                {
                    "workflow_a_flow_precision": 0.3,
                    "workflow_a_latency_ms_p95": 2500.0,
                    "workflow_b_direct_recall": 0.2,
                    "workflow_b_transitive_precision": 0.2,
                    "workflow_b_latency_ms_p95": 3000.0,
                    "workflow_c_top5_hit_rate": 0.2,
                    "workflow_c_latency_ms_p95": 2000.0,
                    "workflow_d_seed_hit_rate": 0.1,
                    "workflow_d_connectedness": 0.1,
                    "workflow_d_latency_ms_p95": 3000.0,
                },
            ),
        ]
        violations = evaluate_release_gates(entries)
        self.assertGreater(len(violations), 0)
        self.assertIn("index:full_index_ms_p95:value=40000.0:max=30000.0", violations)

    def test_load_history_handles_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history = Path(tmpdir) / "history.jsonl"
            history.write_text(
                "\n".join(
                    [
                        '{"timestamp_utc":"2026-02-08T00:00:00Z","suite":"index","metrics":{"full_index_ms_p95":100.0}}',
                        '{"timestamp_utc":"2026-02-08T00:00:01Z","suite":"incremental","metrics":{"incremental_ms_p95":50.0}}',
                    ]
                ),
                encoding="utf-8",
            )
            entries = load_history(history)
            self.assertEqual(2, len(entries))
            self.assertEqual("index", entries[0]["suite"])


if __name__ == "__main__":
    unittest.main()
