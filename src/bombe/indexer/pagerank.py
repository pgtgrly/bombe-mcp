"""Re-export recompute_pagerank from the Rust core extension."""
from _bombe_core import recompute_pagerank

__all__ = ["recompute_pagerank"]
