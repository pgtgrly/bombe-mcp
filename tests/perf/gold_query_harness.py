from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bombe.indexer.pipeline import full_index
from bombe.models import SymbolSearchRequest
from bombe.query.search import search_symbols
from bombe.store.database import Database
from tests.perf.perf_utils import record_metrics


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_eval_repo(root: Path) -> None:
    _write(
        root / "modules/security/auth.py",
        (
            "from modules.storage.repo import user_repo\n"
            "def authenticate(user):\n"
            "    return user_repo(user)\n"
        ),
    )
    _write(
        root / "modules/security/api.py",
        "from modules.security.auth import authenticate\n\ndef service_api(user):\n    return authenticate(user)\n",
    )
    _write(
        root / "modules/storage/repo.py",
        "def user_repo(user):\n    return {'id': user}\n",
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


def evaluate_gold_queries(min_top5_hit_rate: float = 0.95) -> tuple[float, list[str]]:
    fixture_path = Path(__file__).with_name("gold_queries.json")
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    queries = payload.get("queries", [])
    if not isinstance(queries, list):
        return 0.0, ["malformed_queries"]

    violations: list[str] = []
    hits = 0
    evaluated = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _build_eval_repo(root)
        db = Database(root / ".bombe" / "bombe.db")
        db.init_schema()
        full_index(root, db)

        for entry in queries:
            if not isinstance(entry, dict):
                continue
            query = str(entry.get("query", "")).strip()
            expected = entry.get("expected", [])
            if not query or not isinstance(expected, list):
                continue
            evaluated += 1
            response = search_symbols(db, SymbolSearchRequest(query=query, limit=5))
            top = {str(item["qualified_name"]) for item in response.symbols[:5]}
            expected_set = {str(item) for item in expected}
            if top & expected_set:
                hits += 1

    hit_rate = hits / max(1, evaluated)
    if hit_rate < min_top5_hit_rate:
        violations.append("gold_top5_hit_rate")
    record_metrics("gold_eval", {"gold_top5_hit_rate": hit_rate, "gold_queries": float(evaluated)})
    return hit_rate, violations
