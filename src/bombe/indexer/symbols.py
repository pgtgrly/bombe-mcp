"""Symbol and import extraction from parsed source units."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from bombe.models import ImportRecord, ParameterRecord, ParsedUnit, SymbolRecord


def _to_module_name(path: Path) -> str:
    without_suffix = path.with_suffix("")
    parts = list(without_suffix.parts)
    if without_suffix.is_absolute() and parts and parts[0] == without_suffix.anchor:
        parts = parts[1:]
    return ".".join(part for part in parts if part not in ("", "."))


def _visibility(name: str) -> str:
    return "private" if name.startswith("_") else "public"


def _build_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ParameterRecord]:
    params: list[ParameterRecord] = []
    for index, arg in enumerate(node.args.args):
        param_type = ast.unparse(arg.annotation) if arg.annotation else None
        params.append(
            ParameterRecord(
                name=arg.arg,
                type=param_type,
                position=index,
            )
        )
    return params


def _python_imports(tree: ast.AST, source_path: Path) -> list[ImportRecord]:
    imports: list[ImportRecord] = []
    rel_path = source_path.as_posix()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    ImportRecord(
                        source_file_path=rel_path,
                        import_statement=f"import {alias.name}",
                        module_name=alias.name,
                        imported_names=[],
                        line_number=node.lineno,
                    )
                )
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            imported_names = [alias.name for alias in node.names]
            imports.append(
                ImportRecord(
                    source_file_path=rel_path,
                    import_statement=f"from {module_name} import {', '.join(imported_names)}",
                    module_name=module_name,
                    imported_names=imported_names,
                    line_number=node.lineno,
                )
            )
    return imports


def _python_symbols(parsed: ParsedUnit) -> tuple[list[SymbolRecord], list[ImportRecord]]:
    tree = parsed.tree
    if not isinstance(tree, ast.AST):
        return [], []

    module = _to_module_name(parsed.path)
    file_path = parsed.path.as_posix()
    symbols: list[SymbolRecord] = []

    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signature = f"def {node.name}({', '.join(arg.arg for arg in node.args.args)})"
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    qualified_name=f"{module}.{node.name}",
                    kind="function",
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    signature=signature,
                    visibility=_visibility(node.name),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    parameters=_build_parameters(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            class_qualified = f"{module}.{node.name}"
            symbols.append(
                SymbolRecord(
                    name=node.name,
                    qualified_name=class_qualified,
                    kind="class",
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    signature=f"class {node.name}",
                    visibility=_visibility(node.name),
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_signature = f"def {child.name}({', '.join(arg.arg for arg in child.args.args)})"
                    symbols.append(
                        SymbolRecord(
                            name=child.name,
                            qualified_name=f"{class_qualified}.{child.name}",
                            kind="method",
                            file_path=file_path,
                            start_line=child.lineno,
                            end_line=getattr(child, "end_lineno", child.lineno),
                            signature=method_signature,
                            visibility=_visibility(child.name),
                            is_async=isinstance(child, ast.AsyncFunctionDef),
                            parameters=_build_parameters(child),
                        )
                    )

    imports = _python_imports(tree, parsed.path)
    return symbols, imports


JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;")
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;")
JAVA_CLASS_RE = re.compile(
    r"^\s*(?:public|private|protected)?\s*(?:abstract\s+|final\s+)?(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
JAVA_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?[A-Za-z0-9_<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*\{"
)
TS_IMPORT_RE = re.compile(r"^\s*import(?:\s+type)?\s+.*?\s+from\s+['\"]([^'\"]+)['\"];?")
TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
TS_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)
TS_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>"
)
TS_METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected)?\s*(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?::[^=]+)?\s*\{?"
)
GO_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)")
GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+"([^"]+)"')
GO_IMPORT_BLOCK_START_RE = re.compile(r"^\s*import\s*\(")
GO_IMPORT_BLOCK_LINE_RE = re.compile(r'^\s*"([^"]+)"')
GO_TYPE_RE = re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")
GO_FUNCTION_RE = re.compile(r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
GO_METHOD_RE = re.compile(
    r"^\s*func\s*\(([^)]*)\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)"
)


def _java_symbols(parsed: ParsedUnit) -> tuple[list[SymbolRecord], list[ImportRecord]]:
    lines = parsed.source.splitlines()
    file_path = parsed.path.as_posix()
    package_name = ""
    imports: list[ImportRecord] = []
    symbols: list[SymbolRecord] = []
    class_stack: list[tuple[str, int]] = []

    for index, line in enumerate(lines, start=1):
        package_match = JAVA_PACKAGE_RE.match(line)
        if package_match:
            package_name = package_match.group(1)

        import_match = JAVA_IMPORT_RE.match(line)
        if import_match:
            module_name = import_match.group(1)
            imports.append(
                ImportRecord(
                    source_file_path=file_path,
                    import_statement=line.strip(),
                    module_name=module_name,
                    imported_names=[],
                    line_number=index,
                )
            )

        class_match = JAVA_CLASS_RE.match(line)
        if class_match:
            kind = "interface" if class_match.group(1) == "interface" else "class"
            class_name = class_match.group(2)
            qualified_name = f"{package_name}.{class_name}" if package_name else class_name
            class_stack.append((class_name, line.count("{") - line.count("}")))
            symbols.append(
                SymbolRecord(
                    name=class_name,
                    qualified_name=qualified_name,
                    kind=kind,
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public" if "public" in line else "private",
                )
            )
            continue

        method_match = JAVA_METHOD_RE.match(line)
        if method_match and class_stack:
            method_name = method_match.group(1)
            params_raw = method_match.group(2).strip()
            parameters = _parse_parameters(params_raw, language="java")
            current_class = class_stack[-1][0]
            class_prefix = f"{package_name}.{current_class}" if package_name else current_class
            symbols.append(
                SymbolRecord(
                    name=method_name,
                    qualified_name=f"{class_prefix}.{method_name}",
                    kind="method",
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public" if "public" in line else "private",
                    parameters=parameters,
                )
            )

        if class_stack:
            class_name, depth = class_stack[-1]
            depth += line.count("{") - line.count("}")
            class_stack[-1] = (class_name, depth)
            while class_stack and class_stack[-1][1] <= 0:
                class_stack.pop()

    return symbols, imports


def _parse_parameters(params_raw: str, language: str) -> list[ParameterRecord]:
    parameters: list[ParameterRecord] = []
    if not params_raw.strip():
        return parameters
    for index, parameter in enumerate(params_raw.split(",")):
        chunk = parameter.strip()
        if not chunk:
            continue
        name = ""
        param_type: str | None = None
        if language == "typescript" and ":" in chunk:
            before, after = chunk.split(":", maxsplit=1)
            name = before.strip()
            param_type = after.strip()
        elif language == "go":
            chunks = [part for part in chunk.replace("\t", " ").split(" ") if part]
            if chunks:
                name = chunks[0].replace("...", "").strip()
                if len(chunks) > 1:
                    param_type = " ".join(chunks[1:])
        else:
            chunks = [part for part in chunk.replace("\t", " ").split(" ") if part]
            if chunks:
                name = chunks[-1].replace("...", "").strip()
                if len(chunks) > 1:
                    param_type = " ".join(chunks[:-1])
        if name:
            parameters.append(
                ParameterRecord(
                    name=name,
                    type=param_type,
                    position=index,
                )
            )
    return parameters


def _typescript_symbols(parsed: ParsedUnit) -> tuple[list[SymbolRecord], list[ImportRecord]]:
    lines = parsed.source.splitlines()
    file_path = parsed.path.as_posix()
    module_name = _to_module_name(parsed.path)
    imports: list[ImportRecord] = []
    symbols: list[SymbolRecord] = []
    class_stack: list[tuple[str, int]] = []

    for index, line in enumerate(lines, start=1):
        import_match = TS_IMPORT_RE.match(line)
        if import_match:
            import_module = import_match.group(1)
            imports.append(
                ImportRecord(
                    source_file_path=file_path,
                    import_statement=line.strip(),
                    module_name=import_module,
                    imported_names=[],
                    line_number=index,
                )
            )

        class_match = TS_CLASS_RE.match(line)
        if class_match:
            raw_kind = class_match.group(1)
            kind = "interface" if raw_kind in {"interface", "type"} else "class"
            class_name = class_match.group(2)
            symbols.append(
                SymbolRecord(
                    name=class_name,
                    qualified_name=f"{module_name}.{class_name}",
                    kind=kind,
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public",
                )
            )
            class_stack.append((class_name, line.count("{") - line.count("}")))
            continue

        function_match = TS_FUNCTION_RE.match(line)
        if function_match:
            function_name = function_match.group(1)
            parameters = _parse_parameters(function_match.group(2), language="typescript")
            symbols.append(
                SymbolRecord(
                    name=function_name,
                    qualified_name=f"{module_name}.{function_name}",
                    kind="function",
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public",
                    is_async="async " in line,
                    parameters=parameters,
                )
            )
            continue

        arrow_match = TS_ARROW_RE.match(line)
        if arrow_match:
            function_name = arrow_match.group(1)
            parameters = _parse_parameters(arrow_match.group(2), language="typescript")
            symbols.append(
                SymbolRecord(
                    name=function_name,
                    qualified_name=f"{module_name}.{function_name}",
                    kind="function",
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public",
                    is_async="async " in line,
                    parameters=parameters,
                )
            )
            continue

        method_match = TS_METHOD_RE.match(line)
        if method_match and class_stack:
            method_name = method_match.group(1)
            if method_name != "constructor":
                parameters = _parse_parameters(method_match.group(2), language="typescript")
                current_class = class_stack[-1][0]
                symbols.append(
                    SymbolRecord(
                        name=method_name,
                        qualified_name=f"{module_name}.{current_class}.{method_name}",
                        kind="method",
                        file_path=file_path,
                        start_line=index,
                        end_line=index,
                        signature=line.strip(),
                        visibility="public",
                        is_async="async " in line,
                        parameters=parameters,
                    )
                )

        if class_stack:
            class_name, depth = class_stack[-1]
            depth += line.count("{") - line.count("}")
            class_stack[-1] = (class_name, depth)
            while class_stack and class_stack[-1][1] <= 0:
                class_stack.pop()

    return symbols, imports


def _go_symbols(parsed: ParsedUnit) -> tuple[list[SymbolRecord], list[ImportRecord]]:
    lines = parsed.source.splitlines()
    file_path = parsed.path.as_posix()
    package_name = ""
    imports: list[ImportRecord] = []
    symbols: list[SymbolRecord] = []
    import_block = False

    for index, line in enumerate(lines, start=1):
        package_match = GO_PACKAGE_RE.match(line)
        if package_match:
            package_name = package_match.group(1)

        if GO_IMPORT_BLOCK_START_RE.match(line):
            import_block = True
            continue
        if import_block:
            if line.strip() == ")":
                import_block = False
            else:
                block_match = GO_IMPORT_BLOCK_LINE_RE.match(line)
                if block_match:
                    module_name = block_match.group(1)
                    imports.append(
                        ImportRecord(
                            source_file_path=file_path,
                            import_statement=line.strip(),
                            module_name=module_name,
                            imported_names=[],
                            line_number=index,
                        )
                    )
            continue

        import_match = GO_IMPORT_SINGLE_RE.match(line)
        if import_match:
            module_name = import_match.group(1)
            imports.append(
                ImportRecord(
                    source_file_path=file_path,
                    import_statement=line.strip(),
                    module_name=module_name,
                    imported_names=[],
                    line_number=index,
                )
            )
            continue

        type_match = GO_TYPE_RE.match(line)
        if type_match:
            type_name = type_match.group(1)
            type_kind = "interface" if type_match.group(2) == "interface" else "class"
            qualified = f"{package_name}.{type_name}" if package_name else type_name
            symbols.append(
                SymbolRecord(
                    name=type_name,
                    qualified_name=qualified,
                    kind=type_kind,
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public" if type_name[0].isupper() else "private",
                )
            )
            continue

        method_match = GO_METHOD_RE.match(line)
        if method_match:
            receiver_raw = method_match.group(1).strip()
            method_name = method_match.group(2)
            params_raw = method_match.group(3)
            receiver_tokens = [token for token in receiver_raw.split(" ") if token]
            receiver_type = receiver_tokens[-1].replace("*", "") if receiver_tokens else "Receiver"
            parameters = _parse_parameters(params_raw, language="go")
            class_prefix = f"{package_name}.{receiver_type}" if package_name else receiver_type
            symbols.append(
                SymbolRecord(
                    name=method_name,
                    qualified_name=f"{class_prefix}.{method_name}",
                    kind="method",
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public" if method_name[0].isupper() else "private",
                    parameters=parameters,
                )
            )
            continue

        function_match = GO_FUNCTION_RE.match(line)
        if function_match:
            function_name = function_match.group(1)
            params_raw = function_match.group(2)
            parameters = _parse_parameters(params_raw, language="go")
            qualified = f"{package_name}.{function_name}" if package_name else function_name
            symbols.append(
                SymbolRecord(
                    name=function_name,
                    qualified_name=qualified,
                    kind="function",
                    file_path=file_path,
                    start_line=index,
                    end_line=index,
                    signature=line.strip(),
                    visibility="public" if function_name[0].isupper() else "private",
                    parameters=parameters,
                )
            )

    return symbols, imports


def extract_symbols(parsed: ParsedUnit) -> tuple[list[SymbolRecord], list[ImportRecord]]:
    if parsed.language == "python":
        return _python_symbols(parsed)
    if parsed.language == "java":
        return _java_symbols(parsed)
    if parsed.language == "typescript":
        return _typescript_symbols(parsed)
    if parsed.language == "go":
        return _go_symbols(parsed)
    return [], []
