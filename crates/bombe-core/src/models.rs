//! Shared typed models used across indexing, storage, and query layers.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// Schema / contract constants
// ---------------------------------------------------------------------------

/// Delta schema version for incremental sync payloads.
pub const DELTA_SCHEMA_VERSION: i64 = 1;

/// Artifact schema version for promoted bundles.
pub const ARTIFACT_SCHEMA_VERSION: i64 = 1;

/// MCP tool-contract version advertised by the server.
pub const MCP_CONTRACT_VERSION: i64 = 1;

// ---------------------------------------------------------------------------
// Helper functions exposed to Python
// ---------------------------------------------------------------------------

/// Compute a SHA-256 hex digest of the given signature string (or "" if None).
#[pyfunction]
#[pyo3(signature = (signature=None))]
pub fn _signature_hash(signature: Option<String>) -> String {
    let input = signature.unwrap_or_default();
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Derive a short repo identifier (first 16 hex chars of SHA-256) from a canonical path.
#[pyfunction]
pub fn _repo_id_from_path(canonical_path: String) -> String {
    let mut hasher = Sha256::new();
    hasher.update(canonical_path.as_bytes());
    let digest = format!("{:x}", hasher.finalize());
    digest[..16].to_string()
}

// ---------------------------------------------------------------------------
// 1. FileRecord
// ---------------------------------------------------------------------------

/// A record representing a single indexed file.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct FileRecord {
    pub path: String,
    pub language: String,
    pub content_hash: String,
    pub size_bytes: Option<i64>,
}

#[pymethods]
impl FileRecord {
    #[new]
    #[pyo3(signature = (path, language, content_hash, size_bytes=None))]
    fn new(path: String, language: String, content_hash: String, size_bytes: Option<i64>) -> Self {
        Self {
            path,
            language,
            content_hash,
            size_bytes,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "FileRecord(path={:?}, language={:?}, content_hash={:?}, size_bytes={:?})",
            self.path, self.language, self.content_hash, self.size_bytes,
        )
    }
}

// ---------------------------------------------------------------------------
// 2. ParameterRecord
// ---------------------------------------------------------------------------

/// A record representing a single parameter of a symbol (function/method).
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ParameterRecord {
    pub name: String,
    pub position: i64,
    #[pyo3(name = "type")]
    pub type_: Option<String>,
    pub default_value: Option<String>,
}

#[pymethods]
impl ParameterRecord {
    #[new]
    #[pyo3(signature = (name, position, type_=None, default_value=None))]
    fn new(
        name: String,
        position: i64,
        type_: Option<String>,
        default_value: Option<String>,
    ) -> Self {
        Self {
            name,
            position,
            type_,
            default_value,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ParameterRecord(name={:?}, position={}, type={:?}, default_value={:?})",
            self.name, self.position, self.type_, self.default_value,
        )
    }
}

// ---------------------------------------------------------------------------
// 3. SymbolRecord
// ---------------------------------------------------------------------------

/// A record representing a code symbol (function, class, method, etc.).
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct SymbolRecord {
    pub name: String,
    pub qualified_name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub signature: Option<String>,
    pub return_type: Option<String>,
    pub visibility: Option<String>,
    pub is_async: bool,
    pub is_static: bool,
    pub parent_symbol_id: Option<i64>,
    pub docstring: Option<String>,
    pub pagerank_score: f64,
    pub parameters: Vec<ParameterRecord>,
}

#[pymethods]
impl SymbolRecord {
    #[new]
    #[pyo3(signature = (
        name,
        qualified_name,
        kind,
        file_path,
        start_line,
        end_line,
        signature=None,
        return_type=None,
        visibility=None,
        is_async=false,
        is_static=false,
        parent_symbol_id=None,
        docstring=None,
        pagerank_score=0.0,
        parameters=Vec::new(),
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        name: String,
        qualified_name: String,
        kind: String,
        file_path: String,
        start_line: i64,
        end_line: i64,
        signature: Option<String>,
        return_type: Option<String>,
        visibility: Option<String>,
        is_async: bool,
        is_static: bool,
        parent_symbol_id: Option<i64>,
        docstring: Option<String>,
        pagerank_score: f64,
        parameters: Vec<ParameterRecord>,
    ) -> Self {
        Self {
            name,
            qualified_name,
            kind,
            file_path,
            start_line,
            end_line,
            signature,
            return_type,
            visibility,
            is_async,
            is_static,
            parent_symbol_id,
            docstring,
            pagerank_score,
            parameters,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "SymbolRecord(name={:?}, qualified_name={:?}, kind={:?}, file_path={:?}, \
             start_line={}, end_line={})",
            self.name,
            self.qualified_name,
            self.kind,
            self.file_path,
            self.start_line,
            self.end_line,
        )
    }
}

// ---------------------------------------------------------------------------
// 4. SymbolKey
// ---------------------------------------------------------------------------

/// Unique identity key for a symbol (qualified_name + file + line range + sig hash).
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct SymbolKey {
    pub qualified_name: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub signature_hash: String,
}

#[pymethods]
impl SymbolKey {
    #[new]
    fn new(
        qualified_name: String,
        file_path: String,
        start_line: i64,
        end_line: i64,
        signature_hash: String,
    ) -> Self {
        Self {
            qualified_name,
            file_path,
            start_line,
            end_line,
            signature_hash,
        }
    }

