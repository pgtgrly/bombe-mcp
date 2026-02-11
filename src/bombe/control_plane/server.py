"""Reference HTTP control-plane server for artifact exchange."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bombe.models import (
    DeltaHeader,
    EdgeContractRecord,
    FileDelta,
    IndexDelta,
    ParameterRecord,
    QualityStats,
    SymbolKey,
    SymbolRecord,
)
from bombe.sync.transport import FileControlPlaneTransport


def _symbol_key_from_payload(payload: dict[str, Any]) -> SymbolKey:
    return SymbolKey(
        qualified_name=str(payload.get("qualified_name", "")),
        file_path=str(payload.get("file_path", "")),
        start_line=int(payload.get("start_line", 0)),
        end_line=int(payload.get("end_line", 0)),
        signature_hash=str(payload.get("signature_hash", "")),
    )


def _edge_record_from_payload(payload: dict[str, Any]) -> EdgeContractRecord:
    source_raw = payload.get("source", {})
    target_raw = payload.get("target", {})
    if not isinstance(source_raw, dict) or not isinstance(target_raw, dict):
        raise ValueError("edge payload requires source and target objects")
    return EdgeContractRecord(
        source=_symbol_key_from_payload(source_raw),
        target=_symbol_key_from_payload(target_raw),
        relationship=str(payload.get("relationship", "")),
        line_number=int(payload.get("line_number", 0)),
        confidence=float(payload.get("confidence", 1.0)),
        provenance=str(payload.get("provenance", "local")),
    )


def _symbol_record_from_payload(payload: dict[str, Any]) -> SymbolRecord:
    params_raw = payload.get("parameters", [])
    params: list[ParameterRecord] = []
    for item in params_raw if isinstance(params_raw, list) else []:
        if not isinstance(item, dict):
            continue
        params.append(
            ParameterRecord(
                name=str(item.get("name", "")),
                position=int(item.get("position", 0)),
                type_=item.get("type"),
                default_value=item.get("default_value"),
            )
        )
    return SymbolRecord(
        name=str(payload.get("name", "")),
        qualified_name=str(payload.get("qualified_name", "")),
        kind=str(payload.get("kind", "")),
        file_path=str(payload.get("file_path", "")),
        start_line=int(payload.get("start_line", 0)),
        end_line=int(payload.get("end_line", 0)),
        signature=payload.get("signature"),
        return_type=payload.get("return_type"),
        visibility=payload.get("visibility"),
        is_async=bool(payload.get("is_async", False)),
        is_static=bool(payload.get("is_static", False)),
        parent_symbol_id=payload.get("parent_symbol_id"),
        docstring=payload.get("docstring"),
        pagerank_score=float(payload.get("pagerank_score", 0.0)),
        parameters=params,
    )


def _index_delta_from_payload(payload: dict[str, Any]) -> IndexDelta:
    header_raw = payload.get("header", {})
    if not isinstance(header_raw, dict):
        raise ValueError("delta.header must be an object")
    header = DeltaHeader(
        repo_id=str(header_raw.get("repo_id", "")),
        parent_snapshot=header_raw.get("parent_snapshot"),
        local_snapshot=str(header_raw.get("local_snapshot", "")),
        tool_version=str(header_raw.get("tool_version", "")),
        schema_version=int(header_raw.get("schema_version", 0)),
        created_at_utc=str(header_raw.get("created_at_utc", "")),
    )
    file_changes_raw = payload.get("file_changes", [])
    file_changes: list[FileDelta] = []
    for item in file_changes_raw if isinstance(file_changes_raw, list) else []:
        if not isinstance(item, dict):
            continue
        file_changes.append(
            FileDelta(
                status=str(item.get("status", "")),
                path=str(item.get("path", "")),
                old_path=item.get("old_path"),
                content_hash=item.get("content_hash"),
                size_bytes=item.get("size_bytes"),
            )
        )
    symbol_upserts_raw = payload.get("symbol_upserts", [])
    symbol_upserts = [
        _symbol_record_from_payload(item)
        for item in symbol_upserts_raw
        if isinstance(item, dict)
    ]
    symbol_deletes_raw = payload.get("symbol_deletes", [])
    symbol_deletes = [
        _symbol_key_from_payload(item)
        for item in symbol_deletes_raw
        if isinstance(item, dict)
    ]
    edge_upserts_raw = payload.get("edge_upserts", [])
    edge_upserts = [
        _edge_record_from_payload(item)
        for item in edge_upserts_raw
        if isinstance(item, dict)
    ]
    edge_deletes_raw = payload.get("edge_deletes", [])
    edge_deletes = [
        _edge_record_from_payload(item)
        for item in edge_deletes_raw
        if isinstance(item, dict)
    ]
    quality_raw = payload.get("quality_stats", {})
    quality = (
        QualityStats(
            ambiguity_rate=float(quality_raw.get("ambiguity_rate", 0.0)),
            unresolved_imports=int(quality_raw.get("unresolved_imports", 0)),
            parse_failures=int(quality_raw.get("parse_failures", 0)),
        )
        if isinstance(quality_raw, dict)
        else QualityStats()
    )
    return IndexDelta(
        header=header,
        file_changes=file_changes,
        symbol_upserts=symbol_upserts,
        symbol_deletes=symbol_deletes,
        edge_upserts=edge_upserts,
        edge_deletes=edge_deletes,
        quality_stats=quality,
    )


class ReferenceControlPlaneServer:
    def __init__(
        self,
        root: Path,
        host: str = "127.0.0.1",
        port: int = 0,
        auth_token: str | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.port = int(port)
        self.auth_token = auth_token
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._transport = FileControlPlaneTransport(self.root)

    def push_delta_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        delta = _index_delta_from_payload(payload)
        result = self._transport.push_delta(delta)
        if isinstance(result, dict):
            return result
        return {"accepted": bool(result)}

    def pull_latest_artifact_payload(
        self,
        repo_id: str,
        snapshot_id: str,
        parent_snapshot: str | None,
    ) -> dict[str, Any] | None:
        artifact = self._transport.pull_latest_artifact(
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            parent_snapshot=parent_snapshot,
        )
        if artifact is None:
            return None
        from bombe.models import model_to_dict
        return model_to_dict(artifact)

    def start(self) -> str:
        if self._httpd is not None:
            return self.url
        handler = self._build_handler()
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            return f"http://{self.host}:{self.port}"
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.auth_token:
            return True
        auth = handler.headers.get("Authorization", "")
        return auth.strip() == f"Bearer {self.auth_token}"

    def _build_handler(self):
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def _json(self, status: int, payload: dict[str, Any]) -> None:
                encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self) -> None:  # noqa: N802
                if not server._authorized(self):
                    self._json(401, {"error": "unauthorized"})
                    return
                parsed = urlparse(self.path)
                if parsed.path != "/v1/deltas":
                    self._json(404, {"error": "not_found"})
                    return
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(max(0, content_length))
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    self._json(400, {"error": "invalid_json"})
                    return
                if not isinstance(payload, dict):
                    self._json(400, {"error": "invalid_payload"})
                    return
                delta_raw = payload.get("delta")
                if not isinstance(delta_raw, dict):
                    self._json(400, {"error": "missing_delta"})
                    return
                try:
                    response = server.push_delta_payload(delta_raw)
                except Exception as exc:
                    self._json(400, {"error": f"invalid_delta:{exc}"})
                    return
                self._json(200, response)

            def do_GET(self) -> None:  # noqa: N802
                if not server._authorized(self):
                    self._json(401, {"error": "unauthorized"})
                    return
                parsed = urlparse(self.path)
                if parsed.path != "/v1/artifacts/latest":
                    self._json(404, {"error": "not_found"})
                    return
                query = parse_qs(parsed.query)
                repo_id = query.get("repo_id", [""])[0]
                snapshot_id = query.get("snapshot_id", [""])[0]
                parent_snapshot = query.get("parent_snapshot", [None])[0]
                if not repo_id or not snapshot_id:
                    self._json(400, {"error": "missing_query_params"})
                    return
                artifact_payload = server.pull_latest_artifact_payload(
                    repo_id=repo_id,
                    snapshot_id=snapshot_id,
                    parent_snapshot=parent_snapshot,
                )
                if artifact_payload is None:
                    self._json(404, {"error": "artifact_not_found"})
                    return
                self._json(200, {"artifact": artifact_payload})

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        return _Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bombe-control-plane",
        description="Reference control-plane server for Bombe artifacts.",
    )
    parser.add_argument("--root", type=Path, required=True, help="Storage root for deltas and artifacts.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8085, help="Bind port.")
    parser.add_argument("--auth-token", type=str, default=None, help="Optional bearer token.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    server = ReferenceControlPlaneServer(
        root=args.root,
        host=str(args.host),
        port=int(args.port),
        auth_token=args.auth_token,
    )
    url = server.start()
    print(json.dumps({"status": "running", "url": url, "root": server.root.as_posix()}, sort_keys=True))
    try:
        while True:
            threading.Event().wait(1.0)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
