from __future__ import annotations

import unittest
from dataclasses import replace

from bombe.models import (
    ArtifactBundle,
    DeltaHeader,
    EdgeContractRecord,
    FileDelta,
    IndexDelta,
    QualityStats,
    SymbolKey,
    SymbolRecord,
)
from bombe.sync.client import build_artifact_checksum
from bombe.sync.reconcile import promote_delta, reconcile_artifact


def _symbol(name: str, qualified_name: str, file_path: str, start_line: int) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        qualified_name=qualified_name,
        kind="function",
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + 1,
        signature=f"def {name}()",
    )


def _delta() -> IndexDelta:
    header = DeltaHeader(
        repo_id="repo",
        parent_snapshot="snap_0",
        local_snapshot="snap_1",
        tool_version="0.1.0",
        schema_version=1,
        created_at_utc="2026-02-08T00:00:00Z",
    )
    source_symbol = _symbol("source", "pkg.source", "src/a.py", 1)
    target_symbol = _symbol("target", "pkg.target", "src/b.py", 5)
    edge = EdgeContractRecord(
        source=SymbolKey.from_symbol(source_symbol),
        target=SymbolKey.from_symbol(target_symbol),
        relationship="CALLS",
        line_number=2,
        confidence=0.9,
    )
    return IndexDelta(
        header=header,
        file_changes=[FileDelta(status="M", path="src/a.py")],
        symbol_upserts=[source_symbol, target_symbol],
        edge_upserts=[edge],
        quality_stats=QualityStats(ambiguity_rate=0.05, parse_failures=0),
    )


def _artifact() -> ArtifactBundle:
    stale_symbol = SymbolKey.from_fields(
        qualified_name="pkg.stale",
        file_path="src/a.py",
        start_line=1,
        end_line=2,
        signature="def stale()",
    )
    untouched_symbol = SymbolKey.from_fields(
        qualified_name="pkg.keep",
        file_path="src/c.py",
        start_line=1,
        end_line=2,
        signature="def keep()",
    )
    stale_edge = EdgeContractRecord(
        source=stale_symbol,
        target=untouched_symbol,
        relationship="CALLS",
        line_number=1,
        confidence=0.8,
    )
    artifact = ArtifactBundle(
        artifact_id="artifact_1",
        repo_id="repo",
        snapshot_id="snap_1",
        parent_snapshot="snap_0",
        tool_version="0.1.0",
        schema_version=1,
        created_at_utc="2026-02-08T00:01:00Z",
        promoted_symbols=[stale_symbol, untouched_symbol],
        promoted_edges=[stale_edge],
    )
    return replace(artifact, checksum=build_artifact_checksum(artifact))


class SyncReconcileTests(unittest.TestCase):
    def test_promote_delta_accepts_clean_delta(self) -> None:
        result = promote_delta(
            _delta(),
            artifact_id="artifact_new",
            snapshot_id="snap_1",
            min_edge_confidence=0.8,
        )
        self.assertTrue(result.promoted)
        self.assertEqual(result.reason, "promoted")
        self.assertIsNotNone(result.artifact)
        self.assertTrue(bool(result.artifact and result.artifact.checksum))

    def test_promote_delta_rejects_high_ambiguity(self) -> None:
        noisy = replace(_delta(), quality_stats=QualityStats(ambiguity_rate=0.8, parse_failures=0))
        result = promote_delta(
            noisy,
            artifact_id="artifact_new",
            snapshot_id="snap_1",
            max_ambiguity_rate=0.2,
        )
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "ambiguity_too_high")
        self.assertIsNone(result.artifact)

    def test_reconcile_prefers_local_for_touched_scope(self) -> None:
        merged = reconcile_artifact(_delta(), _artifact())
        symbol_names = {symbol.qualified_name for symbol in merged.promoted_symbols}
        self.assertIn("pkg.keep", symbol_names)
        self.assertIn("pkg.source", symbol_names)
        self.assertNotIn("pkg.stale", symbol_names)

        for edge in merged.promoted_edges:
            self.assertNotEqual(edge.source.qualified_name, "pkg.stale")
        self.assertTrue(bool(merged.checksum))


if __name__ == "__main__":
    unittest.main()
