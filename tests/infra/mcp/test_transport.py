"""Real SDK against an independent socket peer; all payloads are synthetic."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

import pytest

from app.infra.mcp.driver import McpDriver
from app.infra.mcp.transport import parse_json
from app.mcp.contracts import INPUT_SCHEMAS, SAFETY_ANNOTATIONS, input_digest, safety_digest
from app.mcp.models import McpFailure, ServiceConfig
from app.ports.mcp import McpAuthorizationContext


class Peer:
    def __init__(self, version: str, mode: str = "json", fault: str = "") -> None:
        self.version, self.mode, self.fault = version, mode, fault
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.effects = 0
        self.release = threading.Event()

    def reply(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request["method"]
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": self.version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-peer", "version": "1"},
            }
        elif method == "server/discover":
            result = {
                "supportedVersions": [self.version],
                "capabilities": {"tools": {}},
                "resultType": "complete",
                "ttlMs": 0,
                "cacheScope": "private",
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "business_context_get",
                        "inputSchema": INPUT_SCHEMAS["business_context_get"],
                        "annotations": dict(SAFETY_ANNOTATIONS["business_context_get"]),
                    }
                ]
            }
            if self.version == "2026-07-28":
                result.update(resultType="complete", ttlMs=0, cacheScope="private")
            if self.fault == "drift":
                result["tools"][0]["inputSchema"] = {"type": "string"}
        else:
            self.effects += 1
            result = {
                "structuredContent": {"synthetic": True},
                "content": [{"type": "text", "text": '{"synthetic":true}'}],
            }
            if self.version == "2026-07-28":
                result["resultType"] = "complete"
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}


@contextmanager
def serving(peer: Peer) -> Iterator[ServiceConfig]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            peer.calls.append((self.command, dict(self.headers), request))
            if "id" not in request:
                self.send_response(
                    int(peer.fault[-3:])
                    if peer.fault in {"initialized401", "initialized403"}
                    else 202
                )
                self.end_headers()
                return
            response = peer.reply(request)
            if peer.fault == "wrong-id":
                response["id"] = "not-request-id"
            if peer.fault == "rpc-error":
                response.pop("result")
                response["error"] = {"code": -32603, "message": "synthetic-secret-canary"}
            data = json.dumps(response).encode()
            if peer.fault == "oversize":
                data += b" " * 2048
            self.send_response(
                int(peer.fault[4:])
                if peer.fault in {"http401", "http403"}
                else 500
                if peer.fault == "http500" and request["method"] == "tools/call"
                else 200
            )
            self.send_header(
                "Content-Type",
                "text/event-stream" if peer.mode.startswith("sse") else "application/json",
            )
            if peer.fault == "session":
                self.send_header("Mcp-Session-Id", "synthetic-session-canary")
            self.end_headers()
            if peer.mode.startswith("sse"):
                data = b"id: 9\nevent: message\ndata: " + data + b"\n\n"
            for start in range(0, len(data), 17):
                self.wfile.write(data[start : start + 17])
            self.wfile.flush()
            if peer.mode == "sse-open":
                peer.release.wait(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        yield ServiceConfig(
            service_config_id="service-a",
            service_config_version=1,
            deployment_id="synthetic",
            tenant_id="default",
            display_name="Synthetic",
            endpoint=origin + "/mcp",
            issuer=origin,
            resource=origin + "/mcp",
            registration_endpoint=origin + "/register",
            authorization_endpoint=origin + "/authorize",
            token_endpoint=origin + "/token",
            callback_uri="https://client.invalid/callback",
            callback_config_version=1,
            protocol_version=peer.version,
            enabled=True,
        )
    finally:
        peer.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def authorization(service: str = "service-a") -> McpAuthorizationContext:
    return McpAuthorizationContext(
        tenant_id="default",
        user_id="synthetic-user",
        login_session_fingerprint="fingerprint",
        task_id="synthetic-task",
        chat_session_id="synthetic-chat",
        service_config_id=service,
        service_config_version=1,
        connection_id="connection-a",
        binding_epoch=1,
        grant_epoch=1,
        registration_id="registration-a",
        capability_id="synthetic.read",
        capability_version="1",
    )


class Tokens:
    async def reject(self, context: McpAuthorizationContext) -> None:
        pass

    async def resolve(self, context: McpAuthorizationContext) -> str:
        assert context.user_id == "synthetic-user"
        return "synthetic-secret-canary"


@pytest.mark.parametrize("version", ["2025-11-25", "2026-07-28"])
@pytest.mark.parametrize("mode", ["json", "sse"])
def test_real_sdk_profiles_use_only_post_and_one_call(version: str, mode: str) -> None:
    peer = Peer(version, mode)
    with serving(peer) as config:
        driver = McpDriver({config.service_config_id: config}, Tokens())
        result = asyncio.run(
            driver.call(
                authorization(),
                "business_context_get",
                {},
                input_digest=input_digest("business_context_get"),
                safety_digest=safety_digest("business_context_get"),
                write=False,
            )
        )
    assert result["structuredContent"] == {"synthetic": True}
    assert peer.effects == 1
    assert [body["method"] for _, _, body in peer.calls] == (
        ["initialize", "notifications/initialized", "tools/list", "tools/call"]
        if version == "2025-11-25"
        else ["server/discover", "tools/list", "tools/call"]
    )
    assert {method for method, _, _ in peer.calls} == {"POST"}
    for _, headers, body in peer.calls:
        lowered = {key.lower(): value for key, value in headers.items()}
        assert lowered["mcp-protocol-version"] == version
        if version == "2026-07-28":
            assert lowered["mcp-method"] == body["method"]
            assert body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == version
            if body["method"] == "tools/call":
                assert lowered["mcp-name"] == "business_context_get"


@pytest.mark.parametrize("fault", ["wrong-id", "rpc-error", "session", "drift", "oversize"])
def test_pre_call_faults_fail_closed(fault: str, caplog: Any) -> None:
    peer = Peer("2025-11-25", fault=fault)
    with serving(peer) as config:
        if fault == "oversize":
            config = config.model_copy(update={"response_limit": 1024})
        with pytest.raises(McpFailure) as caught:
            asyncio.run(
                McpDriver({"service-a": config}, Tokens()).call(
                    authorization(),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=False,
                )
            )
    assert caught.value.may_have_sent is False
    assert peer.effects == 0
    assert "synthetic-secret-canary" not in caplog.text
    assert "synthetic-secret-canary" not in str(caught.value)


def test_write_http_failure_has_one_effect_and_unknown_outcome() -> None:
    peer = Peer("2025-11-25", fault="http500")
    with serving(peer) as config:
        with pytest.raises(McpFailure) as caught:
            asyncio.run(
                McpDriver({"service-a": config}, Tokens()).call(
                    authorization(),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=True,
                )
            )
    assert caught.value.may_have_sent is True
    assert peer.effects == 1
    assert len([body for _, _, body in peer.calls if body["method"] == "tools/call"]) == 1


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b'{"x":' + b"[" * 65 + b"0" + b"]" * 65 + b"}"]
)
def test_json_boundaries(raw: bytes) -> None:
    with pytest.raises(McpFailure, match="mcp_response_invalid"):
        parse_json(raw)


def test_complete_sse_result_returns_before_server_closes_stream() -> None:
    peer = Peer("2026-07-28", mode="sse-open")
    with serving(peer) as profile:
        profile = profile.model_copy(update={"deadline_seconds": 2})
        result = asyncio.run(
            McpDriver({"service-a": profile}, Tokens()).call(
                authorization(),
                "business_context_get",
                {},
                input_digest=input_digest("business_context_get"),
                safety_digest=safety_digest("business_context_get"),
                write=False,
            )
        )
        assert result["structuredContent"] == {"synthetic": True}
        assert not peer.release.is_set() and peer.effects == 1


def test_dependency_payload_logs_are_sealed_even_at_debug(caplog) -> None:
    from app.infra.mcp.transport import seal_vendor_logs

    names = ("mcp", "mcp.shared.session", "client", "httpx2", "httpcore2.connection")
    for name in names:
        logging.getLogger(name).setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    seal_vendor_logs()
    for name in names:
        logging.getLogger(name).error("synthetic-sensitive-log-canary")
    assert "synthetic-sensitive-log-canary" not in caplog.text
    peer = Peer("2026-07-28", fault="rpc-error")
    with serving(peer) as profile:
        with pytest.raises(McpFailure):
            asyncio.run(
                McpDriver({"service-a": profile}, Tokens()).call(
                    authorization(),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=False,
                )
            )
    assert "synthetic-secret-canary" not in caplog.text
