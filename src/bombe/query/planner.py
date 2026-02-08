"""Query planner with lightweight in-memory response caching."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class _CacheEntry:
    value: dict[str, Any] | str
    expires_at: float


class QueryPlanner:
    def __init__(self, max_entries: int = 512, ttl_seconds: float = 15.0) -> None:
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = max(0.1, ttl_seconds)
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def _cache_key(self, tool_name: str, payload: dict[str, Any]) -> str:
        try:
            normalized_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except TypeError:
            normalized_payload = repr(payload)
        return f"{tool_name}:{normalized_payload}"

    def _evict_expired_locked(self, now: float) -> None:
        expired_keys = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._cache.pop(key, None)

    def _evict_over_capacity_locked(self) -> None:
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)

    def get_or_compute(
        self,
        tool_name: str,
        payload: dict[str, Any],
        compute: Callable[[], dict[str, Any] | str],
    ) -> tuple[dict[str, Any] | str, str]:
        cache_key = self._cache_key(tool_name, payload)
        now = time.monotonic()
        with self._lock:
            self._evict_expired_locked(now)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached.value, "cache_hit"

        result = compute()
        expires_at = time.monotonic() + self.ttl_seconds
        with self._lock:
            self._cache[cache_key] = _CacheEntry(value=result, expires_at=expires_at)
            self._cache.move_to_end(cache_key)
            self._evict_over_capacity_locked()
        return result, "cache_miss"

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._cache), "max_entries": self.max_entries}