    /// Build a ``SymbolKey`` from a ``SymbolRecord``.
    #[classmethod]
    fn from_symbol(_cls: &Bound<'_, pyo3::types::PyType>, symbol: &SymbolRecord) -> Self {
        Self::_from_fields(
            symbol.qualified_name.clone(),
            symbol.file_path.clone(),
            symbol.start_line,
            symbol.end_line,
            symbol.signature.clone(),
        )
    }

    /// Build a ``SymbolKey`` from raw field values (hashing the signature).
    #[classmethod]
    #[pyo3(signature = (qualified_name, file_path, start_line, end_line, signature=None))]
    fn from_fields(
        _cls: &Bound<'_, pyo3::types::PyType>,
        qualified_name: String,
        file_path: String,
        start_line: i64,
        end_line: i64,
        signature: Option<String>,
    ) -> Self {
        Self::_from_fields(qualified_name, file_path, start_line, end_line, signature)
    }

    /// Return the key as a Python tuple.
    fn as_tuple<'py>(&self, py: Python<'py>) -> Bound<'py, PyTuple> {
        PyTuple::new(
            py,
            &[
                self.qualified_name
                    .clone()
                    .into_pyobject(py)
                    .unwrap()
                    .into_any(),
                self.file_path.clone().into_pyobject(py).unwrap().into_any(),
                self.start_line.into_pyobject(py).unwrap().into_any(),
                self.end_line.into_pyobject(py).unwrap().into_any(),
                self.signature_hash
                    .clone()
                    .into_pyobject(py)
                    .unwrap()
                    .into_any(),
            ],
        )
        .unwrap()
    }

    fn __repr__(&self) -> String {
        format!(
            "SymbolKey(qualified_name={:?}, file_path={:?}, start_line={}, end_line={}, \
             signature_hash={:?})",
            self.qualified_name,
            self.file_path,
            self.start_line,
            self.end_line,
            self.signature_hash,
        )
    }

    fn __eq__(&self, other: &SymbolKey) -> bool {
        self.qualified_name == other.qualified_name
            && self.file_path == other.file_path
            && self.start_line == other.start_line
            && self.end_line == other.end_line
            && self.signature_hash == other.signature_hash
    }

    fn __hash__(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        self.qualified_name.hash(&mut hasher);
        self.file_path.hash(&mut hasher);
        self.start_line.hash(&mut hasher);
        self.end_line.hash(&mut hasher);
        self.signature_hash.hash(&mut hasher);
        hasher.finish()
    }
}

impl SymbolKey {
    fn _from_fields(
        qualified_name: String,
        file_path: String,
        start_line: i64,
        end_line: i64,
        signature: Option<String>,
    ) -> Self {
        let sig_hash = _signature_hash(signature);
        Self {
            qualified_name,
            file_path,
            start_line,
            end_line,
            signature_hash: sig_hash,
        }
    }
}

// ---------------------------------------------------------------------------
// 5. EdgeKey
// ---------------------------------------------------------------------------

/// Unique identity key for an edge between two symbols.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct EdgeKey {
    pub source: SymbolKey,
    pub target: SymbolKey,
    pub relationship: String,
    pub line_number: i64,
}

#[pymethods]
impl EdgeKey {
    #[new]
    fn new(source: SymbolKey, target: SymbolKey, relationship: String, line_number: i64) -> Self {
        Self {
            source,
            target,
            relationship,
            line_number,
        }
    }

    /// Return ``(source.as_tuple(), target.as_tuple(), relationship, line_number)``.
    fn as_tuple<'py>(&self, py: Python<'py>) -> Bound<'py, PyTuple> {
        let src = self.source.as_tuple(py);
        let tgt = self.target.as_tuple(py);
        PyTuple::new(
            py,
            &[
                src.into_any(),
                tgt.into_any(),
                self.relationship
                    .clone()
                    .into_pyobject(py)
                    .unwrap()
                    .into_any(),
                self.line_number.into_pyobject(py).unwrap().into_any(),
            ],
        )
        .unwrap()
    }

    fn __repr__(&self) -> String {
        format!(
            "EdgeKey(source={:?}, target={:?}, relationship={:?}, line_number={})",
            self.source, self.target, self.relationship, self.line_number,
        )
    }
}

// ---------------------------------------------------------------------------
// 6. EdgeRecord
// ---------------------------------------------------------------------------

/// A stored edge row with numeric source/target ids.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct EdgeRecord {
    pub source_id: i64,
    pub target_id: i64,
    pub source_type: String,
    pub target_type: String,
    pub relationship: String,
    pub file_path: Option<String>,
    pub line_number: Option<i64>,
    pub confidence: f64,
}

#[pymethods]
impl EdgeRecord {
    #[new]
    #[pyo3(signature = (
        source_id,
        target_id,
        source_type,
        target_type,
        relationship,
        file_path=None,
        line_number=None,
        confidence=1.0,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        source_id: i64,
        target_id: i64,
        source_type: String,
        target_type: String,
        relationship: String,
        file_path: Option<String>,
        line_number: Option<i64>,
        confidence: f64,
    ) -> Self {
        Self {
            source_id,
            target_id,
            source_type,
            target_type,
            relationship,
            file_path,
            line_number,
            confidence,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "EdgeRecord(source_id={}, target_id={}, relationship={:?}, confidence={})",
            self.source_id, self.target_id, self.relationship, self.confidence,
        )
    }
}

