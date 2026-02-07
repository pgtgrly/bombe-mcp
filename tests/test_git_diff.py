from __future__ import annotations

import unittest

from bombe.watcher.git_diff import parse_diff_index_output


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


if __name__ == "__main__":
    unittest.main()
