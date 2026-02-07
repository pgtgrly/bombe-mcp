"""Language parsing wrapper used by extraction passes."""

from __future__ import annotations

import ast
from pathlib import Path

from bombe.models import ParsedUnit


SUPPORTED_LANGUAGES = {"python", "java", "typescript", "go"}


def parse_file(path: Path, language: str) -> ParsedUnit:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source) if language == "python" else None
    return ParsedUnit(
        path=path,
        language=language,
        source=source,
        tree=tree,
    )
