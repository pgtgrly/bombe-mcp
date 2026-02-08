from __future__ import annotations

import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

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


def _create_fixture_repo(root: Path) -> Database:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    files = {
        "src/api.py": "def service_api(user):\n    return authenticate(user)\n",
        "src/auth.py": (
            "def authenticate(user):\n"
            "    profile = user_repo(user)\n"
            "    return audit_log(profile)\n"
        ),
        "src/repo.py": "def user_repo(user):\n    return {'id': user}\n",
        "src/logging.py": "def audit_log(profile):\n    return profile\n",
        "src/handler.py": "def request_handler(user):\n    return service_api(user)\n",
        "src/models.py": "class BaseUser:\n    pass\n\nclass AppUser(BaseUser):\n    pass\n",
    }
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    db = Database(root / "bombe.db")
    db.init_schema()
    with closing(db.connect()) as conn:
        for index, rel_path in enumerate(files.keys()):
            absolute_path = (root / rel_path).as_posix()
            conn.execute(
                """
                INSERT INTO files(path, language, content_hash, size_bytes)
                VALUES (?, 'python', ?, ?);
                """,
                (absolute_path, f"h{index}", 128),
            )

        rows_to_insert = [
            ("service_api", "pkg.api.service_api", "function", "src/api.py", 1, 2, 0.91),
            ("authenticate", "pkg.auth.authenticate", "function", "src/auth.py", 1, 3, 0.95),
            ("user_repo", "pkg.repo.user_repo", "function", "src/repo.py", 1, 2, 0.80),
            ("audit_log", "pkg.logging.audit_log", "function", "src/logging.py", 1, 2, 0.70),
            ("request_handler", "pkg.handler.request_handler", "function", "src/handler.py", 1, 2, 0.60),
            ("BaseUser", "pkg.models.BaseUser", "class", "src/models.py", 1, 2, 0.55),
            ("AppUser", "pkg.models.AppUser", "class", "src/models.py", 4, 5, 0.50),
        ]
        for name, qn, kind, rel_path, start, end, rank in rows_to_insert:
            conn.execute(
                """
                INSERT INTO symbols(
                    name, qualified_name, kind, file_path, start_line, end_line, signature, pagerank_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    name,
                    qn,
                    kind,
                    (root / rel_path).as_posix(),
                    start,
                    end,
                    f"{name}()",
                    rank,
                ),
            )
        ids = {
            str(row["qualified_name"]): int(row["id"])
            for row in conn.execute("SELECT id, qualified_name FROM symbols;").fetchall()
        }
        edge_rows = [
            (ids["pkg.handler.request_handler"], ids["pkg.api.service_api"], "CALLS", 1),
            (ids["pkg.api.service_api"], ids["pkg.auth.authenticate"], "CALLS", 1),
            (ids["pkg.auth.authenticate"], ids["pkg.repo.user_repo"], "CALLS", 2),
            (ids["pkg.auth.authenticate"], ids["pkg.logging.audit_log"], "CALLS", 3),
            (ids["pkg.models.AppUser"], ids["pkg.models.BaseUser"], "IMPLEMENTS", 4),
        ]
        for source_id, target_id, relationship, line in edge_rows:
            conn.execute(
                """
                INSERT INTO edges(source_id, target_id, source_type, target_type, relationship, line_number)
                VALUES (?, ?, 'symbol', 'symbol', ?, ?);
                """,
                (source_id, target_id, relationship, line),
            )
        conn.commit()
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
        db = _create_fixture_repo(root)
        expected_flow_pairs = {
            ("request_handler", "service_api"),
            ("service_api", "authenticate"),
            ("authenticate", "user_repo"),
            ("authenticate", "audit_log"),
        }
        expected_direct_callers = {"service_api"}
        expected_transitive_callers = {"request_handler"}
        traversal_queries = [
            ("authenticate", "pkg.auth.authenticate"),
            ("service_api", "pkg.api.service_api"),
            ("user_repo", "pkg.repo.user_repo"),
            ("BaseUser", "pkg.models.BaseUser"),
        ]

        for _ in range(iterations):
            start = time.perf_counter()
            flow_payload = trace_data_flow(
                db,
                symbol_name="pkg.auth.authenticate",
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
                symbol_name="pkg.auth.authenticate",
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
                    query="authentication path",
                    entry_points=["pkg.auth.authenticate"],
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
