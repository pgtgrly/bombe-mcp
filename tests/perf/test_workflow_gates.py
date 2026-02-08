from __future__ import annotations

import os
import unittest

try:
    from tests.perf.perf_utils import record_metrics
    from tests.perf.workflow_harness import run_workflow_benchmark
except ModuleNotFoundError:
    from perf_utils import record_metrics
    from workflow_harness import run_workflow_benchmark


@unittest.skipUnless(os.getenv("BOMBE_RUN_PERF") == "1", "Perf tests are opt-in.")
class WorkflowGateTests(unittest.TestCase):
    def test_hard_workflow_gates_pass(self) -> None:
        metrics, violations = run_workflow_benchmark(iterations=20)
        history_path = record_metrics("workflow_gates", metrics)
        print(
            "[perf][workflow_gates] "
            f"workflow_a_flow_precision={metrics['workflow_a_flow_precision']:.3f} "
            f"workflow_a_latency_ms_p95={metrics['workflow_a_latency_ms_p95']:.2f} "
            f"workflow_b_direct_recall={metrics['workflow_b_direct_recall']:.3f} "
            f"workflow_b_transitive_precision={metrics['workflow_b_transitive_precision']:.3f} "
            f"workflow_b_latency_ms_p95={metrics['workflow_b_latency_ms_p95']:.2f} "
            f"workflow_c_top5_hit_rate={metrics['workflow_c_top5_hit_rate']:.3f} "
            f"workflow_c_latency_ms_p95={metrics['workflow_c_latency_ms_p95']:.2f} "
            f"workflow_d_seed_hit_rate={metrics['workflow_d_seed_hit_rate']:.3f} "
            f"workflow_d_connectedness={metrics['workflow_d_connectedness']:.3f} "
            f"workflow_d_latency_ms_p95={metrics['workflow_d_latency_ms_p95']:.2f} "
            f"history={history_path}"
        )
        self.assertEqual([], violations, f"Workflow gate violations: {violations}")


if __name__ == "__main__":
    unittest.main()
