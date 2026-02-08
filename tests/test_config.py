from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bombe.config import build_settings, resolve_repo_path


class ConfigTests(unittest.TestCase):
    def test_resolve_repo_path_rejects_missing_directory(self) -> None:
        with self.assertRaises(FileNotFoundError):
            resolve_repo_path(Path("/tmp/definitely-does-not-exist-bombe"))

    def test_build_settings_defaults_db_path_under_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            settings = build_settings(
                repo=repo,
                db_path=None,
                log_level="INFO",
                init_only=False,
                runtime_profile="default",
            )
            self.assertEqual(settings.repo_root, repo.resolve())
            self.assertEqual(settings.db_path, repo.resolve() / ".bombe" / "bombe.db")
            self.assertEqual(settings.log_level, "INFO")
            self.assertFalse(settings.init_only)
            self.assertEqual(settings.runtime_profile, "default")


if __name__ == "__main__":
    unittest.main()
