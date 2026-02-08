from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bombe.models import DeltaHeader, IndexDelta, SymbolRecord
from bombe.sync.client import validate_artifact_checksum, validate_artifact_signature
from bombe.sync.transport import FileControlPlaneTransport


def _delta_with_symbol(repo_id: str = "repo") -> IndexDelta:
    header = DeltaHeader(
        repo_id=repo_id,
        parent_snapshot="snap_0",
        local_snapshot="snap_1",
        tool_version="0.1.0",
        schema_version=1,
        created_at_utc="2026-02-08T00:00:00Z",
    )
    symbol = SymbolRecord(
        name="run",
        qualified_name="pkg.run",
        kind="function",
        file_path="src/main.py",
        start_line=1,
        end_line=2,
        signature="def run()",
    )
    return IndexDelta(header=header, symbol_upserts=[symbol])


class SyncTransportTests(unittest.TestCase):
    def test_transport_signs_promoted_artifact_when_key_is_configured(self) -> None:
        with TemporaryDirectory() as tmpdir:
            transport = FileControlPlaneTransport(Path(tmpdir), signing_key="secret")
            result = transport.push_delta(_delta_with_symbol(repo_id="repo"))
            self.assertTrue(bool(result))
            artifact = transport.pull_latest_artifact("repo", "snap_1", "snap_0")
            self.assertIsNotNone(artifact)
            assert artifact is not None
            self.assertIsNotNone(artifact.signature)
            self.assertEqual(artifact.signature_algo, "hmac-sha256")
            self.assertEqual(artifact.signing_key_id, "local")
            self.assertTrue(validate_artifact_checksum(artifact))
            self.assertTrue(validate_artifact_signature(artifact, "secret"))


if __name__ == "__main__":
    unittest.main()
