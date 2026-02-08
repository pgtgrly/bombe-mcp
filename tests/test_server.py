from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bombe.server import build_parser


class ServerCLITests(unittest.TestCase):
    def test_build_parser_defaults_to_serve_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.repo, Path("."))
        self.assertEqual(args.log_level, "INFO")
        doctor_args = parser.parse_args(["doctor"])
        watch_args = parser.parse_args(["watch", "--max-cycles", "1"])
        self.assertEqual(doctor_args.command, "doctor")
        self.assertEqual(watch_args.command, "watch")
        self.assertEqual(int(watch_args.max_cycles), 1)

    def test_index_and_status_commands_emit_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "main.py").write_text(
                "def run():\n    return 1\n",
                encoding="utf-8",
            )

            project_root = Path(__file__).resolve().parents[1]
            env = os.environ.copy()
            env["PYTHONPATH"] = str(project_root / "src")

            full = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "--hybrid-sync",
                    "index-full",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            full_payload = json.loads(full.stdout.strip())
            self.assertEqual(full_payload["mode"], "full")
            self.assertGreaterEqual(int(full_payload["files_indexed"]), 1)
            self.assertIn("sync", full_payload)
            self.assertEqual(full_payload["sync"]["push"]["reason"], "pushed")

            incremental = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "--hybrid-sync",
                    "index-incremental",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            inc_payload = json.loads(incremental.stdout.strip())
            self.assertEqual(inc_payload["mode"], "incremental")
            self.assertIn("changed_files", inc_payload)
            self.assertIn("sync", inc_payload)

            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "status",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            status_payload = json.loads(status.stdout.strip())
            self.assertIn("counts", status_payload)
            self.assertGreaterEqual(int(status_payload["counts"]["files"]), 1)
            self.assertGreaterEqual(int(status_payload["counts"]["artifact_pins"]), 1)

            doctor = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "doctor",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            doctor_payload = json.loads(doctor.stdout.strip())
            self.assertIn("status", doctor_payload)
            self.assertIn("checks", doctor_payload)
            self.assertGreaterEqual(len(doctor_payload["checks"]), 1)

            watch = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "watch",
                    "--max-cycles",
                    "1",
                    "--poll-interval-ms",
                    "100",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            watch_payload = json.loads(watch.stdout.strip())
            self.assertEqual(watch_payload["mode"], "watch")
            self.assertEqual(int(watch_payload["cycles"]), 1)


if __name__ == "__main__":
    unittest.main()
