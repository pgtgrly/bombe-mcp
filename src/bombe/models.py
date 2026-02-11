"""Bombe shared data models — re-exported from the Rust core extension."""

from _bombe_core import (
    ARTIFACT_SCHEMA_VERSION,
    DELTA_SCHEMA_VERSION,
    MCP_CONTRACT_VERSION,
    ArtifactBundle,
    BlastRadiusRequest,
    BlastRadiusResponse,
    ContextRequest,
    ContextResponse,
    CrossRepoEdge,
    DeltaHeader,
    EdgeContractRecord,
    EdgeKey,
    EdgeRecord,
    ExternalDepRecord,
    FederatedQueryResult,
    FileChange,
    FileDelta,
    FileRecord,
    GlobalSymbolURI,
    ImportRecord,
    IndexDelta,
    IndexStats,
    ParameterRecord,
    ParsedUnit,
    QualityStats,
    ReferenceRequest,
    ReferenceResponse,
    ShardGroupConfig,
    ShardInfo,
    StructureRequest,
    SymbolKey,
    SymbolRecord,
    SymbolSearchRequest,
    SymbolSearchResponse,
    WorkspaceConfig,
    WorkspaceRoot,
    _repo_id_from_path,
    _signature_hash,
)

_RUST_MODEL_CLASSES = (
    ArtifactBundle, BlastRadiusRequest, BlastRadiusResponse, ContextRequest,
    ContextResponse, CrossRepoEdge, DeltaHeader, EdgeContractRecord, EdgeKey,
    EdgeRecord, ExternalDepRecord, FederatedQueryResult, FileChange, FileDelta,
    FileRecord, GlobalSymbolURI, ImportRecord, IndexDelta, IndexStats,
    ParameterRecord, ParsedUnit, QualityStats, ReferenceRequest,
    ReferenceResponse, ShardGroupConfig, ShardInfo, StructureRequest,
    SymbolKey, SymbolRecord, SymbolSearchRequest, SymbolSearchResponse,
    WorkspaceConfig, WorkspaceRoot,
)


def model_to_dict(obj: object) -> dict:
    """Recursively convert a Rust PyO3 model instance to a plain dict.

    Replaces ``dataclasses.asdict`` for PyO3 ``#[pyclass(frozen, get_all)]``
    types which are not Python dataclasses.
    """
    return _convert(obj)


def _convert(value: object) -> object:
    if isinstance(value, _RUST_MODEL_CLASSES):
        result = {}
        cls = type(value)
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            try:
                v = getattr(value, attr_name)
            except Exception:
                continue
            if callable(v):
                continue
            result[attr_name] = _convert(v)
        return result
    if isinstance(value, list):
        return [_convert(item) for item in value]
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_convert(item) for item in value)
    return value


def model_replace(obj: object, **changes: object) -> object:
    """Create a copy of a Rust PyO3 model with some fields replaced.

    Replaces ``dataclasses.replace`` for PyO3 ``#[pyclass(frozen, get_all)]``
    types.  Unlike model_to_dict, this preserves nested objects as-is
    (only the top-level fields specified in *changes* are replaced).
    """
    cls = type(obj)
    kwargs: dict[str, object] = {}
    for attr_name in dir(cls):
        if attr_name.startswith("_"):
            continue
        try:
            v = getattr(obj, attr_name)
        except Exception:
            continue
        if callable(v):
            continue
        kwargs[attr_name] = v
    kwargs.update(changes)
    return cls(**kwargs)


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "model_replace",
    "model_to_dict",
    "DELTA_SCHEMA_VERSION",
    "MCP_CONTRACT_VERSION",
    "_repo_id_from_path",
    "_signature_hash",
    "ArtifactBundle",
    "BlastRadiusRequest",
    "BlastRadiusResponse",
    "ContextRequest",
    "ContextResponse",
    "CrossRepoEdge",
    "DeltaHeader",
    "EdgeContractRecord",
    "EdgeKey",
    "EdgeRecord",
    "ExternalDepRecord",
    "FederatedQueryResult",
    "FileChange",
    "FileDelta",
    "FileRecord",
    "GlobalSymbolURI",
    "ImportRecord",
    "IndexDelta",
    "IndexStats",
    "ParameterRecord",
    "ParsedUnit",
    "QualityStats",
    "ReferenceRequest",
    "ReferenceResponse",
    "ShardGroupConfig",
    "ShardInfo",
    "StructureRequest",
    "SymbolKey",
    "SymbolRecord",
    "SymbolSearchRequest",
    "SymbolSearchResponse",
    "WorkspaceConfig",
    "WorkspaceRoot",
]
