"""Language parsing wrapper used by extraction passes."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

from bombe.models import ParsedUnit


SUPPORTED_LANGUAGES = {"python", "java", "typescript", "go"}
TREE_SITTER_LANGUAGE_MAP = {
    "python": "python",
    "java": "java",
    "typescript": "typescript",
    "go": "go",
}


def _load_tree_sitter_parser(language: str) -> Any | None:
    if language not in TREE_SITTER_LANGUAGE_MAP:
        return None
    spec = importlib.util.find_spec("tree_sitter_languages")
    if spec is None:
        return None
    module = importlib.import_module("tree_sitter_languages")
    get_parser = getattr(module, "get_parser", None)
    if get_parser is None:
        return None
    return get_parser(TREE_SITTER_LANGUAGE_MAP[language])


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_file(path: Path, language: str) -> ParsedUnit:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")

    source = _read_source(path)
    if language == "python":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
    else:
        parser = _load_tree_sitter_parser(language)
        tree = parser.parse(source.encode("utf-8")) if parser else None

    return ParsedUnit(
        path=path,
        language=language,
        source=source,
        tree=tree,
    )
