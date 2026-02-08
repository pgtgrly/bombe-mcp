from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * p))
    return ordered[max(0, min(index, len(ordered) - 1))]


def record_metrics(suite: str, metrics: dict[str, float]) -> Path:
    output_path = Path(os.getenv("BOMBE_PERF_HISTORY", "/tmp/bombe-perf-history.jsonl"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "suite": suite,
        "metrics": metrics,
    }
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True))
        handle.write("\n")
    return output_path
