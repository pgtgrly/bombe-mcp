from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.watcher.git_diff import (
    get_changed_files,
    parse_diff_index_output,
    parse_status_porcelain_output,
)


class GitDiffTests(unittest.TestCase):
    def test_parse_diff_index_output_all_statuses(self) -> None:
        output = (
            "A\tnew.py\n"
            "M\tchanged.py\n"
            "D\tremoved.py\n"
            "R100\told.py\tnew_name.py\n"
        )
        changes = parse_diff_index_output(output)
        self.assertEqual(len(changes), 4)
        self.assertEqual(changes[0].status, "A")
        self.assertEqual(changes[1].status, "M")
        self.assertEqual(changes[2].status, "D")
        self.assertEqual(changes[3].status, "R")
        self.assertEqual(changes[3].old_path, "old.py")
        self.assertEqual(changes[3].path, "new_name.py")

    def test_parse_status_porcelain_output_supports_untracked_and_renames(self) -> None:
        output = (
            " M changed.py\n"
            "A  staged.py\n"
            "D  removed.py\n"
            "R  old.py -> new.py\n"
            "?? fresh.py\n"
        )
        changes = parse_status_porcelain_output(output)
        self.assertEqual([change.status for change in changes], ["M", "A", "D", "R", "A"])
        self.assertEqual(changes[3].old_path, "old.py")
        self.assertEqual(changes[3].path, "new.py")
        self.assertEqual(changes[4].path, "fresh.py")

    def test_get_changed_files_uses_filesystem_fallback_for_non_git_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            file_path = root / "src" / "main.py"
            file_path.write_text("print('one')\n", encoding="utf-8")

            first = get_changed_files(root)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0].status, "A")
            self.assertEqual(first[0].path, "src/main.py")

            second = get_changed_files(root)
            self.assertEqual(second, [])

            file_path.write_text("print('two')\n", encoding="utf-8")
            third = get_changed_files(root)
            self.assertEqual(len(third), 1)
            self.assertEqual(third[0].status, "M")

            file_path.unlink()
            fourth = get_changed_files(root)
            self.assertEqual(len(fourth), 1)
            self.assertEqual(fourth[0].status, "D")

    def test_get_changed_files_respects_include_and_exclude_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
            (root / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")

            _ = get_changed_files(root)
            filtered = get_changed_files(
                root,
                include_patterns=["src/*.py"],
                exclude_patterns=["*b.py"],
            )
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0].path, "src/a.py")
            filtered = get_changed_files(
                root,
                include_patterns=["src/*.py"],
                exclude_patterns=["*b.py"],
            )
            self.assertEqual(filtered, [])

            (root / "src" / "a.py").write_text("print('aa')\n", encoding="utf-8")
            (root / "src" / "b.py").write_text("print('bb')\n", encoding="utf-8")
            filtered = get_changed_files(
                root,
                include_patterns=["src/*.py"],
                exclude_patterns=["*b.py"],
            )
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0].path, "src/a.py")
            self.assertEqual(filtered[0].status, "M")


if __name__ == "__main__":
    unittest.main()