// ---------------------------------------------------------------------------
// 7. EdgeContractRecord
// ---------------------------------------------------------------------------

/// A contract-level edge carrying full ``SymbolKey`` endpoints.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct EdgeContractRecord {
    pub source: SymbolKey,
    pub target: SymbolKey,
    pub relationship: String,
    pub line_number: i64,
    pub confidence: f64,
    pub provenance: String,
}

#[pymethods]
impl EdgeContractRecord {
    #[new]
    #[pyo3(signature = (source, target, relationship, line_number, confidence=1.0, provenance="local".to_string()))]
    fn new(
        source: SymbolKey,
        target: SymbolKey,
        relationship: String,
        line_number: i64,
        confidence: f64,
        provenance: String,
    ) -> Self {
        Self {
            source,
            target,
            relationship,
            line_number,
            confidence,
            provenance,
        }
    }

    /// Derive the ``EdgeKey`` for this record.
    fn key(&self) -> EdgeKey {
        EdgeKey {
            source: self.source.clone(),
            target: self.target.clone(),
            relationship: self.relationship.clone(),
            line_number: self.line_number,
        }
    }

    /// Return the tuple representation of the underlying ``EdgeKey``.
    fn as_tuple<'py>(&self, py: Python<'py>) -> Bound<'py, PyTuple> {
        self.key().as_tuple(py)
    }

    fn __repr__(&self) -> String {
        format!(
            "EdgeContractRecord(relationship={:?}, line_number={}, confidence={}, \
             provenance={:?})",
            self.relationship, self.line_number, self.confidence, self.provenance,
        )
    }
}

// ---------------------------------------------------------------------------
// 8. ExternalDepRecord
// ---------------------------------------------------------------------------

/// An external (unresolvable) dependency reference.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ExternalDepRecord {
    pub file_path: String,
    pub import_statement: String,
    pub module_name: String,
    pub line_number: Option<i64>,
}

#[pymethods]
impl ExternalDepRecord {
    #[new]
    #[pyo3(signature = (file_path, import_statement, module_name, line_number=None))]
    fn new(
        file_path: String,
        import_statement: String,
        module_name: String,
        line_number: Option<i64>,
    ) -> Self {
        Self {
            file_path,
            import_statement,
            module_name,
            line_number,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ExternalDepRecord(file_path={:?}, module_name={:?}, line_number={:?})",
            self.file_path, self.module_name, self.line_number,
        )
    }
}

// ---------------------------------------------------------------------------
// 9. ImportRecord
// ---------------------------------------------------------------------------

/// A resolved import statement with its constituent imported names.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ImportRecord {
    pub source_file_path: String,
    pub import_statement: String,
    pub module_name: String,
    pub imported_names: Vec<String>,
    pub line_number: Option<i64>,
}

#[pymethods]
impl ImportRecord {
    #[new]
    #[pyo3(signature = (source_file_path, import_statement, module_name, imported_names=Vec::new(), line_number=None))]
    fn new(
        source_file_path: String,
        import_statement: String,
        module_name: String,
        imported_names: Vec<String>,
        line_number: Option<i64>,
    ) -> Self {
        Self {
            source_file_path,
            import_statement,
            module_name,
            imported_names,
            line_number,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ImportRecord(source_file_path={:?}, module_name={:?}, imported_names={:?})",
            self.source_file_path, self.module_name, self.imported_names,
        )
    }
}

// ---------------------------------------------------------------------------
// 10. ParsedUnit
// ---------------------------------------------------------------------------

/// A parsed source file with its tree-sitter AST.
#[pyclass(frozen, get_all)]
pub struct ParsedUnit {
    pub path: String,
    pub language: String,
    pub source: String,
    pub tree: Py<PyAny>,
}

#[pymethods]
impl ParsedUnit {
    #[new]
    fn new(path: String, language: String, source: String, tree: Py<PyAny>) -> Self {
        Self {
            path,
            language,
            source,
            tree,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ParsedUnit(path={:?}, language={:?})",
            self.path, self.language,
        )
    }
}

// ---------------------------------------------------------------------------
// 11. FileChange
// ---------------------------------------------------------------------------

/// A file-level change detected by git-diff or the watcher.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct FileChange {
    pub status: String,
    pub path: String,
    pub old_path: Option<String>,
}

#[pymethods]
impl FileChange {
    #[new]
    #[pyo3(signature = (status, path, old_path=None))]
    fn new(status: String, path: String, old_path: Option<String>) -> Self {
        Self {
            status,
            path,
            old_path,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "FileChange(status={:?}, path={:?}, old_path={:?})",
            self.status, self.path, self.old_path,
        )
    }
}

// ---------------------------------------------------------------------------
// 12. WorkspaceRoot
// ---------------------------------------------------------------------------

/// A single root entry in a multi-root workspace configuration.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct WorkspaceRoot {
    pub id: String,
    pub path: String,
    pub db_path: String,
    pub enabled: bool,
}

