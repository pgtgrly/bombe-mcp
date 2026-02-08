from __future__ import annotations

import os
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from bombe.indexer.pipeline import full_index
from bombe.models import ContextRequest, ReferenceRequest, SymbolSearchRequest
from bombe.query.change_impact import change_impact
from bombe.query.context import get_context
from bombe.query.data_flow import trace_data_flow
from bombe.query.references import get_references
from bombe.query.search import search_symbols
from bombe.store.database import Database

try:
    from tests.perf.perf_utils import percentile
except ModuleNotFoundError:
    from perf_utils import percentile


@dataclass(frozen=True)
class WorkflowThresholds:
    flow_precision_min: float = 0.90
    flow_latency_ms_p95_max: float = 2000.0
    impact_direct_recall_min: float = 0.95
    impact_transitive_precision_min: float = 0.85
    impact_latency_ms_p95_max: float = 2500.0
    traversal_top5_hit_rate_min: float = 0.95
    traversal_latency_ms_p95_max: float = 1200.0
    context_seed_hit_rate_min: float = 0.90
    context_connectedness_min: float = 0.80
    context_latency_ms_p95_max: float = 1800.0


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_opensearch_like_repo(root: Path) -> None:
    _write(
        root / "modules/security/auth.py",
        (
            "from modules.storage.repo import user_repo\n"
            "from modules.logging.audit import audit_log\n\n"
            "def authenticate(user):\n"
            "    profile = user_repo(user)\n"
            "    return audit_log(profile)\n"
        ),
    )
    _write(
        root / "modules/security/api.py",
        "from modules.security.auth import authenticate\n\ndef service_api(user):\n    return authenticate(user)\n",
    )
    _write(
        root / "modules/security/handler.py",
        "from modules.security.api import service_api\n\ndef request_handler(user):\n    return service_api(user)\n",
    )
    _write(
        root / "modules/storage/repo.py",
        "def user_repo(user):\n    return {'id': user}\n",
    )
    _write(
        root / "modules/logging/audit.py",
        "def audit_log(profile):\n    return profile\n",
    )
    _write(
        root / "modules/security/models.py",
        "class BaseUser:\n    pass\n\nclass AppUser(BaseUser):\n    pass\n",
    )
    _write(
        root / "server/src/main/java/org/opensearch/index/IndexService.java",
        (
            "package org.opensearch.index;\n\n"
            "public class IndexService {\n"
            "  public String resolveShard(String shardId) {\n"
            "    return shardId;\n"
            "  }\n"
            "}\n"
        ),
    )
    _write(
        root / "plugins/ingest/pipeline.ts",
        (
            "export function transform(doc: string): string {\n"
            "  return doc;\n"
            "}\n\n"
            "export function runPipeline(doc: string): string {\n"
            "  return transform(doc);\n"
            "}\n"
        ),
    )
    _write(
        root / "transport/router.go",
        (
            "package router\n\n"
            "func Route(path string) string {\n"
            "  return Normalize(path)\n"
            "}\n\n"
            "func Normalize(path string) string {\n"
            "  return path\n"
            "}\n"
        ),
    )


def _fixture_db(root: Path) -> Database:
    _build_opensearch_like_repo(root)
    db = Database(root / ".bombe" / "bombe.db")
    db.init_schema()
    full_index(root, db)
    return db


