"""Read-only API payload builders for inspector UI surfaces."""

from bombe.ui_api.inspector import (
    build_explainer_index,
    build_inspector_bundle,
    build_symbol_explanation,
)

__all__ = ["build_inspector_bundle", "build_symbol_explanation", "build_explainer_index"]