#[pymethods]
impl WorkspaceRoot {
    #[new]
    #[pyo3(signature = (id, path, db_path, enabled=true))]
    fn new(id: String, path: String, db_path: String, enabled: bool) -> Self {
        Self {
            id,
            path,
            db_path,
            enabled,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "WorkspaceRoot(id={:?}, path={:?}, db_path={:?}, enabled={})",
            self.id, self.path, self.db_path, self.enabled,
        )
    }
}

// ---------------------------------------------------------------------------
// 13. WorkspaceConfig
// ---------------------------------------------------------------------------

/// Top-level workspace configuration referencing multiple roots.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct WorkspaceConfig {
    pub name: String,
    pub version: i64,
    pub roots: Vec<WorkspaceRoot>,
}

#[pymethods]
impl WorkspaceConfig {
    #[new]
    #[pyo3(signature = (name, version, roots=Vec::new()))]
    fn new(name: String, version: i64, roots: Vec<WorkspaceRoot>) -> Self {
        Self {
            name,
            version,
            roots,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "WorkspaceConfig(name={:?}, version={}, roots_count={})",
            self.name,
            self.version,
            self.roots.len(),
        )
    }
}

// ---------------------------------------------------------------------------
// 14. FileDelta
// ---------------------------------------------------------------------------

/// A file-level delta entry within an ``IndexDelta``.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct FileDelta {
    pub status: String,
    pub path: String,
    pub old_path: Option<String>,
    pub content_hash: Option<String>,
    pub size_bytes: Option<i64>,
}

#[pymethods]
impl FileDelta {
    #[new]
    #[pyo3(signature = (status, path, old_path=None, content_hash=None, size_bytes=None))]
    fn new(
        status: String,
        path: String,
        old_path: Option<String>,
        content_hash: Option<String>,
        size_bytes: Option<i64>,
    ) -> Self {
        Self {
            status,
            path,
            old_path,
            content_hash,
            size_bytes,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "FileDelta(status={:?}, path={:?}, old_path={:?})",
            self.status, self.path, self.old_path,
        )
    }
}

// ---------------------------------------------------------------------------
// 15. DeltaHeader
// ---------------------------------------------------------------------------

/// Metadata header for an ``IndexDelta`` payload.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct DeltaHeader {
    pub repo_id: String,
    pub parent_snapshot: Option<String>,
    pub local_snapshot: String,
    pub tool_version: String,
    pub schema_version: i64,
    pub created_at_utc: String,
}

#[pymethods]
impl DeltaHeader {
    #[new]
    #[pyo3(signature = (repo_id, parent_snapshot, local_snapshot, tool_version, schema_version, created_at_utc))]
    fn new(
        repo_id: String,
        parent_snapshot: Option<String>,
        local_snapshot: String,
        tool_version: String,
        schema_version: i64,
        created_at_utc: String,
    ) -> Self {
        Self {
            repo_id,
            parent_snapshot,
            local_snapshot,
            tool_version,
            schema_version,
            created_at_utc,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "DeltaHeader(repo_id={:?}, local_snapshot={:?}, schema_version={})",
            self.repo_id, self.local_snapshot, self.schema_version,
        )
    }
}

// ---------------------------------------------------------------------------
// 16. QualityStats
// ---------------------------------------------------------------------------

/// Quality statistics produced alongside an index delta.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct QualityStats {
    pub ambiguity_rate: f64,
    pub unresolved_imports: i64,
    pub parse_failures: i64,
}

#[pymethods]
impl QualityStats {
    #[new]
    #[pyo3(signature = (ambiguity_rate=0.0, unresolved_imports=0, parse_failures=0))]
    fn new(ambiguity_rate: f64, unresolved_imports: i64, parse_failures: i64) -> Self {
        Self {
            ambiguity_rate,
            unresolved_imports,
            parse_failures,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "QualityStats(ambiguity_rate={}, unresolved_imports={}, parse_failures={})",
            self.ambiguity_rate, self.unresolved_imports, self.parse_failures,
        )
    }
}

// ---------------------------------------------------------------------------
// 17. IndexDelta
// ---------------------------------------------------------------------------

/// An incremental index delta describing changes since the last snapshot.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct IndexDelta {
    pub header: DeltaHeader,
    pub file_changes: Vec<FileDelta>,
    pub symbol_upserts: Vec<SymbolRecord>,
    pub symbol_deletes: Vec<SymbolKey>,
    pub edge_upserts: Vec<EdgeContractRecord>,
    pub edge_deletes: Vec<EdgeContractRecord>,
    pub quality_stats: QualityStats,
}

#[pymethods]
impl IndexDelta {
    #[new]
    #[pyo3(signature = (
        header,
        file_changes=Vec::new(),
        symbol_upserts=Vec::new(),
        symbol_deletes=Vec::new(),
        edge_upserts=Vec::new(),
        edge_deletes=Vec::new(),
        quality_stats=QualityStats::new(0.0, 0, 0),
    ))]
    fn new(
        header: DeltaHeader,
        file_changes: Vec<FileDelta>,
        symbol_upserts: Vec<SymbolRecord>,
        symbol_deletes: Vec<SymbolKey>,
        edge_upserts: Vec<EdgeContractRecord>,
        edge_deletes: Vec<EdgeContractRecord>,
        quality_stats: QualityStats,
    ) -> Self {
        Self {
            header,
            file_changes,
            symbol_upserts,
            symbol_deletes,
            edge_upserts,
            edge_deletes,
            quality_stats,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "IndexDelta(header={:?}, file_changes={}, symbol_upserts={}, symbol_deletes={}, \
             edge_upserts={}, edge_deletes={})",
            self.header,
            self.file_changes.len(),
            self.symbol_upserts.len(),
            self.symbol_deletes.len(),
            self.edge_upserts.len(),
            self.edge_deletes.len(),
        )
    }
}

