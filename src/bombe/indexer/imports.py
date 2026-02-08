"""Import resolution from language-specific import records to repository files."""

from __future__ import annotations

import posixpath
import zlib
from pathlib import Path

from bombe.models import EdgeRecord, ExternalDepRecord, FileRecord, ImportRecord


def _file_id(path: str) -> int:
    return int(zlib.crc32(path.encode("utf-8")) & 0x7FFFFFFF)


def _resolve_python(
    source_file: FileRecord,
    module_name: str,
    all_files: dict[str, FileRecord],
) -> str | None:
    if not module_name:
        return None
    if module_name.startswith("."):
        levels = len(module_name) - len(module_name.lstrip("."))
        suffix = module_name.lstrip(".")
        source_dir = Path(source_file.path).parent
        base_dir = source_dir
        for _ in range(max(levels - 1, 0)):
            base_dir = base_dir.parent
        if suffix:
            base = (base_dir / suffix.replace(".", "/")).as_posix()
        else:
            base = base_dir.as_posix()
    else:
        base = module_name.replace(".", "/")
    candidates = [f"{base}.py", f"{base}/__init__.py"]
    for candidate in candidates:
        if candidate in all_files:
            return candidate
    return None


def _resolve_java(module_name: str, all_files: dict[str, FileRecord]) -> str | None:
    if module_name.endswith(".*"):
        package_prefix = module_name[:-2].replace(".", "/")
        candidates = sorted(
            path for path in all_files if path.startswith(f"{package_prefix}/") and path.endswith(".java")
        )
        return candidates[0] if candidates else None
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
    resolved_base = posixpath.normpath((source_dir / module_name).as_posix())
    if resolved_base.startswith("./"):
        resolved_base = resolved_base[2:]
    candidates = [
        resolved_base,
        f"{resolved_base}.ts",
        f"{resolved_base}.tsx",
        f"{resolved_base}.js",
        f"{resolved_base}.jsx",
        f"{resolved_base}/index.ts",
        f"{resolved_base}/index.tsx",
        f"{resolved_base}/index.js",
        f"{resolved_base}/index.jsx",
    ]
    for candidate in candidates:
        normalized = posixpath.normpath(Path(candidate).as_posix())
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
    source_file: FileRecord,
    module_name: str,
    all_files: dict[str, FileRecord],
) -> str | None:
    if module_name.startswith("."):
        source_dir = Path(source_file.path).parent
        normalized = posixpath.normpath((source_dir / module_name).as_posix())
        candidates = sorted(
            path for path in all_files if path.startswith(f"{normalized}/") and path.endswith(".go")
        )
        return candidates[0] if candidates else None

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
    file_id_lookup: dict[str, int] | None = None,
) -> tuple[list[EdgeRecord], list[ExternalDepRecord]]:
    edges: list[EdgeRecord] = []
    external: list[ExternalDepRecord] = []
    source_id = (
        int(file_id_lookup[source_file.path])
        if file_id_lookup is not None and source_file.path in file_id_lookup
        else _file_id(source_file.path)
    )

    for import_record in imports:
        module_name = import_record.module_name
        resolved_path: str | None = None

        if source_file.language == "python":
            resolved_path = _resolve_python(source_file, module_name, all_files)
        elif source_file.language == "java":
            resolved_path = _resolve_java(module_name, all_files)
        elif source_file.language == "typescript":
            resolved_path = _resolve_typescript(source_file, module_name, all_files)
        elif source_file.language == "go":
            resolved_path = _resolve_go(repo_root, source_file, module_name, all_files)

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
                target_id=(
                    int(file_id_lookup[resolved_path])
                    if file_id_lookup is not None and resolved_path in file_id_lookup
                    else _file_id(resolved_path)
                ),
                source_type="file",
                target_type="file",
                relationship="IMPORTS",
                file_path=source_file.path,
                line_number=import_record.line_number,
                confidence=1.0,
            )
        )

    return edges, external
