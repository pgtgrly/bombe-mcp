"""Re-export Database from the Rust core extension."""
from _bombe_core import Database

SCHEMA_VERSION = 7

__all__ = ["Database", "SCHEMA_VERSION"]
