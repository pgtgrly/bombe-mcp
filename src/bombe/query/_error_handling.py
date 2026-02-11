"""Shared error handling for Rust query wrappers."""


def is_not_found(exc: Exception) -> bool:
    """Check if a ValueError from Rust represents 'symbol not found'.

    Rust ``BombeError::Query("Symbol not found: ...")`` maps to ``ValueError``.
    """
    return isinstance(exc, ValueError) and "not found" in str(exc).lower()