// ---------------------------------------------------------------------------
// 18. ArtifactBundle
// ---------------------------------------------------------------------------

/// A promoted artifact bundle for sync/export.
#[pyclass(frozen, get_all)]
pub struct ArtifactBundle {
    pub artifact_id: String,
    pub repo_id: String,
    pub snapshot_id: String,
    pub parent_snapshot: Option<String>,
    pub tool_version: String,
    pub schema_version: i64,
    pub created_at_utc: String,
    pub promoted_symbols: Vec<SymbolKey>,
    pub promoted_edges: Vec<EdgeContractRecord>,
    pub impact_priors: Py<PyAny>,
    pub flow_hints: Py<PyAny>,
    pub signature_algo: Option<String>,
    pub signing_key_id: Option<String>,
    pub checksum: Option<String>,
    pub signature: Option<String>,
}

#[pymethods]
impl ArtifactBundle {
    #[new]
    #[pyo3(signature = (
        artifact_id,
        repo_id,
        snapshot_id,
        parent_snapshot,
        tool_version,
        schema_version,
        created_at_utc,
        promoted_symbols=Vec::new(),
        promoted_edges=Vec::new(),
        impact_priors=None,
        flow_hints=None,
        signature_algo=None,
        signing_key_id=None,
        checksum=None,
        signature=None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        artifact_id: String,
        repo_id: String,
        snapshot_id: String,
        parent_snapshot: Option<String>,
        tool_version: String,
        schema_version: i64,
        created_at_utc: String,
        promoted_symbols: Vec<SymbolKey>,
        promoted_edges: Vec<EdgeContractRecord>,
        impact_priors: Option<Py<PyAny>>,
        flow_hints: Option<Py<PyAny>>,
        signature_algo: Option<String>,
        signing_key_id: Option<String>,
        checksum: Option<String>,
        signature: Option<String>,
    ) -> Self {
        let impact_priors = impact_priors.unwrap_or_else(|| PyList::empty(py).into_any().unbind());
        let flow_hints = flow_hints.unwrap_or_else(|| PyList::empty(py).into_any().unbind());
        Self {
            artifact_id,
            repo_id,
            snapshot_id,
            parent_snapshot,
            tool_version,
            schema_version,
            created_at_utc,
            promoted_symbols,
            promoted_edges,
            impact_priors,
            flow_hints,
            signature_algo,
            signing_key_id,
            checksum,
            signature,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ArtifactBundle(artifact_id={:?}, repo_id={:?}, snapshot_id={:?})",
            self.artifact_id, self.repo_id, self.snapshot_id,
        )
    }
}

// ---------------------------------------------------------------------------
// 19. IndexStats
// ---------------------------------------------------------------------------

/// Summary statistics from an indexing run.
#[pyclass(frozen, get_all)]
pub struct IndexStats {
    pub files_seen: i64,
    pub files_indexed: i64,
    pub symbols_indexed: i64,
    pub edges_indexed: i64,
    pub elapsed_ms: i64,
    pub run_id: Option<String>,
    pub diagnostics_summary: Py<PyAny>,
    pub indexing_telemetry: Py<PyAny>,
    pub progress_snapshots: Py<PyAny>,
}

#[pymethods]
impl IndexStats {
    #[new]
    #[pyo3(signature = (
        files_seen,
        files_indexed,
        symbols_indexed,
        edges_indexed,
        elapsed_ms,
        run_id=None,
        diagnostics_summary=None,
        indexing_telemetry=None,
        progress_snapshots=None,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        files_seen: i64,
        files_indexed: i64,
        symbols_indexed: i64,
        edges_indexed: i64,
        elapsed_ms: i64,
        run_id: Option<String>,
        diagnostics_summary: Option<Py<PyAny>>,
        indexing_telemetry: Option<Py<PyAny>>,
        progress_snapshots: Option<Py<PyAny>>,
    ) -> Self {
        let diagnostics_summary =
            diagnostics_summary.unwrap_or_else(|| PyDict::new(py).into_any().unbind());
        let indexing_telemetry =
            indexing_telemetry.unwrap_or_else(|| PyDict::new(py).into_any().unbind());
        let progress_snapshots =
            progress_snapshots.unwrap_or_else(|| PyList::empty(py).into_any().unbind());
        Self {
            files_seen,
            files_indexed,
            symbols_indexed,
            edges_indexed,
            elapsed_ms,
            run_id,
            diagnostics_summary,
            indexing_telemetry,
            progress_snapshots,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "IndexStats(files_seen={}, files_indexed={}, symbols_indexed={}, \
             edges_indexed={}, elapsed_ms={})",
            self.files_seen,
            self.files_indexed,
            self.symbols_indexed,
            self.edges_indexed,
            self.elapsed_ms,
        )
    }
}

