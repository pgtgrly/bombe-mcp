"""Import resolution from language-specific import records to repository files."""

from __future__ import annotations

import zlib
from pathlib import Path

from bombe.models import EdgeRecord, ExternalDepRecord, FileRecord, ImportRecord


def _file_id(path: str) -> int:
    return int(zlib.crc32(path.encode("utf-8")) & 0x7FFFFFFF)


def _resolve_python(module_name: str, all_files: dict[str, FileRecord]) -> str | None:
    if not module_name:
        return None
    base = module_name.replace(".", "/")
    candidates = [f"{base}.py", f"{base}/__init__.py"]
    for candidate in candidates:
        if candidate in all_files:
            return candidate
    return None


def _resolve_java(module_name: str, all_files: dict[str, FileRecord]) -> str | None:
    candidate = f"{module_name.replace('.', '/')}.java"
    if candidate in all_files:
        return candidate
    return None


def _resolve_typescript(
    source_file: FileRecord,
    module_name: str,
    all_files: dict[str, FileRecord],
) -> str | None:
    if not module_name.startswith("."):
        return None
    source_dir = Path(source_file.path).parent
    resolved_base = (source_dir / module_name).as_posix()
    if resolved_base.startswith("./"):
        resolved_base = resolved_base[2:]
    candidates = [
        resolved_base,
        f"{resolved_base}.ts",
        f"{resolved_base}.tsx",
        f"{resolved_base}/index.ts",
        f"{resolved_base}/index.tsx",
    ]
    for candidate in candidates:
        normalized = Path(candidate).as_posix()
        if normalized in all_files:
            return normalized
    return None


def _read_go_module(repo_root: str) -> str | None:
    go_mod = Path(repo_root) / "go.mod"
    if not go_mod.exists():
        return None
    for line in go_mod.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped.split(" ", maxsplit=1)[1].strip()
    return None


def _resolve_go(
    repo_root: str,
    module_name: str,
    all_files: dict[str, FileRecord],
) -> str | None:
    root_module = _read_go_module(repo_root)
    if root_module is None or not module_name.startswith(root_module):
        return None
    rel_pkg = module_name[len(root_module) :].lstrip("/")
    prefix = f"{rel_pkg}/" if rel_pkg else ""
    candidates = sorted(
        path for path in all_files if path.startswith(prefix) and path.endswith(".go")
    )
    return candidates[0] if candidates else None


def resolve_imports(
    repo_root: str,
    source_file: FileRecord,
    imports: list[ImportRecord],
    all_files: dict[str, FileRecord],
) -> tuple[list[EdgeRecord], list[ExternalDepRecord]]:
    edges: list[EdgeRecord] = []
    external: list[ExternalDepRecord] = []
    source_id = _file_id(source_file.path)

    for import_record in imports:
        module_name = import_record.module_name
        resolved_path: str | None = None

        if source_file.language == "python":
            resolved_path = _resolve_python(module_name, all_files)
        elif source_file.language == "java":
            resolved_path = _resolve_java(module_name, all_files)
        elif source_file.language == "typescript":
            resolved_path = _resolve_typescript(source_file, module_name, all_files)
        elif source_file.language == "go":
            resolved_path = _resolve_go(repo_root, module_name, all_files)

        if resolved_path is None:
            external.append(
                ExternalDepRecord(
                    file_path=source_file.path,
                    import_statement=import_record.import_statement,
                    module_name=module_name,
                    line_number=import_record.line_number,
                )
            )
            continue

        edges.append(
            EdgeRecord(
                source_id=source_id,
                target_id=_file_id(resolved_path),
                source_type="file",
                target_type="file",
                relationship="IMPORTS",
                file_path=source_file.path,
                line_number=import_record.line_number,
                confidence=1.0,
            )
        )

    return edges, external
