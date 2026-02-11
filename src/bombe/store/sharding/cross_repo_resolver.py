"""Cross-repo resolver wrappers providing Python-compatible API over Rust core."""

from __future__ import annotations

from _bombe_core import compute_repo_id as _rust_compute_repo_id
from _bombe_core import post_index_cross_repo_sync as _rust_post_index_cross_repo_sync
from _bombe_core import resolve_cross_repo_imports as _rust_resolve_cross_repo_imports
from bombe.models import CrossRepoEdge, GlobalSymbolURI


def compute_repo_id(path):
    """Compute repo ID, accepting both str and Path objects."""
    return _rust_compute_repo_id(str(path))


def resolve_cross_repo_imports(catalog, repo_id, db):
    """Resolve cross-repo imports, unwrapping catalog wrapper if needed.

    Returns list of CrossRepoEdge objects (converting from Rust edge dicts).
    """
    inner_catalog = getattr(catalog, "_inner", catalog)
    results = _rust_resolve_cross_repo_imports(inner_catalog, repo_id, db)
    return [_dict_to_cross_repo_edge(r) for r in results]


def post_index_cross_repo_sync(repo_root, db, catalog):
    """Post-index cross-repo sync with Python calling convention.

    Python callers use: post_index_cross_repo_sync(repo_root, db, catalog)
    Rust function uses: post_index_cross_repo_sync(catalog, repo_path, db)
    """
    inner_catalog = getattr(catalog, "_inner", catalog)
    return _rust_post_index_cross_repo_sync(inner_catalog, str(repo_root), db)


def _dict_to_cross_repo_edge(d):
    """Convert a flat dict from Rust into a CrossRepoEdge model."""
    if isinstance(d, dict):
        return CrossRepoEdge(
            source_uri=GlobalSymbolURI(
                repo_id=str(d.get("source_repo_id", "")),
                qualified_name=str(d.get("source_qualified_name", "")),
                file_path=str(d.get("source_file_path", "")),
            ),
            target_uri=GlobalSymbolURI(
                repo_id=str(d.get("target_repo_id", "")),
                qualified_name=str(d.get("target_qualified_name", "")),
                file_path=str(d.get("target_file_path", "")),
            ),
            relationship=str(d.get("relationship", "")),
            confidence=float(d.get("confidence", 1.0)),
        )
    # If already a CrossRepoEdge or similar object, return as-is
    return d


__all__ = ["compute_repo_id", "resolve_cross_repo_imports", "post_index_cross_repo_sync"]