// ---------------------------------------------------------------------------
// 20. SymbolSearchRequest
// ---------------------------------------------------------------------------

/// Parameters for a symbol-search query.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct SymbolSearchRequest {
    pub query: String,
    pub kind: String,
    pub file_pattern: Option<String>,
    pub limit: i64,
}

#[pymethods]
impl SymbolSearchRequest {
    #[new]
    #[pyo3(signature = (query, kind="any".to_string(), file_pattern=None, limit=20))]
    fn new(query: String, kind: String, file_pattern: Option<String>, limit: i64) -> Self {
        Self {
            query,
            kind,
            file_pattern,
            limit,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "SymbolSearchRequest(query={:?}, kind={:?}, limit={})",
            self.query, self.kind, self.limit,
        )
    }
}

// ---------------------------------------------------------------------------
// 21. ReferenceRequest
// ---------------------------------------------------------------------------

/// Parameters for a reference (callers/callees) query.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ReferenceRequest {
    pub symbol_name: String,
    pub direction: String,
    pub depth: i64,
    pub include_source: bool,
}

#[pymethods]
impl ReferenceRequest {
    #[new]
    #[pyo3(signature = (symbol_name, direction="both".to_string(), depth=1, include_source=false))]
    fn new(symbol_name: String, direction: String, depth: i64, include_source: bool) -> Self {
        Self {
            symbol_name,
            direction,
            depth,
            include_source,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ReferenceRequest(symbol_name={:?}, direction={:?}, depth={})",
            self.symbol_name, self.direction, self.depth,
        )
    }
}

// ---------------------------------------------------------------------------
// 22. ContextRequest
// ---------------------------------------------------------------------------

/// Parameters for a context-assembly query.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ContextRequest {
    pub query: String,
    pub entry_points: Vec<String>,
    pub token_budget: i64,
    pub include_signatures_only: bool,
    pub expansion_depth: i64,
}

#[pymethods]
impl ContextRequest {
    #[new]
    #[pyo3(signature = (
        query,
        entry_points=Vec::new(),
        token_budget=8000,
        include_signatures_only=false,
        expansion_depth=2,
    ))]
    fn new(
        query: String,
        entry_points: Vec<String>,
        token_budget: i64,
        include_signatures_only: bool,
        expansion_depth: i64,
    ) -> Self {
        Self {
            query,
            entry_points,
            token_budget,
            include_signatures_only,
            expansion_depth,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ContextRequest(query={:?}, token_budget={}, expansion_depth={})",
            self.query, self.token_budget, self.expansion_depth,
        )
    }
}

// ---------------------------------------------------------------------------
// 23. StructureRequest
// ---------------------------------------------------------------------------

/// Parameters for a structure (file/directory overview) query.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct StructureRequest {
    pub path: String,
    pub token_budget: i64,
    pub include_signatures: bool,
}

#[pymethods]
impl StructureRequest {
    #[new]
    #[pyo3(signature = (path=".".to_string(), token_budget=4000, include_signatures=true))]
    fn new(path: String, token_budget: i64, include_signatures: bool) -> Self {
        Self {
            path,
            token_budget,
            include_signatures,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "StructureRequest(path={:?}, token_budget={}, include_signatures={})",
            self.path, self.token_budget, self.include_signatures,
        )
    }
}

// ---------------------------------------------------------------------------
// 24. BlastRadiusRequest
// ---------------------------------------------------------------------------

/// Parameters for a blast-radius (impact analysis) query.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct BlastRadiusRequest {
    pub symbol_name: String,
    pub change_type: String,
    pub max_depth: i64,
}

#[pymethods]
impl BlastRadiusRequest {
    #[new]
    #[pyo3(signature = (symbol_name, change_type="behavior".to_string(), max_depth=3))]
    fn new(symbol_name: String, change_type: String, max_depth: i64) -> Self {
        Self {
            symbol_name,
            change_type,
            max_depth,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "BlastRadiusRequest(symbol_name={:?}, change_type={:?}, max_depth={})",
            self.symbol_name, self.change_type, self.max_depth,
        )
    }
}

// ---------------------------------------------------------------------------
// 25. SymbolSearchResponse
// ---------------------------------------------------------------------------

/// Response payload from a symbol-search query.
#[pyclass(frozen, get_all)]
pub struct SymbolSearchResponse {
    pub symbols: Py<PyAny>,
    pub total_matches: i64,
}

#[pymethods]
impl SymbolSearchResponse {
    #[new]
    fn new(symbols: Py<PyAny>, total_matches: i64) -> Self {
        Self {
            symbols,
            total_matches,
        }
    }

    fn __repr__(&self) -> String {
        format!("SymbolSearchResponse(total_matches={})", self.total_matches)
    }
}

// ---------------------------------------------------------------------------
// 26. ReferenceResponse
// ---------------------------------------------------------------------------

/// Response payload from a reference query.
#[pyclass(frozen, get_all)]
pub struct ReferenceResponse {
    pub payload: Py<PyAny>,
}

#[pymethods]
impl ReferenceResponse {
    #[new]
    fn new(payload: Py<PyAny>) -> Self {
        Self { payload }
    }

    fn __repr__(&self) -> String {
        "ReferenceResponse(...)".to_string()
    }
}

// ---------------------------------------------------------------------------
// 27. ContextResponse
// ---------------------------------------------------------------------------

