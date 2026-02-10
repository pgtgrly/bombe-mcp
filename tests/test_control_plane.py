from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bombe.control_plane import ReferenceControlPlaneServer
from bombe.models import DeltaHeader, FileDelta, IndexDelta, QualityStats, SymbolRecord
from bombe.sync.transport import HttpControlPlaneTransport


class ReferenceControlPlaneTests(unittest.TestCase):
    def test_reference_server_payload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "control-plane"
            server = ReferenceControlPlaneServer(root=root, host="127.0.0.1", port=0)
            delta = IndexDelta(
                header=DeltaHeader(
                    repo_id="repo://demo",
                    parent_snapshot=None,
                    local_snapshot="snap-1",
                    tool_version="0.1.0",
                    schema_version=1,
                    created_at_utc=datetime.now(timezone.utc).isoformat(),
                ),
                file_changes=[FileDelta(status="M", path="src/main.py")],
                symbol_upserts=[
                    SymbolRecord(
                        name="run",
                        qualified_name="app.main.run",
                        kind="function",
                        file_path="src/main.py",
                        start_line=1,
                        end_line=2,
                        signature="def run()",
                    )
                ],
                quality_stats=QualityStats(),
            )
            push_result = server.push_delta_payload(
                {
                    "header": {
                        "repo_id": delta.header.repo_id,
                        "parent_snapshot": delta.header.parent_snapshot,
                        "local_snapshot": delta.header.local_snapshot,
                        "tool_version": delta.header.tool_version,
                        "schema_version": delta.header.schema_version,
                        "created_at_utc": delta.header.created_at_utc,
                    },
                    "file_changes": [
                        {
                            "status": "M",
                            "path": "src/main.py",
                            "old_path": None,
                            "content_hash": None,
                            "size_bytes": None,
                        }
                    ],
                    "symbol_upserts": [
                        {
                            "name": "run",
                            "qualified_name": "app.main.run",
                            "kind": "function",
                            "file_path": "src/main.py",
                            "start_line": 1,
                            "end_line": 2,
                            "signature": "def run()",
                            "parameters": [],
                        }
                    ],
                    "symbol_deletes": [],
                    "edge_upserts": [],
                    "edge_deletes": [],
                    "quality_stats": {
                        "ambiguity_rate": 0.0,
                        "unresolved_imports": 0,
                        "parse_failures": 0,
                    },
                }
            )
            self.assertTrue(bool(push_result.get("accepted", False)))
            artifact_payload = server.pull_latest_artifact_payload(
                repo_id="repo://demo",
                snapshot_id="snap-1",
                parent_snapshot=None,
            )
            self.assertIsNotNone(artifact_payload)
            assert artifact_payload is not None
            self.assertEqual(str(artifact_payload["repo_id"]), "repo://demo")

    def test_http_transport_parses_artifact_payload(self) -> None:
        transport = HttpControlPlaneTransport("http://127.0.0.1:8085")

        def _fake_request(method: str, path: str, payload=None):
            del method, path, payload
            return (
                200,
                {
                    "artifact": {
                        "artifact_id": "artifact-s1",
                        "repo_id": "repo://demo",
                        "snapshot_id": "s1",
                        "parent_snapshot": None,
                        "tool_version": "0.1.0",
                        "schema_version": 1,
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "promoted_symbols": [],
                        "promoted_edges": [],
                        "impact_priors": [],
                        "flow_hints": [],
                        "signature_algo": None,
                        "signing_key_id": None,
                        "checksum": None,
                        "signature": None,
                    }
                },
            )

        transport._request_json = _fake_request  # type: ignore[assignment]
        artifact = transport.pull_latest_artifact("repo://demo", "s1", None)
        self.assertIsNotNone(artifact)
        self.assertEqual(str(artifact.artifact_id), "artifact-s1")


if __name__ == "__main__":
    unittest.main()
