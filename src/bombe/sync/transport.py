"""File-backed control-plane transport for local hybrid sync testing."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from dataclasses import asdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bombe.models import ArtifactBundle, EdgeContractRecord, IndexDelta, SymbolKey
from bombe.sync.client import build_artifact_checksum, build_artifact_signature
from bombe.sync.reconcile import promote_delta


def _safe_repo_key(repo_id: str) -> str:
    return hashlib.sha256(repo_id.encode("utf-8")).hexdigest()[:24]


def _safe_snapshot(snapshot_id: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in snapshot_id)
    return sanitized or "snapshot"


def _artifact_from_dict(payload: dict[str, object]) -> ArtifactBundle:
    symbol_keys = [
        SymbolKey(
            qualified_name=str(item.get("qualified_name", "")),
            file_path=str(item.get("file_path", "")),
            start_line=int(item.get("start_line", 0)),
            end_line=int(item.get("end_line", 0)),
            signature_hash=str(item.get("signature_hash", "")),
        )
        for item in payload.get("promoted_symbols", [])
        if isinstance(item, dict)
    ]
    promoted_edges: list[EdgeContractRecord] = []
    for item in payload.get("promoted_edges", []):
        if not isinstance(item, dict):
            continue
        source_raw = item.get("source")
        target_raw = item.get("target")
        if not isinstance(source_raw, dict) or not isinstance(target_raw, dict):
            continue
        source = SymbolKey(
            qualified_name=str(source_raw.get("qualified_name", "")),
            file_path=str(source_raw.get("file_path", "")),
            start_line=int(source_raw.get("start_line", 0)),
            end_line=int(source_raw.get("end_line", 0)),
            signature_hash=str(source_raw.get("signature_hash", "")),
        )
        target = SymbolKey(
            qualified_name=str(target_raw.get("qualified_name", "")),
            file_path=str(target_raw.get("file_path", "")),
            start_line=int(target_raw.get("start_line", 0)),
            end_line=int(target_raw.get("end_line", 0)),
            signature_hash=str(target_raw.get("signature_hash", "")),
        )
        promoted_edges.append(
            EdgeContractRecord(
                source=source,
                target=target,
                relationship=str(item.get("relationship", "")),
                line_number=int(item.get("line_number", 0)),
                confidence=float(item.get("confidence", 1.0)),
                provenance=str(item.get("provenance", "local")),
            )
        )

    impact_priors = payload.get("impact_priors", [])
    flow_hints = payload.get("flow_hints", [])
    return ArtifactBundle(
        artifact_id=str(payload.get("artifact_id", "")),
        repo_id=str(payload.get("repo_id", "")),
        snapshot_id=str(payload.get("snapshot_id", "")),
        parent_snapshot=payload.get("parent_snapshot"),
        tool_version=str(payload.get("tool_version", "")),
        schema_version=int(payload.get("schema_version", 0)),
        created_at_utc=str(payload.get("created_at_utc", "")),
        promoted_symbols=symbol_keys,
        promoted_edges=promoted_edges,
        impact_priors=impact_priors if isinstance(impact_priors, list) else [],
        flow_hints=flow_hints if isinstance(flow_hints, list) else [],
        signature_algo=payload.get("signature_algo"),
        signing_key_id=payload.get("signing_key_id"),
        checksum=payload.get("checksum"),
        signature=payload.get("signature"),
    )


class FileControlPlaneTransport:
    def __init__(
        self,
        root: Path,
        signing_key: str | None = None,
        signing_algorithm: str | None = None,
        signing_key_id: str | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.signing_key = signing_key or os.getenv("BOMBE_SYNC_SIGNING_KEY")
        self.signing_algorithm = (
            signing_algorithm
            or os.getenv("BOMBE_SYNC_SIGNING_ALGO", "hmac-sha256")
        ).strip().lower()
        self.signing_key_id = (
            signing_key_id
            or os.getenv("BOMBE_SYNC_KEY_ID", "local")
        ).strip()

    def _repo_delta_dir(self, repo_id: str) -> Path:
        directory = self.root / "deltas" / _safe_repo_key(repo_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _repo_artifact_dir(self, repo_id: str) -> Path:
        directory = self.root / "artifacts" / _safe_repo_key(repo_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def push_delta(self, delta: IndexDelta) -> bool | dict[str, object]:
        repo_delta_dir = self._repo_delta_dir(delta.header.repo_id)
        snapshot_key = _safe_snapshot(delta.header.local_snapshot)
        delta_path = repo_delta_dir / f"{snapshot_key}.json"
        delta_path.write_text(json.dumps(asdict(delta), sort_keys=True), encoding="utf-8")

        promoted = promote_delta(
            delta,
            artifact_id=f"artifact-{snapshot_key}",
            snapshot_id=delta.header.local_snapshot,
        )
        artifact_promoted = False
        if promoted.promoted and promoted.artifact is not None:
            artifact_promoted = True
            artifact_dir = self._repo_artifact_dir(delta.header.repo_id)
            artifact = promoted.artifact
            if self.signing_key:
                artifact = replace(
                    artifact,
                    signature_algo=self.signing_algorithm,
                    signing_key_id=self.signing_key_id,
                )
                artifact = replace(
                    artifact,
                    checksum=build_artifact_checksum(artifact),
                )
                artifact = replace(
                    artifact,
                    signature=build_artifact_signature(
                        artifact,
                        self.signing_key,
                        algorithm=self.signing_algorithm,
                    ),
                )
            artifact_payload = asdict(artifact)
            artifact_path = artifact_dir / f"{promoted.artifact.artifact_id}.json"
            latest_path = artifact_dir / "latest.json"
            artifact_path.write_text(json.dumps(artifact_payload, sort_keys=True), encoding="utf-8")
            latest_path.write_text(json.dumps(artifact_payload, sort_keys=True), encoding="utf-8")

        return {
            "accepted": True,
            "delta_path": delta_path.as_posix(),
            "artifact_promoted": artifact_promoted,
        }

    def pull_latest_artifact(
        self,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> ArtifactBundle | None:
        del snapshot_id, parent_snapshot
        artifact_dir = self._repo_artifact_dir(repo_id)
        latest_path = artifact_dir / "latest.json"
        if not latest_path.exists():
            return None
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return _artifact_from_dict(payload)


class HttpControlPlaneTransport:
    def __init__(
        self,
        base_url: str,
        auth_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token or os.getenv("BOMBE_CONTROL_PLANE_TOKEN")

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object] | None]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if payload is not None:
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
        request = Request(url=url, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                if not body.strip():
                    return response.status, None
                parsed = json.loads(body)
                return response.status, parsed if isinstance(parsed, dict) else None
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")
                parsed = json.loads(body)
                return int(exc.code), parsed if isinstance(parsed, dict) else None
            except Exception:
                return int(exc.code), None
        except URLError:
            return 503, None

    def push_delta(self, delta: IndexDelta) -> bool | dict[str, object]:
        status, payload = self._request_json(
            "POST",
            "/v1/deltas",
            payload={"delta": asdict(delta)},
        )
        if status >= 400:
            return {"accepted": False, "error_status": status}
        if payload is None:
            return {"accepted": True}
        return payload

    def pull_latest_artifact(
        self,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> ArtifactBundle | None:
        params = {
            "repo_id": repo_id,
            "snapshot_id": snapshot_id,
        }
        if parent_snapshot:
            params["parent_snapshot"] = parent_snapshot
        query = urlencode(params)
        status, payload = self._request_json("GET", f"/v1/artifacts/latest?{query}", payload=None)
        if status == 404 or payload is None:
            return None
        artifact_raw = payload.get("artifact")
        if not isinstance(artifact_raw, dict):
            return None
        return _artifact_from_dict(artifact_raw)
