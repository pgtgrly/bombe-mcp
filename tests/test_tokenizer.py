from __future__ import annotations

import unittest

from bombe.query.tokenizer import estimate_tokens


class TokenizerTests(unittest.TestCase):
    def test_estimate_tokens_handles_empty_and_non_empty_text(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreaterEqual(estimate_tokens("def run():\n    return 1\n"), 1)

    def test_estimate_tokens_is_monotonic_for_longer_text(self) -> None:
        short = "authenticate"
        long = short * 50
        self.assertLessEqual(estimate_tokens(short), estimate_tokens(long))


if __name__ == "__main__":
    unittest.main()
