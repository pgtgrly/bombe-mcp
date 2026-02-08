from __future__ import annotations

import hashlib
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from bombe.indexer.filesystem import compute_content_hash, detect_language, iter_repo_files


class FilesystemTests(unittest.TestCase):
    def test_iter_repo_files_honors_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "ignored.py").write_text("print('x')\n", encoding="utf-8")
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "debug.log").write_text("trace\n", encoding="utf-8")

            files = sorted(path.relative_to(root).as_posix() for path in iter_repo_files(root))
            self.assertEqual(files, [".gitignore", "main.py"])

    def test_iter_repo_files_skips_internal_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
            (root / ".bombe").mkdir()
            (root / ".bombe" / "bombe.db").write_text("internal\n", encoding="utf-8")
            (root / "src.py").write_text("print('ok')\n", encoding="utf-8")
            files = sorted(path.relative_to(root).as_posix() for path in iter_repo_files(root))
            self.assertEqual(files, ["src.py"])

    def test_iter_repo_files_supports_bombeignore_and_include_exclude(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".bombeignore").write_text("tmp/\n", encoding="utf-8")
            (root / "tmp").mkdir()
            (root / "tmp" / "ignored.py").write_text("print('x')\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
            (root / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")
            (root / "src" / "main.go").write_text("package main\n", encoding="utf-8")

            included = sorted(
                path.relative_to(root).as_posix()
                for path in iter_repo_files(
                    root,
                    include_patterns=["src/*.py"],
                    exclude_patterns=["*b.py"],
                )
            )
            self.assertEqual(included, ["src/a.py"])

    def test_iter_repo_files_excludes_sensitive_paths_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "secrets.yaml").write_text("api_key: abc\n", encoding="utf-8")
            (root / "src.py").write_text("print('ok')\n", encoding="utf-8")

            files = sorted(path.relative_to(root).as_posix() for path in iter_repo_files(root))
            self.assertEqual(files, ["src.py"])

    def test_iter_repo_files_allows_sensitive_paths_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "src.py").write_text("print('ok')\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"BOMBE_EXCLUDE_SENSITIVE": "0"}, clear=False):
                files = sorted(path.relative_to(root).as_posix() for path in iter_repo_files(root))
            self.assertIn(".env", files)
            self.assertIn("src.py", files)

    def test_detect_language_uses_extension_mapping(self) -> None:
        self.assertEqual(detect_language(Path("src/main.py")), "python")
        self.assertEqual(detect_language(Path("src/service.java")), "java")
        self.assertEqual(detect_language(Path("src/types.tsx")), "typescript")
        self.assertEqual(detect_language(Path("src/main.go")), "go")
        self.assertIsNone(detect_language(Path("README.md")))

    def test_compute_content_hash_matches_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "sample.py"
            content = b"print('hello')\n"
            file_path.write_bytes(content)
            expected = hashlib.sha256(content).hexdigest()
            self.assertEqual(compute_content_hash(file_path), expected)


if __name__ == "__main__":
    unittest.main()
