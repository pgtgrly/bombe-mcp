from __future__ import annotations

import unittest

try:
    from tests.perf.real_repo_harness import get_real_repo_paths, run_real_repo_eval
except ModuleNotFoundError:
    from real_repo_harness import get_real_repo_paths, run_real_repo_eval


class RealRepoEvalTests(unittest.TestCase):
    def test_real_repo_eval_gates(self) -> None:
        if not get_real_repo_paths():
            self.skipTest("BOMBE_REAL_REPO_PATHS is not configured")

        results, violations = run_real_repo_eval(max_repos=2)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual([], violations, f"real repo violations: {violations}")


if __name__ == "__main__":
    unittest.main()