def run_workflow_benchmark(
    *,
    iterations: int = 20,
    thresholds: WorkflowThresholds | None = None,
) -> tuple[dict[str, float], list[str]]:
    active_thresholds = thresholds or WorkflowThresholds()
    flow_latencies: list[float] = []
    impact_latencies: list[float] = []
    traversal_latencies: list[float] = []
    context_latencies: list[float] = []

    flow_precisions: list[float] = []
    impact_direct_recalls: list[float] = []
    impact_transitive_precisions: list[float] = []
    traversal_hits: list[float] = []
    seed_hit_rates: list[float] = []
    connectedness_scores: list[float] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db = _fixture_db(root)
        with _cwd(root):
            with closing(db.connect()) as conn:
                row = conn.execute(
                    "SELECT qualified_name FROM symbols WHERE name = 'authenticate' ORDER BY pagerank_score DESC LIMIT 1;"
                ).fetchone()
                if row is None:
                    raise RuntimeError("fixture missing authenticate symbol")
                authenticate_qn = str(row["qualified_name"])
            expected_flow_pairs = {
                ("request_handler", "service_api"),
                ("service_api", "authenticate"),
                ("authenticate", "user_repo"),
                ("authenticate", "audit_log"),
            }
            expected_direct_callers = {"service_api"}
            expected_transitive_callers = {"request_handler"}
            traversal_queries = [
                ("authenticate", authenticate_qn),
                ("IndexService", "org.opensearch.index.IndexService"),
                ("runPipeline", "plugins.ingest.pipeline.runPipeline"),
                ("Route", "router.Route"),
            ]

            for _ in range(iterations):
                start = time.perf_counter()
                flow_payload = trace_data_flow(
                    db,
                    symbol_name=authenticate_qn,
                    direction="both",
                    max_depth=3,
                )
                flow_latencies.append((time.perf_counter() - start) * 1000)
                observed_pairs = {
                    (str(item["from_name"]), str(item["to_name"])) for item in flow_payload["paths"]
                }
                matched = len(observed_pairs & expected_flow_pairs)
                flow_precisions.append(matched / max(1, len(observed_pairs)))

                start = time.perf_counter()
                impact_payload = change_impact(
                    db,
                    symbol_name=authenticate_qn,
                    change_type="behavior",
                    max_depth=3,
                )
                impact_latencies.append((time.perf_counter() - start) * 1000)
                impact_data = impact_payload["impact"]
                direct_names = {str(item["name"]) for item in impact_data["direct_callers"]}
                transitive_names = {str(item["name"]) for item in impact_data["transitive_callers"]}
                direct_match = len(direct_names & expected_direct_callers)
                impact_direct_recalls.append(direct_match / max(1, len(expected_direct_callers)))
                transitive_match = len(transitive_names & expected_transitive_callers)
                impact_transitive_precisions.append(transitive_match / max(1, len(transitive_names)))

                start = time.perf_counter()
                query_hits = 0
                for query, qualified_name in traversal_queries:
                    search_payload = search_symbols(db, SymbolSearchRequest(query=query, limit=5))
                    top_candidates = [str(item["qualified_name"]) for item in search_payload.symbols[:5]]
                    if qualified_name in top_candidates:
                        query_hits += 1
                    get_references(
                        db,
                        ReferenceRequest(symbol_name=qualified_name, direction="both", depth=2),
                    )
                traversal_latencies.append((time.perf_counter() - start) * 1000)
                traversal_hits.append(query_hits / max(1, len(traversal_queries)))

                start = time.perf_counter()
                context_payload = get_context(
                    db,
                    ContextRequest(
                        query="authentication request flow",
                        entry_points=[authenticate_qn],
                        token_budget=1200,
                        expansion_depth=2,
                    ),
                )
                context_latencies.append((time.perf_counter() - start) * 1000)
                quality = context_payload.payload["context_bundle"]["quality_metrics"]
                seed_hit_rates.append(float(quality["seed_hit_rate"]))
                connectedness_scores.append(float(quality["connectedness"]))

    metrics = {
        "workflow_a_flow_precision": sum(flow_precisions) / max(1, len(flow_precisions)),
        "workflow_a_latency_ms_p95": percentile(flow_latencies, 0.95),
        "workflow_b_direct_recall": (
            sum(impact_direct_recalls) / max(1, len(impact_direct_recalls))
        ),
        "workflow_b_transitive_precision": (
            sum(impact_transitive_precisions) / max(1, len(impact_transitive_precisions))
        ),
        "workflow_b_latency_ms_p95": percentile(impact_latencies, 0.95),
        "workflow_c_top5_hit_rate": sum(traversal_hits) / max(1, len(traversal_hits)),
        "workflow_c_latency_ms_p95": percentile(traversal_latencies, 0.95),
        "workflow_d_seed_hit_rate": sum(seed_hit_rates) / max(1, len(seed_hit_rates)),
        "workflow_d_connectedness": sum(connectedness_scores) / max(1, len(connectedness_scores)),
        "workflow_d_latency_ms_p95": percentile(context_latencies, 0.95),
    }
    violations = evaluate_workflow_gates(metrics, active_thresholds)
    return metrics, violations


def evaluate_workflow_gates(
    metrics: dict[str, float],
    thresholds: WorkflowThresholds | None = None,
) -> list[str]:
    active_thresholds = thresholds or WorkflowThresholds()
    violations: list[str] = []
    if metrics["workflow_a_flow_precision"] < active_thresholds.flow_precision_min:
        violations.append("workflow_a_flow_precision")
    if metrics["workflow_a_latency_ms_p95"] > active_thresholds.flow_latency_ms_p95_max:
        violations.append("workflow_a_latency_ms_p95")
    if metrics["workflow_b_direct_recall"] < active_thresholds.impact_direct_recall_min:
        violations.append("workflow_b_direct_recall")
    if metrics["workflow_b_transitive_precision"] < active_thresholds.impact_transitive_precision_min:
        violations.append("workflow_b_transitive_precision")
    if metrics["workflow_b_latency_ms_p95"] > active_thresholds.impact_latency_ms_p95_max:
        violations.append("workflow_b_latency_ms_p95")
    if metrics["workflow_c_top5_hit_rate"] < active_thresholds.traversal_top5_hit_rate_min:
        violations.append("workflow_c_top5_hit_rate")
    if metrics["workflow_c_latency_ms_p95"] > active_thresholds.traversal_latency_ms_p95_max:
        violations.append("workflow_c_latency_ms_p95")
    if metrics["workflow_d_seed_hit_rate"] < active_thresholds.context_seed_hit_rate_min:
        violations.append("workflow_d_seed_hit_rate")
    if metrics["workflow_d_connectedness"] < active_thresholds.context_connectedness_min:
        violations.append("workflow_d_connectedness")
    if metrics["workflow_d_latency_ms_p95"] > active_thresholds.context_latency_ms_p95_max:
        violations.append("workflow_d_latency_ms_p95")
    return violations
