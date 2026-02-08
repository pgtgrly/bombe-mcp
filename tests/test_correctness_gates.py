from __future__ import annotations

import unittest

try:
    from tests.perf.workflow_harness import WorkflowThresholds, run_workflow_benchmark
except ModuleNotFoundError:
    from perf.workflow_harness import WorkflowThresholds, run_workflow_benchmark


class CorrectnessGateTests(unittest.TestCase):
    def test_workflow_quality_gates_hold_on_reference_corpus(self) -> None:
        metrics, violations = run_workflow_benchmark(
            iterations=3,
            thresholds=WorkflowThresholds(
                flow_precision_min=0.9,
                impact_direct_recall_min=0.9,
                impact_transitive_precision_min=0.8,
                traversal_top5_hit_rate_min=0.9,
                context_seed_hit_rate_min=0.9,
                context_connectedness_min=0.75,
            ),
        )
        self.assertEqual([], violations, f"workflow correctness violations: {violations}")
        self.assertGreaterEqual(metrics["workflow_a_flow_precision"], 0.9)
        self.assertGreaterEqual(metrics["workflow_b_direct_recall"], 0.9)
        self.assertGreaterEqual(metrics["workflow_c_top5_hit_rate"], 0.9)
        self.assertGreaterEqual(metrics["workflow_d_seed_hit_rate"], 0.9)


if __name__ == "__main__":
    unittest.main()