/// Response payload from a context-assembly query.
#[pyclass(frozen, get_all)]
pub struct ContextResponse {
    pub payload: Py<PyAny>,
}

#[pymethods]
impl ContextResponse {
    #[new]
    fn new(payload: Py<PyAny>) -> Self {
        Self { payload }
    }

    fn __repr__(&self) -> String {
        "ContextResponse(...)".to_string()
    }
}

// ---------------------------------------------------------------------------
// 28. BlastRadiusResponse
// ---------------------------------------------------------------------------

/// Response payload from a blast-radius query.
#[pyclass(frozen, get_all)]
pub struct BlastRadiusResponse {
    pub payload: Py<PyAny>,
}

#[pymethods]
impl BlastRadiusResponse {
    #[new]
    fn new(payload: Py<PyAny>) -> Self {
        Self { payload }
    }

    fn __repr__(&self) -> String {
        "BlastRadiusResponse(...)".to_string()
    }
}

// ---------------------------------------------------------------------------
// Phase 15: Cross-repo graphing and sharding models
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 29. GlobalSymbolURI
// ---------------------------------------------------------------------------

/// Globally unique symbol identifier across repositories.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct GlobalSymbolURI {
    pub repo_id: String,
    pub qualified_name: String,
    pub file_path: String,
}

#[pymethods]
impl GlobalSymbolURI {
    #[new]
    fn new(repo_id: String, qualified_name: String, file_path: String) -> Self {
        Self {
            repo_id,
            qualified_name,
            file_path,
        }
    }

    /// The canonical URI string: ``bombe://<repo_id>/<qualified_name>#<file_path>``.
    #[getter]
    fn uri(&self) -> String {
        format!(
            "bombe://{}/{}#{}",
            self.repo_id, self.qualified_name, self.file_path
        )
    }

    /// Parse a ``bombe://`` URI string into a ``GlobalSymbolURI``.
    #[classmethod]
    fn from_uri(_cls: &Bound<'_, pyo3::types::PyType>, uri: String) -> PyResult<Self> {
        const PREFIX: &str = "bombe://";
        if !uri.starts_with(PREFIX) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid GlobalSymbolURI: {uri}"
            )));
        }
        let rest = &uri[PREFIX.len()..];
        let slash_idx = rest.find('/').ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid GlobalSymbolURI (missing /): {uri}"
            ))
        })?;
        let repo_id = rest[..slash_idx].to_string();
        let remainder = &rest[slash_idx + 1..];
        let hash_idx = remainder.find('#').ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid GlobalSymbolURI (missing #): {uri}"
            ))
        })?;
        let qualified_name = remainder[..hash_idx].to_string();
        let file_path = remainder[hash_idx + 1..].to_string();
        Ok(Self {
            repo_id,
            qualified_name,
            file_path,
        })
    }

    /// Build a ``GlobalSymbolURI`` from a repo id and a ``SymbolRecord``.
    #[classmethod]
    fn from_symbol(
        _cls: &Bound<'_, pyo3::types::PyType>,
        repo_id: String,
        symbol: &SymbolRecord,
    ) -> Self {
        Self {
            repo_id,
            qualified_name: symbol.qualified_name.clone(),
            file_path: symbol.file_path.clone(),
        }
    }

    fn __repr__(&self) -> String {
        format!("GlobalSymbolURI(uri={:?})", self.uri())
    }
}

// ---------------------------------------------------------------------------
// 30. ShardInfo
// ---------------------------------------------------------------------------

/// Metadata about a single shard (repo database) in a shard group.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ShardInfo {
    pub repo_id: String,
    pub repo_path: String,
    pub db_path: String,
    pub enabled: bool,
    pub last_indexed_at: Option<String>,
    pub symbol_count: i64,
    pub edge_count: i64,
}

#[pymethods]
impl ShardInfo {
    #[new]
    #[pyo3(signature = (
        repo_id,
        repo_path,
        db_path,
        enabled=true,
        last_indexed_at=None,
        symbol_count=0,
        edge_count=0,
    ))]
    fn new(
        repo_id: String,
        repo_path: String,
        db_path: String,
        enabled: bool,
        last_indexed_at: Option<String>,
        symbol_count: i64,
        edge_count: i64,
    ) -> Self {
        Self {
            repo_id,
            repo_path,
            db_path,
            enabled,
            last_indexed_at,
            symbol_count,
            edge_count,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ShardInfo(repo_id={:?}, repo_path={:?}, enabled={}, symbol_count={}, edge_count={})",
            self.repo_id, self.repo_path, self.enabled, self.symbol_count, self.edge_count,
        )
    }
}

// ---------------------------------------------------------------------------
// 31. CrossRepoEdge
// ---------------------------------------------------------------------------

/// An edge between symbols in different repositories.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct CrossRepoEdge {
    pub source_uri: GlobalSymbolURI,
    pub target_uri: GlobalSymbolURI,
    pub relationship: String,
    pub confidence: f64,
    pub provenance: String,
}

