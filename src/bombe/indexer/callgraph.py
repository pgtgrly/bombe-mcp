"""Call graph construction from parsed units and symbol tables."""

from __future__ import annotations

import ast
import re
import zlib
from dataclasses import dataclass

from bombe.models import EdgeRecord, ParsedUnit, SymbolRecord


CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
TS_IMPORT_RE = re.compile(r"""import(?:\s+type)?\s+.*?\s+from\s+['"]([^'"]+)['"]""")
PY_FROM_RE = re.compile(r"""from\s+([A-Za-z0-9_\.]+)\s+import""")
PY_IMPORT_RE = re.compile(r"""import\s+([A-Za-z0-9_\.]+)""")
JAVA_IMPORT_RE = re.compile(r"""import\s+([A-Za-z0-9_.*]+);""")
GO_IMPORT_RE = re.compile(r'''"([^"]+)"''')
CALL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "new",
    "function",
    "class",
    "catch",
}


@dataclass(frozen=True)
class CallSite:
    callee_name: str
    line_number: int


def _symbol_id(qualified_name: str) -> int:
    return int(zlib.crc32(qualified_name.encode("utf-8")) & 0x7FFFFFFF)


def _extract_python_calls(parsed: ParsedUnit) -> list[CallSite]:
    tree = parsed.tree
    if not isinstance(tree, ast.AST):
        return []

    callsites: list[CallSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee_name = ""
        if isinstance(node.func, ast.Name):
            callee_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee_name = node.func.attr
        if callee_name:
            callsites.append(CallSite(callee_name=callee_name, line_number=node.lineno))
    return callsites


def _extract_regex_calls(parsed: ParsedUnit) -> list[CallSite]:
    callsites: list[CallSite] = []
    for index, line in enumerate(parsed.source.splitlines(), start=1):
        for match in CALL_RE.finditer(line):
            name = match.group(1)
            if name in CALL_KEYWORDS:
                continue
            prefix = line[: match.start()].strip()
            if prefix.endswith(("def", "function", "func", "class", "new")):
                continue
            callsites.append(CallSite(callee_name=name, line_number=index))
    return callsites


def _extract_calls(parsed: ParsedUnit) -> list[CallSite]:
    if parsed.language == "python":
        return _extract_python_calls(parsed)
    return _extract_regex_calls(parsed)


def _caller_for_line(line_number: int, file_symbols: list[SymbolRecord]) -> SymbolRecord | None:
    containing = [
        symbol
        for symbol in file_symbols
        if symbol.start_line <= line_number <= symbol.end_line
    ]
    if not containing:
        return None
    return min(containing, key=lambda symbol: symbol.end_line - symbol.start_line)


def _import_hints(source: str) -> set[str]:
    hints: set[str] = set()
    for line in source.splitlines():
        normalized = line.strip()
        from_match = PY_FROM_RE.search(normalized)
        if from_match:
            value = from_match.group(1).strip()
            hints.add(value)
            hints.add(value.split(".")[-1])

        import_match = PY_IMPORT_RE.search(normalized)
        if import_match and normalized.startswith("import "):
            value = import_match.group(1).strip()
            hints.add(value)
            hints.add(value.split(".")[-1])

        ts_match = TS_IMPORT_RE.search(normalized)
        if ts_match:
            value = ts_match.group(1).strip()
            hints.add(value)
            hints.add(value.split("/")[-1])

        java_match = JAVA_IMPORT_RE.search(normalized)
        if java_match:
            value = java_match.group(1).strip().rstrip(".*")
            hints.add(value)
            hints.add(value.split(".")[-1])

        if normalized.startswith("import ") and '"' in normalized:
            go_match = GO_IMPORT_RE.search(normalized)
            if go_match:
                value = go_match.group(1).strip()
                hints.add(value)
                hints.add(value.split("/")[-1])
    return hints


def _resolve_targets(
    callee_name: str,
    caller: SymbolRecord,
    candidate_symbols: list[SymbolRecord],
    import_hints: set[str],
) -> tuple[list[SymbolRecord], float]:
    matches = [symbol for symbol in candidate_symbols if symbol.name == callee_name]
    if not matches:
        return [], 0.0

    same_file = [symbol for symbol in matches if symbol.file_path == caller.file_path]
    if same_file:
        return same_file, 1.0 if len(same_file) == 1 else 0.8

    import_scoped = [
        symbol
        for symbol in matches
        if any(
            hint
            and (
                hint in symbol.qualified_name
                or symbol.file_path.endswith(f"/{hint}.py")
                or symbol.file_path.endswith(f"/{hint}.ts")
                or symbol.file_path.endswith(f"/{hint}.go")
            )
            for hint in import_hints
        )
    ]
    if import_scoped:
        return import_scoped, 1.0 if len(import_scoped) == 1 else 0.7

    return matches, 1.0 if len(matches) == 1 else 0.5


def build_call_edges(
    parsed: ParsedUnit,
    file_symbols: list[SymbolRecord],
    candidate_symbols: list[SymbolRecord],
) -> list[EdgeRecord]:
    callsites = _extract_calls(parsed)
    hints = _import_hints(parsed.source)
    edges: list[EdgeRecord] = []
    seen: set[tuple[int, int, int]] = set()

    for callsite in callsites:
        caller = _caller_for_line(callsite.line_number, file_symbols)
        if caller is None:
            continue
        targets, confidence = _resolve_targets(
            callsite.callee_name,
            caller,
            candidate_symbols,
            hints,
        )
        for target in targets:
            source_id = _symbol_id(caller.qualified_name)
            target_id = _symbol_id(target.qualified_name)
            dedupe_key = (source_id, target_id, callsite.line_number)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            edges.append(
                EdgeRecord(
                    source_id=source_id,
                    target_id=target_id,
                    source_type="symbol",
                    target_type="symbol",
                    relationship="CALLS",
                    file_path=parsed.path.as_posix(),
                    line_number=callsite.line_number,
                    confidence=confidence,
                )
            )

    edges.sort(key=lambda edge: (edge.line_number or 0, edge.source_id, edge.target_id))
    return edges
