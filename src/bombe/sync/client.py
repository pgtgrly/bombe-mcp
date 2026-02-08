"""Hybrid sync client for local->control-plane delta exchange."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from bombe.models import (
    ARTIFACT_SCHEMA_VERSION,
    DELTA_SCHEMA_VERSION,
    ArtifactBundle,
    IndexDelta,
)


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    mode: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PullResult:
    artifact: ArtifactBundle | None
    mode: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuarantineRecord:
    artifact_id: str
    reason: str
    quarantined_at_utc: str


class ControlPlaneTransport(Protocol):
    def push_delta(self, delta: IndexDelta) -> bool | dict[str, Any]:
        ...

    def pull_latest_artifact(
        self,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> ArtifactBundle | None:
        ...


def build_artifact_checksum(artifact: ArtifactBundle) -> str:
    payload = asdict(artifact)
    payload.pop("checksum", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_artifact_checksum(artifact: ArtifactBundle) -> bool:
    if not artifact.checksum:
        return False
    return artifact.checksum == build_artifact_checksum(artifact)


class ArtifactQuarantineStore:
    def __init__(self) -> None:
        self._records: dict[str, QuarantineRecord] = {}
        self._lock = threading.Lock()

    def add(self, artifact_id: str, reason: str) -> None:
        with self._lock:
            self._records[artifact_id] = QuarantineRecord(
                artifact_id=artifact_id,
                reason=reason,
                quarantined_at_utc=datetime.now(timezone.utc).isoformat(),
            )

    def is_quarantined(self, artifact_id: str) -> bool:
        with self._lock:
            return artifact_id in self._records

    def records(self) -> list[QuarantineRecord]:
        with self._lock:
            return list(self._records.values())


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_timeout_seconds: float = 10.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout_seconds = max(0.01, reset_timeout_seconds)
        self._failure_count = 0
        self._opened_at_monotonic = 0.0
        self._state = "closed"
        self._lock = threading.Lock()

    def state(self) -> str:
        with self._lock:
            return self._state

    def allow_request(self) -> bool:
        with self._lock:
            if self._state != "open":
                return True
            elapsed = time.monotonic() - self._opened_at_monotonic
            if elapsed >= self.reset_timeout_seconds:
                self._state = "half_open"
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._opened_at_monotonic = 0.0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == "half_open":
                self._failure_count = self.failure_threshold
            else:
                self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = "open"
                self._opened_at_monotonic = time.monotonic()


class CompatibilityPolicy:
    def __init__(
        self,
        tool_version: str,
        delta_schema_version: int = DELTA_SCHEMA_VERSION,
        artifact_schema_version: int = ARTIFACT_SCHEMA_VERSION,
    ) -> None:
        self.tool_version = tool_version
        self.delta_schema_version = delta_schema_version
        self.artifact_schema_version = artifact_schema_version

    def _major(self, version: str) -> str:
        return version.split(".", 1)[0]

    def evaluate_delta(self, delta: IndexDelta) -> tuple[bool, str]:
        if delta.header.schema_version != self.delta_schema_version:
            return False, "delta_schema_mismatch"
        if self._major(delta.header.tool_version) != self._major(self.tool_version):
            return False, "delta_tool_mismatch"
        return True, "ok"

    def evaluate_artifact(
        self,
        artifact: ArtifactBundle,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> tuple[bool, str]:
        if artifact.repo_id != repo_id:
            return False, "repo_mismatch"
        if artifact.schema_version != self.artifact_schema_version:
            return False, "artifact_schema_mismatch"
        if self._major(artifact.tool_version) != self._major(self.tool_version):
            return False, "artifact_tool_mismatch"

        allowed_lineage = {snapshot_id}
        if parent_snapshot:
            allowed_lineage.add(parent_snapshot)
        if artifact.snapshot_id in allowed_lineage:
            return True, "ok"
        if artifact.parent_snapshot and artifact.parent_snapshot in allowed_lineage:
            return True, "ok"
        return False, "lineage_mismatch"


class SyncClient:
    def __init__(
        self,
        transport: ControlPlaneTransport,
        policy: CompatibilityPolicy,
        timeout_seconds: float = 0.5,
        circuit_breaker: CircuitBreaker | None = None,
        quarantine_store: ArtifactQuarantineStore | None = None,
    ) -> None:
        self.transport = transport
        self.policy = policy
        self.timeout_seconds = max(0.01, timeout_seconds)
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.quarantine_store = quarantine_store or ArtifactQuarantineStore()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bombe-sync")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def push_delta(self, delta: IndexDelta) -> SyncResult:
        compatible, reason = self.policy.evaluate_delta(delta)
        if not compatible:
            return SyncResult(
                ok=False,
                mode="local_fallback",
                reason=reason,
                detail={"delta_snapshot": delta.header.local_snapshot},
            )
        if not self.circuit_breaker.allow_request():
            return SyncResult(ok=False, mode="local_fallback", reason="circuit_open")

        future = self._executor.submit(self.transport.push_delta, delta)
        try:
            result = future.result(timeout=self.timeout_seconds)
        except TimeoutError:
            self.circuit_breaker.record_failure()
            future.cancel()
            return SyncResult(ok=False, mode="local_fallback", reason="push_timeout")
        except Exception as exc:
            self.circuit_breaker.record_failure()
            return SyncResult(
                ok=False,
                mode="local_fallback",
                reason="push_error",
                detail={"error": str(exc)},
            )

        accepted = False
        detail: dict[str, Any] = {}
        if isinstance(result, dict):
            accepted = bool(result.get("accepted", False))
            detail = result
        else:
            accepted = bool(result)

        if accepted:
            self.circuit_breaker.record_success()
            return SyncResult(ok=True, mode="hybrid", reason="pushed", detail=detail)

        self.circuit_breaker.record_failure()
        return SyncResult(ok=False, mode="local_fallback", reason="push_rejected", detail=detail)

    def pull_artifact(
        self,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> PullResult:
        if not self.circuit_breaker.allow_request():
            return PullResult(artifact=None, mode="local_fallback", reason="circuit_open")

        future = self._executor.submit(
            self.transport.pull_latest_artifact,
            repo_id,
            snapshot_id,
            parent_snapshot,
        )
        try:
            artifact = future.result(timeout=self.timeout_seconds)
        except TimeoutError:
            self.circuit_breaker.record_failure()
            future.cancel()
            return PullResult(artifact=None, mode="local_fallback", reason="pull_timeout")
        except Exception as exc:
            self.circuit_breaker.record_failure()
            return PullResult(
                artifact=None,
                mode="local_fallback",
                reason="pull_error",
                detail={"error": str(exc)},
            )

        if artifact is None:
            self.circuit_breaker.record_success()
            return PullResult(artifact=None, mode="local_fallback", reason="no_artifact")

        if self.quarantine_store.is_quarantined(artifact.artifact_id):
            return PullResult(
                artifact=None,
                mode="local_fallback",
                reason="artifact_quarantined",
                detail={"artifact_id": artifact.artifact_id},
            )

        compatible, reason = self.policy.evaluate_artifact(
            artifact=artifact,
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            parent_snapshot=parent_snapshot,
        )
        if not compatible:
            self.circuit_breaker.record_success()
            return PullResult(artifact=None, mode="local_fallback", reason=reason)

        if not validate_artifact_checksum(artifact):
            self.circuit_breaker.record_failure()
            self.quarantine_store.add(artifact.artifact_id, "checksum_mismatch")
            return PullResult(
                artifact=None,
                mode="local_fallback",
                reason="checksum_mismatch",
                detail={"artifact_id": artifact.artifact_id},
            )

        self.circuit_breaker.record_success()
        return PullResult(artifact=artifact, mode="remote_artifact", reason="pulled")
