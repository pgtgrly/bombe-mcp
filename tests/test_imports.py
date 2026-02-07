from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.indexer.imports import resolve_imports
from bombe.models import FileRecord, ImportRecord


class ImportResolutionTests(unittest.TestCase):
    def test_resolve_python_imports(self) -> None:
        all_files = {
            "src/app.py": FileRecord("src/app.py", "python", "h1"),
            "pkg/auth.py": FileRecord("pkg/auth.py", "python", "h2"),
        }
        source = all_files["src/app.py"]
        imports = [
            ImportRecord(
                source_file_path=source.path,
                import_statement="from pkg.auth import login",
                module_name="pkg.auth",
                line_number=1,
            )
        ]
        edges, external = resolve_imports(".", source, imports, all_files)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].relationship, "IMPORTS")
        self.assertEqual(len(external), 0)

    def test_resolve_java_imports(self) -> None:
        all_files = {
            "com/example/Main.java": FileRecord("com/example/Main.java", "java", "h1"),
            "com/example/auth/AuthService.java": FileRecord(
                "com/example/auth/AuthService.java", "java", "h2"
            ),
        }
        source = all_files["com/example/Main.java"]
        imports = [
            ImportRecord(
                source_file_path=source.path,
                import_statement="import com.example.auth.AuthService;",
                module_name="com.example.auth.AuthService",
                line_number=2,
            )
        ]
        edges, external = resolve_imports(".", source, imports, all_files)
        self.assertEqual(len(edges), 1)
        self.assertEqual(len(external), 0)

    def test_resolve_typescript_imports(self) -> None:
        all_files = {
            "src/main.ts": FileRecord("src/main.ts", "typescript", "h1"),
            "src/utils/index.ts": FileRecord("src/utils/index.ts", "typescript", "h2"),
        }
        source = all_files["src/main.ts"]
        imports = [
            ImportRecord(
                source_file_path=source.path,
                import_statement="import { x } from './utils';",
                module_name="./utils",
                line_number=1,
            )
        ]
        edges, external = resolve_imports(".", source, imports, all_files)
        self.assertEqual(len(edges), 1)
        self.assertEqual(len(external), 0)

    def test_resolve_go_imports_with_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "go.mod").write_text(
                "module github.com/example/project\n", encoding="utf-8"
            )
            all_files = {
                "cmd/main.go": FileRecord("cmd/main.go", "go", "h1"),
                "pkg/auth/service.go": FileRecord("pkg/auth/service.go", "go", "h2"),
            }
            source = all_files["cmd/main.go"]
            imports = [
                ImportRecord(
                    source_file_path=source.path,
                    import_statement='import "github.com/example/project/pkg/auth"',
                    module_name="github.com/example/project/pkg/auth",
                    line_number=1,
                )
            ]
            edges, external = resolve_imports(str(repo_root), source, imports, all_files)
            self.assertEqual(len(edges), 1)
            self.assertEqual(len(external), 0)

    def test_unresolved_imports_become_external(self) -> None:
        all_files = {"src/main.py": FileRecord("src/main.py", "python", "h1")}
        source = all_files["src/main.py"]
        imports = [
            ImportRecord(
                source_file_path=source.path,
                import_statement="import requests",
                module_name="requests",
                line_number=1,
            )
        ]
        edges, external = resolve_imports(".", source, imports, all_files)
        self.assertEqual(len(edges), 0)
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0].module_name, "requests")


if __name__ == "__main__":
    unittest.main()
