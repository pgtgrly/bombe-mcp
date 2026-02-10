from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bombe.server import _filesystem_events_available, build_parser


class ServerCLITests(unittest.TestCase):
    def test_build_parser_defaults_to_serve_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.repo, Path("."))
        self.assertEqual(args.log_level, "INFO")
        self.assertEqual(str(args.runtime_profile), "default")
        self.assertEqual(int(args.diagnostics_limit), 50)
        self.assertIsNone(args.control_plane_url)
        self.assertEqual(list(args.include), [])
        self.assertEqual(list(args.exclude), [])
        doctor_args = parser.parse_args(["doctor"])
        diagnostics_args = parser.parse_args(["diagnostics", "--run-id", "run_1"])
        preflight_strict_args = parser.parse_args(["--runtime-profile", "strict", "preflight"])
        include_exclude_args = parser.parse_args(["--include", "src/*.py", "--exclude", "*test*", "index-full"])
        doctor_fix_args = parser.parse_args(["doctor", "--fix"])
        watch_args = parser.parse_args(["watch", "--max-cycles", "1"])
        watch_fs_args = parser.parse_args(["watch", "--watch-mode", "fs", "--max-cycles", "1"])
        workspace_init_args = parser.parse_args(["workspace-init", "--name", "demo-workspace"])
        workspace_status_args = parser.parse_args(["workspace-status"])
        workspace_index_args = parser.parse_args(["workspace-index-full", "--workers", "2"])
        inspect_args = parser.parse_args(["inspect-export", "--output", "ui/bundle.json"])
        self.assertEqual(doctor_args.command, "doctor")
        self.assertEqual(diagnostics_args.command, "diagnostics")
        self.assertEqual(str(diagnostics_args.run_id), "run_1")
        self.assertEqual(preflight_strict_args.command, "preflight")
        self.assertEqual(str(preflight_strict_args.runtime_profile), "strict")
        self.assertEqual(include_exclude_args.include, ["src/*.py"])
        self.assertEqual(include_exclude_args.exclude, ["*test*"])
        self.assertTrue(bool(doctor_fix_args.fix))
        self.assertEqual(watch_args.command, "watch")
        self.assertEqual(str(watch_fs_args.watch_mode), "fs")
        self.assertEqual(int(watch_args.max_cycles), 1)
        self.assertEqual(workspace_init_args.command, "workspace-init")
        self.assertEqual(str(workspace_init_args.name), "demo-workspace")
        self.assertEqual(workspace_status_args.command, "workspace-status")
        self.assertEqual(workspace_index_args.command, "workspace-index-full")
        self.assertEqual(int(workspace_index_args.workers), 2)
        self.assertEqual(inspect_args.command, "inspect-export")

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
            self.assertIn("run_id", full_payload)
            self.assertIn("diagnostics", full_payload)
            self.assertIn("indexing_telemetry", full_payload)
            self.assertIn("progress", full_payload)
            progress_points = [int(point["progress_pct"]) for point in full_payload["progress"]]
            self.assertEqual(progress_points, sorted(progress_points))
            self.assertIn("sync", full_payload)
            self.assertEqual(full_payload["sync"]["push"]["reason"], "pushed")

            preflight = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "preflight",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            preflight_payload = json.loads(preflight.stdout.strip())
            self.assertIn("status", preflight_payload)
            self.assertIn("checks", preflight_payload)
            self.assertEqual(str(preflight_payload["runtime_profile"]), "default")

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
            self.assertIn("indexing_diagnostics_summary", status_payload)
            self.assertIn("recent_indexing_diagnostics", status_payload)

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
            self.assertIn("fixes_applied", doctor_payload)
            self.assertIn("indexing_diagnostics_summary", doctor_payload)
            self.assertIn("recent_indexing_diagnostics", doctor_payload)

            doctor_fix = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "doctor",
                    "--fix",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            doctor_fix_payload = json.loads(doctor_fix.stdout.strip())
            self.assertIn("fixes_applied", doctor_fix_payload)
            self.assertGreaterEqual(len(doctor_fix_payload["fixes_applied"]), 1)

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
            self.assertIn("effective_watch_mode", watch_payload)

            diagnostics = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "diagnostics",
                    "--run-id",
                    str(full_payload["run_id"]),
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            diagnostics_payload = json.loads(diagnostics.stdout.strip())
            self.assertEqual(str(diagnostics_payload["filters"]["run_id"]), str(full_payload["run_id"]))
            self.assertIn("summary", diagnostics_payload)
            self.assertIn("diagnostics", diagnostics_payload)

            workspace_init = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "workspace-init",
                    "--name",
                    "demo-workspace",
                    "--root",
                    repo_root.as_posix(),
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            workspace_init_payload = json.loads(workspace_init.stdout.strip())
            self.assertEqual(str(workspace_init_payload["workspace_name"]), "demo-workspace")
            self.assertGreaterEqual(len(workspace_init_payload["roots"]), 1)

            workspace_status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "workspace-status",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            workspace_status_payload = json.loads(workspace_status.stdout.strip())
            self.assertIn("roots", workspace_status_payload)
            self.assertIn("totals", workspace_status_payload)
            self.assertGreaterEqual(int(workspace_status_payload["totals"]["roots"]), 1)

            workspace_index = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "workspace-index-full",
                    "--workers",
                    "2",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            workspace_index_payload = json.loads(workspace_index.stdout.strip())
            self.assertIn("totals", workspace_index_payload)
            self.assertGreaterEqual(int(workspace_index_payload["totals"]["roots_indexed"]), 1)

            inspect_export = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "inspect-export",
                    "--output",
                    "ui/bundle.json",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            inspect_payload = json.loads(inspect_export.stdout.strip())
            self.assertIn("output", inspect_payload)
            self.assertGreaterEqual(int(inspect_payload["node_count"]), 1)

            watch_fs = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bombe.server",
                    "--repo",
                    repo_root.as_posix(),
                    "--log-level",
                    "ERROR",
                    "watch",
                    "--watch-mode",
                    "fs",
                    "--max-cycles",
                    "1",
                    "--poll-interval-ms",
                    "100",
                ],
                cwd=project_root.as_posix(),
                capture_output=True,
                text=True,
                env=env,
            )
            if _filesystem_events_available():
                self.assertEqual(watch_fs.returncode, 0)
                watch_fs_payload = json.loads(watch_fs.stdout.strip())
                self.assertEqual(str(watch_fs_payload["effective_watch_mode"]), "fs")
            else:
                self.assertNotEqual(watch_fs.returncode, 0)
                self.assertIn("watchdog filesystem events are unavailable", watch_fs.stderr)


if __name__ == "__main__":
    unittest.main()
