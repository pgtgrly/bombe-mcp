from __future__ import annotations

import time
import unittest
from dataclasses import replace

from bombe.models import ARTIFACT_SCHEMA_VERSION, DeltaHeader, IndexDelta, SymbolKey
from bombe.models import ArtifactBundle
from bombe.sync.client import (
    ArtifactQuarantineStore,
    CircuitBreaker,
    CompatibilityPolicy,
    SyncClient,
    build_artifact_checksum,
    build_artifact_signature,
)


def _build_delta() -> IndexDelta:
    header = DeltaHeader(
        repo_id="repo",
        parent_snapshot="snap_0",
        local_snapshot="snap_1",
        tool_version="0.1.0",
        schema_version=1,
        created_at_utc="2026-02-08T00:00:00Z",
    )
    return IndexDelta(header=header)


def _build_artifact(
    *,
    repo_id: str = "repo",
    snapshot_id: str = "snap_1",
    parent_snapshot: str | None = "snap_0",
    schema_version: int = ARTIFACT_SCHEMA_VERSION,
    tool_version: str = "0.1.0",
    with_checksum: bool = True,
    signing_key: str | None = None,
) -> ArtifactBundle:
    symbol_key = SymbolKey.from_fields(
        qualified_name="pkg.run",
        file_path="src/main.py",
        start_line=1,
        end_line=2,
        signature="def run()",
    )
    artifact = ArtifactBundle(
        artifact_id="artifact_1",
        repo_id=repo_id,
        snapshot_id=snapshot_id,
        parent_snapshot=parent_snapshot,
        tool_version=tool_version,
        schema_version=schema_version,
        created_at_utc="2026-02-08T00:01:00Z",
        promoted_symbols=[symbol_key],
    )
    if with_checksum:
        artifact = replace(artifact, checksum=build_artifact_checksum(artifact))
    if signing_key:
        artifact = replace(artifact, signature=build_artifact_signature(artifact, signing_key))
    return artifact


