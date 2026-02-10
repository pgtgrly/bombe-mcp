from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.workspace import (
    build_workspace_config,
    default_workspace_file,
    enabled_workspace_roots,
    load_workspace_config,
    save_workspace_config,
)


class WorkspaceConfigTests(unittest.TestCase):
    def test_load_workspace_falls_back_to_single_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config = load_workspace_config(repo_root)
            self.assertEqual(len(config.roots), 1)
            self.assertEqual(Path(config.roots[0].path), repo_root.resolve())
            self.assertTrue(config.roots[0].enabled)

    def test_save_and_load_workspace_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            root_a = repo_root / "repo-a"
            root_b = repo_root / "repo-b"
            root_a.mkdir()
            root_b.mkdir()
            config = build_workspace_config(
                repo_root=repo_root,
                roots=[root_a, root_b],
                name="team-workspace",
            )
            workspace_file = default_workspace_file(repo_root)
            saved = save_workspace_config(repo_root, config, workspace_file=workspace_file)
            loaded = load_workspace_config(repo_root, workspace_file=saved)
            self.assertEqual(loaded.name, "team-workspace")
            self.assertEqual(len(loaded.roots), 2)
            enabled = enabled_workspace_roots(loaded)
            self.assertEqual(len(enabled), 2)
            self.assertEqual({Path(root.path).name for root in enabled}, {"repo-a", "repo-b"})


if __name__ == "__main__":
    unittest.main()
