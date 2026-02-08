from __future__ import annotations

import unittest

try:
    from tests.perf.gold_query_harness import evaluate_gold_queries
except ModuleNotFoundError:
    from gold_query_harness import evaluate_gold_queries


class GoldEvalTests(unittest.TestCase):
    def test_gold_queries_top5_hit_rate(self) -> None:
        hit_rate, violations = evaluate_gold_queries(min_top5_hit_rate=0.95)
        self.assertEqual([], violations, f"gold eval violations: {violations}")
        self.assertGreaterEqual(hit_rate, 0.95)


if __name__ == "__main__":
    unittest.main()