class _FakeTransport:
    def __init__(
        self,
        *,
        push_result: bool | dict[str, object] = True,
        artifact: ArtifactBundle | None = None,
        push_delay_s: float = 0.0,
        pull_delay_s: float = 0.0,
        push_error: Exception | None = None,
        pull_error: Exception | None = None,
    ) -> None:
        self.push_result = push_result
        self.artifact = artifact
        self.push_delay_s = push_delay_s
        self.pull_delay_s = pull_delay_s
        self.push_error = push_error
        self.pull_error = pull_error

    def push_delta(self, delta: IndexDelta) -> bool | dict[str, object]:
        if self.push_delay_s > 0:
            time.sleep(self.push_delay_s)
        if self.push_error is not None:
            raise self.push_error
        _ = delta
        return self.push_result

    def pull_latest_artifact(
        self,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> ArtifactBundle | None:
        if self.pull_delay_s > 0:
            time.sleep(self.pull_delay_s)
        if self.pull_error is not None:
            raise self.pull_error
        _ = (repo_id, snapshot_id, parent_snapshot)
        return self.artifact


class SyncClientTests(unittest.TestCase):
    def test_push_delta_success(self) -> None:
        client = SyncClient(
            transport=_FakeTransport(push_result={"accepted": True, "queue_id": "q1"}),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
        )
        self.addCleanup(client.close)
        result = client.push_delta(_build_delta())
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "hybrid")
        self.assertEqual(result.reason, "pushed")
        self.assertEqual(result.detail.get("queue_id"), "q1")

    def test_push_delta_timeout_returns_local_fallback(self) -> None:
        client = SyncClient(
            transport=_FakeTransport(push_result=True, push_delay_s=0.05),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.01,
        )
        self.addCleanup(client.close)
        result = client.push_delta(_build_delta())
        self.assertFalse(result.ok)
        self.assertEqual(result.mode, "local_fallback")
        self.assertEqual(result.reason, "push_timeout")

    def test_pull_artifact_schema_mismatch_falls_back(self) -> None:
        artifact = _build_artifact(schema_version=99)
        client = SyncClient(
            transport=_FakeTransport(artifact=artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNone(result.artifact)
        self.assertEqual(result.mode, "local_fallback")
        self.assertEqual(result.reason, "artifact_schema_mismatch")

    def test_pull_artifact_checksum_mismatch_is_quarantined(self) -> None:
        clean_artifact = _build_artifact()
        bad_artifact = replace(clean_artifact, checksum="bad")
        quarantine = ArtifactQuarantineStore()
        client = SyncClient(
            transport=_FakeTransport(artifact=bad_artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
            quarantine_store=quarantine,
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNone(result.artifact)
        self.assertEqual(result.reason, "checksum_mismatch")
        self.assertTrue(quarantine.is_quarantined("artifact_1"))

    def test_pull_artifact_success(self) -> None:
        artifact = _build_artifact()
        client = SyncClient(
            transport=_FakeTransport(artifact=artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNotNone(result.artifact)
        self.assertEqual(result.mode, "remote_artifact")
        self.assertEqual(result.reason, "pulled")

    def test_pull_artifact_signature_mismatch_is_quarantined(self) -> None:
        artifact = _build_artifact(signing_key="correct-key")
        quarantine = ArtifactQuarantineStore()
        client = SyncClient(
            transport=_FakeTransport(artifact=artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
            quarantine_store=quarantine,
            signing_key="wrong-key",
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNone(result.artifact)
        self.assertEqual(result.reason, "signature_mismatch")
        self.assertTrue(quarantine.is_quarantined("artifact_1"))

    def test_pull_artifact_signature_match_succeeds(self) -> None:
        artifact = _build_artifact(signing_key="shared-key")
        client = SyncClient(
            transport=_FakeTransport(artifact=artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
            signing_key="shared-key",
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNotNone(result.artifact)
        self.assertEqual(result.reason, "pulled")

    def test_pull_artifact_untrusted_key_fails_for_ed25519(self) -> None:
        artifact = _build_artifact(
            with_checksum=True,
        )
        artifact = replace(
            artifact,
            signature_algo="ed25519",
            signing_key_id="team-key",
            signature="deadbeef",
            checksum=None,
        )
        artifact = replace(artifact, checksum=build_artifact_checksum(artifact))
        client = SyncClient(
            transport=_FakeTransport(artifact=artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
            trusted_verification_keys={},
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNone(result.artifact)
        self.assertEqual(result.reason, "signature_untrusted_key")

    def test_pull_artifact_ed25519_signature_with_trusted_key_succeeds(self) -> None:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except Exception:
            self.skipTest("cryptography is not installed")

        private_key = Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

        artifact = _build_artifact(with_checksum=True)
        artifact = replace(
            artifact,
            signature_algo="ed25519",
            signing_key_id="team-key",
            signature=None,
        )
        artifact = replace(
            artifact,
            signature=build_artifact_signature(
                artifact,
                private_bytes.hex(),
                algorithm="ed25519",
            ),
        )
        artifact = replace(artifact, checksum=build_artifact_checksum(artifact))

        client = SyncClient(
            transport=_FakeTransport(artifact=artifact),
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.2,
            signing_algorithm="ed25519",
            trusted_verification_keys={"team-key": public_bytes.hex()},
        )
        self.addCleanup(client.close)
        result = client.pull_artifact("repo", "snap_1", "snap_0")
        self.assertIsNotNone(result.artifact)
        self.assertEqual(result.reason, "pulled")

    def test_circuit_breaker_opens_and_recovers(self) -> None:
        transport = _FakeTransport(push_error=RuntimeError("unavailable"))
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=0.01)
        client = SyncClient(
            transport=transport,
            policy=CompatibilityPolicy(tool_version="0.1.0"),
            timeout_seconds=0.05,
            circuit_breaker=breaker,
        )
        self.addCleanup(client.close)

        first = client.push_delta(_build_delta())
        second = client.push_delta(_build_delta())
        blocked = client.push_delta(_build_delta())
        self.assertEqual(first.reason, "push_error")
        self.assertEqual(second.reason, "push_error")
        self.assertEqual(blocked.reason, "circuit_open")
        self.assertEqual(breaker.state(), "open")

        time.sleep(0.02)
        transport.push_error = None
        transport.push_result = True
        recovered = client.push_delta(_build_delta())
        self.assertTrue(recovered.ok)
        self.assertEqual(recovered.reason, "pushed")
        self.assertEqual(breaker.state(), "closed")


if __name__ == "__main__":
    unittest.main()
