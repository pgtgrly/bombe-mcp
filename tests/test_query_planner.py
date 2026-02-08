from __future__ import annotations

import time
import unittest

from bombe.query.planner import QueryPlanner


class QueryPlannerTests(unittest.TestCase):
    def test_returns_cache_hit_on_repeat_query(self) -> None:
        planner = QueryPlanner(max_entries=16, ttl_seconds=10.0)
        counter = {"calls": 0}

        def _compute() -> dict[str, object]:
            counter["calls"] += 1
            return {"ok": True, "count": counter["calls"]}

        first, first_mode = planner.get_or_compute("search_symbols", {"query": "auth"}, _compute)
        second, second_mode = planner.get_or_compute("search_symbols", {"query": "auth"}, _compute)

        self.assertEqual(first_mode, "cache_miss")
        self.assertEqual(second_mode, "cache_hit")
        self.assertEqual(counter["calls"], 1)
        self.assertEqual(first, second)

    def test_cache_entry_expires_after_ttl(self) -> None:
        planner = QueryPlanner(max_entries=16, ttl_seconds=0.1)
        counter = {"calls": 0}

        def _compute() -> dict[str, object]:
            counter["calls"] += 1
            return {"count": counter["calls"]}

        _, mode_one = planner.get_or_compute("get_context", {"query": "flow"}, _compute)
        time.sleep(0.13)
        _, mode_two = planner.get_or_compute("get_context", {"query": "flow"}, _compute)
        self.assertEqual(mode_one, "cache_miss")
        self.assertEqual(mode_two, "cache_miss")
        self.assertEqual(counter["calls"], 2)

    def test_capacity_eviction_removes_oldest_entry(self) -> None:
        planner = QueryPlanner(max_entries=2, ttl_seconds=30.0)
        planner.get_or_compute("a", {"x": 1}, lambda: {"v": 1})
        planner.get_or_compute("b", {"x": 2}, lambda: {"v": 2})
        planner.get_or_compute("c", {"x": 3}, lambda: {"v": 3})
        stats = planner.stats()
        self.assertEqual(stats["entries"], 2)
        _, mode = planner.get_or_compute("a", {"x": 1}, lambda: {"v": 4})
        self.assertEqual(mode, "cache_miss")


if __name__ == "__main__":
    unittest.main()
