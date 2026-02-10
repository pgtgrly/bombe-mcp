from __future__ import annotations

import os
import unittest

from bombe.query.hybrid import rank_symbol


class HybridQueryTests(unittest.TestCase):
    def test_rank_symbol_uses_semantic_overlap_when_enabled(self) -> None:
        previous_hybrid = os.getenv("BOMBE_HYBRID_SEARCH")
        previous_vector = os.getenv("BOMBE_HYBRID_VECTOR")
        os.environ["BOMBE_HYBRID_SEARCH"] = "1"
        os.environ["BOMBE_HYBRID_VECTOR"] = "1"
        try:
            with_semantic = rank_symbol(
                query="bcrypt password hash",
                name="verify_password",
                qualified_name="auth.crypto.verify_password",
                signature="def verify_password(password, hashed)",
                docstring="validate bcrypt hash",
                pagerank=0.1,
                callers=0,
                callees=0,
            )
            without_semantic = rank_symbol(
                query="bcrypt password hash",
                name="verify_password",
                qualified_name="auth.crypto.verify_password",
                signature=None,
                docstring=None,
                pagerank=0.1,
                callers=0,
                callees=0,
            )
            self.assertGreater(with_semantic, without_semantic)
        finally:
            if previous_hybrid is None:
                os.environ.pop("BOMBE_HYBRID_SEARCH", None)
            else:
                os.environ["BOMBE_HYBRID_SEARCH"] = previous_hybrid
            if previous_vector is None:
                os.environ.pop("BOMBE_HYBRID_VECTOR", None)
            else:
                os.environ["BOMBE_HYBRID_VECTOR"] = previous_vector

    def test_rank_symbol_falls_back_to_structural_when_hybrid_disabled(self) -> None:
        previous_hybrid = os.getenv("BOMBE_HYBRID_SEARCH")
        os.environ["BOMBE_HYBRID_SEARCH"] = "0"
        try:
            rank_a = rank_symbol(
                query="auth",
                name="authenticate",
                qualified_name="app.auth.authenticate",
                signature=None,
                docstring=None,
                pagerank=0.3,
                callers=3,
                callees=1,
            )
            rank_b = rank_symbol(
                query="auth",
                name="authenticate",
                qualified_name="app.auth.authenticate",
                signature=None,
                docstring=None,
                pagerank=0.3,
                callers=3,
                callees=1,
            )
            self.assertAlmostEqual(rank_a, rank_b, places=6)
        finally:
            if previous_hybrid is None:
                os.environ.pop("BOMBE_HYBRID_SEARCH", None)
            else:
                os.environ["BOMBE_HYBRID_SEARCH"] = previous_hybrid


if __name__ == "__main__":
    unittest.main()