#[pymethods]
impl CrossRepoEdge {
    #[new]
    #[pyo3(signature = (
        source_uri,
        target_uri,
        relationship,
        confidence=1.0,
        provenance="import_resolution".to_string(),
    ))]
    fn new(
        source_uri: GlobalSymbolURI,
        target_uri: GlobalSymbolURI,
        relationship: String,
        confidence: f64,
        provenance: String,
    ) -> Self {
        Self {
            source_uri,
            target_uri,
            relationship,
            confidence,
            provenance,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "CrossRepoEdge(relationship={:?}, confidence={}, provenance={:?})",
            self.relationship, self.confidence, self.provenance,
        )
    }
}

// ---------------------------------------------------------------------------
// 32. ShardGroupConfig
// ---------------------------------------------------------------------------

/// Configuration for a group of repos that may reference each other.
#[pyclass(frozen, get_all)]
#[derive(Clone, Debug)]
pub struct ShardGroupConfig {
    pub name: String,
    pub catalog_db_path: String,
    pub shards: Vec<ShardInfo>,
    pub version: i64,
}

#[pymethods]
impl ShardGroupConfig {
    #[new]
    #[pyo3(signature = (name, catalog_db_path, shards=Vec::new(), version=1))]
    fn new(name: String, catalog_db_path: String, shards: Vec<ShardInfo>, version: i64) -> Self {
        Self {
            name,
            catalog_db_path,
            shards,
            version,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "ShardGroupConfig(name={:?}, shards_count={}, version={})",
            self.name,
            self.shards.len(),
            self.version,
        )
    }
}

// ---------------------------------------------------------------------------
// 33. FederatedQueryResult
// ---------------------------------------------------------------------------

/// Result from a query that spans multiple shards.
#[pyclass(frozen, get_all)]
pub struct FederatedQueryResult {
    pub results: Py<PyAny>,
    pub shard_reports: Py<PyAny>,
    pub total_matches: i64,
    pub shards_queried: i64,
    pub shards_failed: i64,
    pub elapsed_ms: i64,
}

#[pymethods]
impl FederatedQueryResult {
    #[new]
    #[pyo3(signature = (
        results=None,
        shard_reports=None,
        total_matches=0,
        shards_queried=0,
        shards_failed=0,
        elapsed_ms=0,
    ))]
    fn new(
        py: Python<'_>,
        results: Option<Py<PyAny>>,
        shard_reports: Option<Py<PyAny>>,
        total_matches: i64,
        shards_queried: i64,
        shards_failed: i64,
        elapsed_ms: i64,
    ) -> Self {
        let results = results.unwrap_or_else(|| PyList::empty(py).into_any().unbind());
        let shard_reports = shard_reports.unwrap_or_else(|| PyList::empty(py).into_any().unbind());
        Self {
            results,
            shard_reports,
            total_matches,
            shards_queried,
            shards_failed,
            elapsed_ms,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "FederatedQueryResult(total_matches={}, shards_queried={}, shards_failed={}, \
             elapsed_ms={})",
            self.total_matches, self.shards_queried, self.shards_failed, self.elapsed_ms,
        )
    }
}

// ---------------------------------------------------------------------------
// Module registration helper
// ---------------------------------------------------------------------------

/// Register all model types and functions on a Python module.
pub fn register_models(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    // Constants
    m.add("DELTA_SCHEMA_VERSION", DELTA_SCHEMA_VERSION)?;
    m.add("ARTIFACT_SCHEMA_VERSION", ARTIFACT_SCHEMA_VERSION)?;
    m.add("MCP_CONTRACT_VERSION", MCP_CONTRACT_VERSION)?;

    // Helper functions
    m.add_function(wrap_pyfunction!(_signature_hash, m)?)?;
    m.add_function(wrap_pyfunction!(_repo_id_from_path, m)?)?;

    // Classes
    m.add_class::<FileRecord>()?;
    m.add_class::<ParameterRecord>()?;
    m.add_class::<SymbolRecord>()?;
    m.add_class::<SymbolKey>()?;
    m.add_class::<EdgeKey>()?;
    m.add_class::<EdgeRecord>()?;
    m.add_class::<EdgeContractRecord>()?;
    m.add_class::<ExternalDepRecord>()?;
    m.add_class::<ImportRecord>()?;
    m.add_class::<ParsedUnit>()?;
    m.add_class::<FileChange>()?;
    m.add_class::<WorkspaceRoot>()?;
    m.add_class::<WorkspaceConfig>()?;
    m.add_class::<FileDelta>()?;
    m.add_class::<DeltaHeader>()?;
    m.add_class::<QualityStats>()?;
    m.add_class::<IndexDelta>()?;
    m.add_class::<ArtifactBundle>()?;
    m.add_class::<IndexStats>()?;
    m.add_class::<SymbolSearchRequest>()?;
    m.add_class::<ReferenceRequest>()?;
    m.add_class::<ContextRequest>()?;
    m.add_class::<StructureRequest>()?;
    m.add_class::<BlastRadiusRequest>()?;
    m.add_class::<SymbolSearchResponse>()?;
    m.add_class::<ReferenceResponse>()?;
    m.add_class::<ContextResponse>()?;
    m.add_class::<BlastRadiusResponse>()?;
    m.add_class::<GlobalSymbolURI>()?;
    m.add_class::<ShardInfo>()?;
    m.add_class::<CrossRepoEdge>()?;
    m.add_class::<ShardGroupConfig>()?;
    m.add_class::<FederatedQueryResult>()?;

    Ok(())
}
