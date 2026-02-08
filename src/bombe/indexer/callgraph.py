"""Call graph construction from parsed units and symbol tables."""

from __future__ import annotations

import ast
import re
import zlib
from collections import defaultdict
from dataclasses import dataclass

from bombe.models import EdgeRecord, ParsedUnit, SymbolRecord


CALL_RE = re.compile(
    r"\b(?:([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
TS_IMPORT_RE = re.compile(r"""import(?:\s+type)?\s+.*?\s+from\s+['"]([^'"]+)['"]""")
TS_NAMED_IMPORT_RE = re.compile(
    r"""^\s*import(?:\s+type)?\s+\{([^}]*)\}\s+from\s+['"][^'"]+['"]"""
)
TS_DEFAULT_IMPORT_RE = re.compile(
    r"""^\s*import(?:\s+type)?\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s+['"][^'"]+['"]"""
)
PY_FROM_RE = re.compile(r"""from\s+([A-Za-z0-9_\.]+)\s+import""")
PY_IMPORT_RE = re.compile(r"""import\s+([A-Za-z0-9_\.]+)""")
PY_FROM_ALIAS_RE = re.compile(r"""^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+(.+)$""")
PY_IMPORT_ALIAS_RE = re.compile(
    r"""^\s*import\s+([A-Za-z0-9_\.]+)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\s*$"""
)
JAVA_IMPORT_RE = re.compile(r"""import\s+([A-Za-z0-9_.*]+);""")
GO_IMPORT_RE = re.compile(r'''"([^"]+)"''')
PY_ASSIGN_TYPE_RE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*\("""
)
JAVA_NEW_TYPE_RE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_<>?,\s]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*new\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("""
)
TS_NEW_TYPE_RE = re.compile(
    r"""^\s*(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_<>]*))?\s*=\s*new\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("""
)
GO_SHORT_DECL_TYPE_RE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:=\s*&?([A-Za-z_][A-Za-z0-9_]*)\s*\{"""
)
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
    receiver_name: str | None = None


@dataclass(frozen=True)
class ReceiverHintBlock:
    caller_name: str
    start_line: int
    end_line: int
    receiver_types: dict[str, set[str]]


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
        receiver_name: str | None = None
        if isinstance(node.func, ast.Name):
            callee_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                receiver_name = node.func.value.id
            elif isinstance(node.func.value, ast.Attribute):
                receiver_name = node.func.value.attr
        if callee_name:
            callsites.append(
                CallSite(
                    callee_name=callee_name,
                    line_number=node.lineno,
                    receiver_name=receiver_name,
                )
            )
    return callsites


def _extract_regex_calls(parsed: ParsedUnit) -> list[CallSite]:
    callsites: list[CallSite] = []
    for index, line in enumerate(parsed.source.splitlines(), start=1):
        for match in CALL_RE.finditer(line):
            receiver = match.group(1)
            name = match.group(2)
            if name in CALL_KEYWORDS:
                continue
            prefix = line[: match.start()].strip()
            if prefix.endswith(("def", "function", "func", "class", "new")):
                continue
            callsites.append(
                CallSite(
                    callee_name=name,
                    line_number=index,
                    receiver_name=receiver,
                )
            )
    return callsites


def _extract_calls(parsed: ParsedUnit) -> list[CallSite]:
    if parsed.language == "python":
        return _extract_python_calls(parsed)
    return _extract_regex_calls(parsed)


def _annotation_type_name(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        slice_value = annotation.slice
        if isinstance(slice_value, ast.Tuple):
            for item in slice_value.elts:
                resolved = _annotation_type_name(item)
                if resolved:
                    return resolved
        else:
            resolved = _annotation_type_name(slice_value)
            if resolved:
                return resolved
        return _annotation_type_name(annotation.value)
    return None


def _call_type_name(node: ast.AST | None) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _record_receiver_type(receiver_types: dict[str, set[str]], target: ast.AST, type_name: str) -> None:
    if isinstance(target, ast.Name):
        receiver_types.setdefault(target.id, set()).add(type_name)
        return
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        receiver_key = f"{target.value.id}.{target.attr}"
        receiver_types.setdefault(receiver_key, set()).add(type_name)
        if target.value.id == "self":
            receiver_types.setdefault(target.attr, set()).add(type_name)


def _collect_receiver_types(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, set[str]]:
    receiver_types: dict[str, set[str]] = defaultdict(set)
    for arg in function_node.args.args:
        type_name = _annotation_type_name(arg.annotation)
        if type_name:
            receiver_types[arg.arg].add(type_name)

    for node in ast.walk(function_node):
        if isinstance(node, ast.Assign):
            type_name = _call_type_name(node.value)
            if not type_name:
                continue
            for target in node.targets:
                _record_receiver_type(receiver_types, target, type_name)
        elif isinstance(node, ast.AnnAssign):
            type_name = _annotation_type_name(node.annotation) or _call_type_name(node.value)
            if type_name:
                _record_receiver_type(receiver_types, node.target, type_name)
    return {name: set(values) for name, values in receiver_types.items()}


def _python_receiver_hint_blocks(parsed: ParsedUnit) -> list[ReceiverHintBlock]:
    tree = parsed.tree
    if not isinstance(tree, ast.Module):
        return []
    blocks: list[ReceiverHintBlock] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            blocks.append(
                ReceiverHintBlock(
                    caller_name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    receiver_types=_collect_receiver_types(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            class_member_types: dict[str, set[str]] = defaultdict(set)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__":
                    init_hints = _collect_receiver_types(child)
                    for key, values in init_hints.items():
                        normalized_key = key.split(".", maxsplit=1)[-1] if "." in key else key
                        class_member_types[normalized_key].update(values)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    receiver_types = _collect_receiver_types(child)
                    if child.name != "__init__":
                        for key, values in class_member_types.items():
                            receiver_types.setdefault(key, set()).update(values)
                    blocks.append(
                        ReceiverHintBlock(
                            caller_name=child.name,
                            start_line=child.lineno,
                            end_line=getattr(child, "end_lineno", child.lineno),
                            receiver_types=receiver_types,
                        )
                    )
    return blocks


def _receiver_types_for_call(
    caller: SymbolRecord,
    callsite: CallSite,
    blocks: list[ReceiverHintBlock],
) -> set[str]:
    receiver_name = (callsite.receiver_name or "").strip()
    if not receiver_name:
        return set()
    best_block: ReceiverHintBlock | None = None
    for block in blocks:
        if block.caller_name != caller.name:
            continue
        if not (block.start_line <= callsite.line_number <= block.end_line):
            continue
        if best_block is None:
            best_block = block
            continue
        current_span = best_block.end_line - best_block.start_line
        next_span = block.end_line - block.start_line
        if next_span < current_span:
            best_block = block
    if best_block is None:
        return set()

    hints = set(best_block.receiver_types.get(receiver_name, set()))
    hints.update(best_block.receiver_types.get(f"self.{receiver_name}", set()))
    return hints


def _type_name_tokens(type_name: str) -> set[str]:
    value = type_name.strip()
    if not value:
        return set()
    lowered = value.lower()
    tokens = {lowered}
    for separator in (".", "::", "/"):
        if separator in value:
            tokens.add(value.split(separator)[-1].lower())
    return tokens


def _lexical_receiver_type_hints(
    parsed: ParsedUnit,
    receiver_name: str | None,
    line_number: int,
    window: int = 60,
) -> set[str]:
    if not receiver_name:
        return set()
    receiver = receiver_name.strip()
    if not receiver:
        return set()

    lines = parsed.source.splitlines()
    end_index = min(max(line_number - 1, 0), len(lines))
    begin_index = max(0, end_index - window)
    hints: set[str] = set()
    for index in range(end_index - 1, begin_index - 1, -1):
        line = lines[index]
        py_match = PY_ASSIGN_TYPE_RE.match(line)
        if py_match and py_match.group(1) == receiver:
            hints.add(py_match.group(2))

        java_match = JAVA_NEW_TYPE_RE.match(line)
        if java_match and java_match.group(2) == receiver:
            declared = java_match.group(1).strip().split("<", maxsplit=1)[0]
            constructed = java_match.group(3).strip()
            hints.add(declared)
            hints.add(constructed)

        ts_match = TS_NEW_TYPE_RE.match(line)
        if ts_match and ts_match.group(1) == receiver:
            declared = (ts_match.group(2) or "").strip().split("<", maxsplit=1)[0]
            constructed = ts_match.group(3).strip()
            if declared:
                hints.add(declared)
            hints.add(constructed)

        go_match = GO_SHORT_DECL_TYPE_RE.match(line)
        if go_match and go_match.group(1) == receiver:
            hints.add(go_match.group(2))
    return hints


def _method_owner_name(symbol: SymbolRecord) -> str:
    parts = symbol.qualified_name.split(".")
    if len(parts) < 2:
        return ""
    return parts[-2]


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


def _import_aliases(source: str) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for raw_line in source.splitlines():
        normalized = raw_line.strip()
        if not normalized:
            continue

        from_match = PY_FROM_ALIAS_RE.match(normalized)
        if from_match:
            imported_items = from_match.group(2)
            for chunk in imported_items.split(","):
                token = chunk.strip()
                if not token:
                    continue
                parts = [part.strip() for part in token.split(" as ")]
                imported = parts[0]
                alias = parts[1] if len(parts) > 1 else imported
                aliases.setdefault(alias, set()).add(imported.split(".")[-1])
            continue

        import_match = PY_IMPORT_ALIAS_RE.match(normalized)
        if import_match:
            imported_module = import_match.group(1)
            alias = import_match.group(2) or imported_module.split(".")[-1]
            aliases.setdefault(alias, set()).add(imported_module.split(".")[-1])
            continue

        ts_named = TS_NAMED_IMPORT_RE.match(normalized)
        if ts_named:
            imported_items = ts_named.group(1)
            for chunk in imported_items.split(","):
                token = chunk.strip()
                if not token:
                    continue
                parts = [part.strip() for part in token.split(" as ")]
                imported = parts[0]
                alias = parts[1] if len(parts) > 1 else imported
                aliases.setdefault(alias, set()).add(imported)
            continue

        ts_default = TS_DEFAULT_IMPORT_RE.match(normalized)
        if ts_default:
            alias = ts_default.group(1)
            aliases.setdefault(alias, set()).add(alias)

    return aliases


def _resolve_targets(
    callsite: CallSite,
    caller: SymbolRecord,
    candidate_symbols: list[SymbolRecord],
    import_hints: set[str],
    alias_hints: dict[str, set[str]],
    receiver_type_hints: set[str],
    lexical_receiver_type_hints: set[str],
) -> tuple[list[SymbolRecord], float]:
    callee_name = callsite.callee_name
    candidate_names = {callee_name}
    candidate_names.update(alias_hints.get(callee_name, set()))
    matches = [symbol for symbol in candidate_symbols if symbol.name in candidate_names]
    if not matches:
        return [], 0.0

    receiver = (callsite.receiver_name or "").strip().lower()
    if caller.kind == "method":
        class_prefix = caller.qualified_name.rsplit(".", maxsplit=1)[0]
        class_scoped = [
            symbol
            for symbol in matches
            if symbol.kind == "method" and symbol.qualified_name.startswith(f"{class_prefix}.")
        ]
        if class_scoped and receiver in {"", "self", "cls", "this"}:
            return class_scoped, 1.0 if len(class_scoped) == 1 else 0.78

    combined_type_hints = set(receiver_type_hints)
    combined_type_hints.update(lexical_receiver_type_hints)
    if combined_type_hints:
        type_tokens: set[str] = set()
        for hint in combined_type_hints:
            type_tokens.update(_type_name_tokens(hint))
        typed_matches = []
        for symbol in matches:
            if symbol.kind != "method":
                continue
            owner = _method_owner_name(symbol)
            owner_tokens = _type_name_tokens(owner)
            if owner_tokens & type_tokens:
                typed_matches.append(symbol)
        if typed_matches:
            return typed_matches, 1.0 if len(typed_matches) == 1 else 0.84

    alias_receiver_hints = alias_hints.get(callsite.receiver_name or "", set())
    if alias_receiver_hints:
        alias_tokens: set[str] = set()
        for hint in alias_receiver_hints:
            alias_tokens.update(_type_name_tokens(hint))
        alias_typed_matches = []
        for symbol in matches:
            if symbol.kind != "method":
                continue
            owner = _method_owner_name(symbol)
            if _type_name_tokens(owner) & alias_tokens:
                alias_typed_matches.append(symbol)
        if alias_typed_matches:
            return alias_typed_matches, 1.0 if len(alias_typed_matches) == 1 else 0.83

    if receiver and receiver not in {"self", "cls", "this"}:
        class_receiver = []
        for symbol in matches:
            if symbol.kind != "method":
                continue
            parts = symbol.qualified_name.split(".")
            owner = parts[-2] if len(parts) >= 2 else ""
            if owner == receiver:
                class_receiver.append(symbol)
        if class_receiver:
            return class_receiver, 1.0 if len(class_receiver) == 1 else 0.79

        receiver_scoped = [
            symbol
            for symbol in matches
            if symbol.kind == "method" and f".{receiver}." in symbol.qualified_name
        ]
        if receiver_scoped:
            return receiver_scoped, 1.0 if len(receiver_scoped) == 1 else 0.75

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
    symbol_id_lookup: dict[tuple[str, str], int] | None = None,
) -> list[EdgeRecord]:
    callsites = _extract_calls(parsed)
    hints = _import_hints(parsed.source)
    alias_hints = _import_aliases(parsed.source)
    receiver_hint_blocks = _python_receiver_hint_blocks(parsed) if parsed.language == "python" else []
    edges: list[EdgeRecord] = []
    seen: set[tuple[int, int, int]] = set()

    for callsite in callsites:
        caller = _caller_for_line(callsite.line_number, file_symbols)
        if caller is None:
            continue
        targets, confidence = _resolve_targets(
            callsite,
            caller,
            candidate_symbols,
            hints,
            alias_hints,
            _receiver_types_for_call(caller, callsite, receiver_hint_blocks),
            _lexical_receiver_type_hints(parsed, callsite.receiver_name, callsite.line_number),
        )
        for target in targets:
            if symbol_id_lookup is None:
                source_id = _symbol_id(caller.qualified_name)
                target_id = _symbol_id(target.qualified_name)
            else:
                source_id = symbol_id_lookup.get((caller.qualified_name, caller.file_path))
                target_id = symbol_id_lookup.get((target.qualified_name, target.file_path))
                if source_id is None or target_id is None:
                    continue
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
