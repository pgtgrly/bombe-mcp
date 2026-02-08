from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.models import ContextRequest, SymbolSearchRequest
from bombe.query.context import get_context
from bombe.query.search import search_symbols
from bombe.store.database import Database

try:
    from tests.perf.perf_utils import record_metrics
except ModuleNotFoundError:
    from perf_utils import record_metrics


@dataclass(frozen=True)
class RealRepoEvalResult:
    repo_root: str
    files_indexed: int
    symbols_indexed: int
    edges_indexed: int
    index_elapsed_ms: float
    search_elapsed_ms: float
    context_elapsed_ms: float
    health_score: float


def get_real_repo_paths() -> list[Path]:
    raw = os.getenv("BOMBE_REAL_REPO_PATHS", "").strip()
    if not raw:
        return []
    paths = [Path(item.strip()).expanduser().resolve() for item in raw.split(",") if item.strip()]
    return [path for path in paths if path.exists() and path.is_dir()]


def _evaluate_single_repo(repo_root: Path) -> RealRepoEvalResult:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "bombe-real-repo.db")
        db.init_schema()

        started = time.perf_counter()
        index_stats = full_index(repo_root, db)
        index_elapsed_ms = (time.perf_counter() - started) * 1000.0

        if index_stats.symbols_indexed <= 0:
            return RealRepoEvalResult(
                repo_root=repo_root.as_posix(),
                files_indexed=index_stats.files_indexed,
                symbols_indexed=index_stats.symbols_indexed,
                edges_indexed=index_stats.edges_indexed,
                index_elapsed_ms=index_elapsed_ms,
                search_elapsed_ms=0.0,
                context_elapsed_ms=0.0,
                health_score=0.0,
            )

        top_symbol_row = db.query(
            """
            SELECT qualified_name, name
            FROM symbols
            ORDER BY pagerank_score DESC, id ASC
            LIMIT 1;
            """
        )[0]
        top_symbol = str(top_symbol_row["qualified_name"])
        top_symbol_name = str(top_symbol_row["name"])

        search_started = time.perf_counter()
        search_response = search_symbols(
            db,
            SymbolSearchRequest(
                query=top_symbol_name,
                limit=10,
            ),
        )
        search_elapsed_ms = (time.perf_counter() - search_started) * 1000.0

        context_started = time.perf_counter()
        context_response = get_context(
            db,
            ContextRequest(
                query=top_symbol_name,
                entry_points=[top_symbol],
                token_budget=1600,
                expansion_depth=2,
            ),
        )
        context_elapsed_ms = (time.perf_counter() - context_started) * 1000.0
        quality = context_response.payload["context_bundle"].get("quality_metrics", {})
        seed_hit_rate = float(quality.get("seed_hit_rate", 0.0))
        connectedness = float(quality.get("connectedness", 0.0))
        search_hit = 1.0 if search_response.total_matches > 0 else 0.0
        health_score = (seed_hit_rate + connectedness + search_hit) / 3.0

        return RealRepoEvalResult(
            repo_root=repo_root.as_posix(),
            files_indexed=index_stats.files_indexed,
            symbols_indexed=index_stats.symbols_indexed,
            edges_indexed=index_stats.edges_indexed,
            index_elapsed_ms=index_elapsed_ms,
            search_elapsed_ms=search_elapsed_ms,
            context_elapsed_ms=context_elapsed_ms,
            health_score=health_score,
        )


def evaluate_real_repo_gates(
    results: list[RealRepoEvalResult],
    *,
    min_health_score: float = 0.5,
    max_index_ms: float = 180000.0,
    max_query_ms: float = 12000.0,
) -> list[str]:
    violations: list[str] = []
    for result in results:
        prefix = f"repo={result.repo_root}"
        if result.files_indexed <= 0 or result.symbols_indexed <= 0:
            violations.append(f"{prefix}:index_empty")
        if result.index_elapsed_ms > max_index_ms:
            violations.append(f"{prefix}:index_latency")
        if result.search_elapsed_ms > max_query_ms:
            violations.append(f"{prefix}:search_latency")
        if result.context_elapsed_ms > max_query_ms:
            violations.append(f"{prefix}:context_latency")
        if result.health_score < min_health_score:
            violations.append(f"{prefix}:health_score")
    return violations


def run_real_repo_eval(max_repos: int = 2) -> tuple[list[RealRepoEvalResult], list[str]]:
    paths = get_real_repo_paths()[: max(1, max_repos)]
    if not paths:
        return [], []

    results = [_evaluate_single_repo(path) for path in paths]
    violations = evaluate_real_repo_gates(results)
    metrics = {
        "repos_evaluated": float(len(results)),
        "avg_index_ms": (
            sum(result.index_elapsed_ms for result in results) / max(1, len(results))
        ),
        "avg_search_ms": (
            sum(result.search_elapsed_ms for result in results) / max(1, len(results))
        ),
        "avg_context_ms": (
            sum(result.context_elapsed_ms for result in results) / max(1, len(results))
        ),
        "avg_health_score": (
            sum(result.health_score for result in results) / max(1, len(results))
        ),
    }
    record_metrics("real_repo_eval", metrics)
    return results, violations
