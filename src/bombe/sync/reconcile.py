"""Promotion and reconciliation policies for hybrid artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from bombe.models import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactBundle,
    EdgeContractRecord,
    IndexDelta,
    SymbolKey,
    model_replace,
)
from bombe.sync.client import build_artifact_checksum


@dataclass(frozen=True)
class PromotionResult:
    promoted: bool
    reason: str
    artifact: ArtifactBundle | None = None


def _touched_paths(delta: IndexDelta) -> set[str]:
    touched: set[str] = set()
    for change in delta.file_changes:
        touched.add(change.path)
        if change.old_path:
            touched.add(change.old_path)
    for symbol in delta.symbol_upserts:
        touched.add(symbol.file_path)
    return touched


def _promoted_symbol_keys(delta: IndexDelta) -> list[SymbolKey]:
    keys = [SymbolKey.from_symbol(symbol) for symbol in delta.symbol_upserts]
    unique: dict[tuple[str, str, int, int, str], SymbolKey] = {
        key.as_tuple(): key for key in keys
    }
    return list(unique.values())


def _promoted_edges(delta: IndexDelta, min_edge_confidence: float) -> list[EdgeContractRecord]:
    filtered = [edge for edge in delta.edge_upserts if edge.confidence >= min_edge_confidence]
    unique: dict[
        tuple[tuple[str, str, int, int, str], tuple[str, str, int, int, str], str, int],
        EdgeContractRecord,
    ] = {edge.as_tuple(): edge for edge in filtered}
    return list(unique.values())


def promote_delta(
    delta: IndexDelta,
    *,
    artifact_id: str,
    snapshot_id: str,
    min_edge_confidence: float = 0.75,
    max_ambiguity_rate: float = 0.25,
    max_parse_failures: int = 0,
    schema_version: int = ARTIFACT_SCHEMA_VERSION,
) -> PromotionResult:
    if delta.quality_stats.ambiguity_rate > max_ambiguity_rate:
        return PromotionResult(promoted=False, reason="ambiguity_too_high")
    if delta.quality_stats.parse_failures > max_parse_failures:
        return PromotionResult(promoted=False, reason="parse_failures_too_high")

    promoted_symbols = _promoted_symbol_keys(delta)
    promoted_edges = _promoted_edges(delta, min_edge_confidence)
    if not promoted_symbols and not promoted_edges:
        return PromotionResult(promoted=False, reason="no_promotable_content")

    artifact = ArtifactBundle(
        artifact_id=artifact_id,
        repo_id=delta.header.repo_id,
        snapshot_id=snapshot_id,
        parent_snapshot=delta.header.parent_snapshot,
        tool_version=delta.header.tool_version,
        schema_version=schema_version,
        created_at_utc=delta.header.created_at_utc,
        promoted_symbols=promoted_symbols,
        promoted_edges=promoted_edges,
    )
    return PromotionResult(
        promoted=True,
        reason="promoted",
        artifact=model_replace(artifact, checksum=build_artifact_checksum(artifact)),
    )


def reconcile_artifact(
    local_delta: IndexDelta,
    artifact: ArtifactBundle,
) -> ArtifactBundle:
    touched = _touched_paths(local_delta)
    local_symbols = _promoted_symbol_keys(local_delta)
    local_symbol_tuples = {key.as_tuple() for key in local_symbols}

    merged_symbols: list[SymbolKey] = []
    for symbol in artifact.promoted_symbols:
        if symbol.file_path in touched:
            continue
        merged_symbols.append(symbol)
    for symbol in local_symbols:
        if symbol.as_tuple() in local_symbol_tuples:
            merged_symbols.append(symbol)
    unique_symbols: dict[tuple[str, str, int, int, str], SymbolKey] = {
        key.as_tuple(): key for key in merged_symbols
    }

    local_edges = _promoted_edges(local_delta, min_edge_confidence=0.0)
    local_edge_tuples = {edge.as_tuple() for edge in local_edges}
    merged_edges: list[EdgeContractRecord] = []
    for edge in artifact.promoted_edges:
        if edge.source.file_path in touched or edge.target.file_path in touched:
            continue
        merged_edges.append(edge)
    for edge in local_edges:
        if edge.as_tuple() in local_edge_tuples:
            merged_edges.append(edge)
    unique_edges: dict[
        tuple[tuple[str, str, int, int, str], tuple[str, str, int, int, str], str, int],
        EdgeContractRecord,
    ] = {edge.as_tuple(): edge for edge in merged_edges}

    merged = ArtifactBundle(
        artifact_id=artifact.artifact_id,
        repo_id=artifact.repo_id,
        snapshot_id=artifact.snapshot_id,
        parent_snapshot=artifact.parent_snapshot,
        tool_version=artifact.tool_version,
        schema_version=artifact.schema_version,
        created_at_utc=artifact.created_at_utc,
        promoted_symbols=list(unique_symbols.values()),
        promoted_edges=list(unique_edges.values()),
        impact_priors=artifact.impact_priors,
        flow_hints=artifact.flow_hints,
    )
    return model_replace(merged, checksum=build_artifact_checksum(merged))
