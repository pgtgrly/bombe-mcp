"""Language parsing wrapper used by extraction passes."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import importlib.metadata
import os
from pathlib import Path
from typing import Any

from bombe.models import ParsedUnit


SUPPORTED_LANGUAGES = {"python", "java", "typescript", "go"}
TREE_SITTER_LANGUAGE_MAP = {
    "python": "python",
    "java": "java",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
    "cpp": "cpp",
}
TREE_SITTER_REQUIRED_LANGUAGES = (
    "python",
    "java",
    "go",
    "typescript",
    "rust",
    "cpp",
)


def _load_tree_sitter_parser(language: str) -> Any | None:
    if language not in TREE_SITTER_LANGUAGE_MAP:
        return None
    spec = importlib.util.find_spec("tree_sitter_languages")
    if spec is None:
        return None
    try:
        module = importlib.import_module("tree_sitter_languages")
    except Exception:
        return None
    get_parser = getattr(module, "get_parser", None)
    if get_parser is None:
        return None
    try:
        return get_parser(TREE_SITTER_LANGUAGE_MAP[language])
    except Exception:
        return None


def tree_sitter_capability_report() -> dict[str, Any]:
    module_available = importlib.util.find_spec("tree_sitter_languages") is not None
    versions: dict[str, str | None] = {}
    for package_name in ("tree-sitter", "tree-sitter-languages"):
        try:
            versions[package_name] = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            versions[package_name] = None

    languages: list[dict[str, Any]] = []
    for language in TREE_SITTER_REQUIRED_LANGUAGES:
        backend = TREE_SITTER_LANGUAGE_MAP.get(language, language)
        available = bool(_load_tree_sitter_parser(language)) if module_available else False
        reason = "ok" if available else (
            "module_not_found" if not module_available else "parser_unavailable"
        )
        languages.append(
            {
                "language": language,
                "backend": backend,
                "available": available,
                "reason": reason,
            }
        )

    all_required_available = all(bool(item["available"]) for item in languages)
    return {
        "module_available": module_available,
        "all_required_available": all_required_available,
        "required_languages": list(TREE_SITTER_REQUIRED_LANGUAGES),
        "versions": versions,
        "languages": languages,
    }


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _require_tree_sitter() -> bool:
    raw = os.getenv("BOMBE_REQUIRE_TREE_SITTER", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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
        if parser is None:
            if _require_tree_sitter():
                raise RuntimeError(
                    f"Tree-sitter parser unavailable for language '{language}'. "
                    "Install compatible tree-sitter dependencies."
                )
            tree = None
        else:
            try:
                tree = parser.parse(source.encode("utf-8"))
            except Exception:
                if _require_tree_sitter():
                    raise
                tree = None

    return ParsedUnit(
        path=str(path),
        language=language,
        source=source,
        tree=tree,
    )
