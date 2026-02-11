"""Repository structure map generation backend (Rust with Python-compatible wrapper)."""

from _bombe_core import get_structure as _rust_get_structure
from bombe.models import StructureRequest


def get_structure(db, request_or_path=".", token_budget=4000, include_signatures=True):
    """Get structure, accepting either a StructureRequest or individual args."""
    if isinstance(request_or_path, StructureRequest):
        return _rust_get_structure(
            db,
            request_or_path.path,
            request_or_path.token_budget,
            request_or_path.include_signatures,
        )
    return _rust_get_structure(db, request_or_path, token_budget, include_signatures)


__all__ = ["get_structure"]
