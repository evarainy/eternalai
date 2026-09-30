"""A public SDK Transport implementation with one bounded POST per message.

Never instantiates the SDK's stock HTTP/OAuth transports. There is no GET,
DELETE, redirect, reconnect, refresh, protocol fallback, or retry path here.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

import anyio
import httpx2
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pydantic import TypeAdapter

from app.mcp.models import McpFailure, ServiceConfig, digest
from mcp.shared.message import ClientMessageMetadata, SessionMessage
from mcp.types import JSONRPCMessage

_MESSAGE: TypeAdapter[JSONRPCMessage] = TypeAdapter(JSONRPCMessage)


class _NoVendorPayloadLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


def seal_vendor_logs() -> None:
    """Suppress payload-capable dependency logging at the owning logger.

    Applied once during composition, including SDK's historical `client` name.
    Local observability uses closed error codes outside these namespaces.
    """
    names = {"mcp", "client", "httpx2", "httpcore2"}
    names.update(
        name
        for name in logging.Logger.manager.loggerDict
        if (name.startswith(("mcp.", "client.", "httpx2.", "httpcore2.")))
    )
    for name in names:
        logger = logging.getLogger(name)
        if not any(isinstance(item, _NoVendorPayloadLog) for item in logger.filters):
            logger.addFilter(_NoVendorPayloadLog())
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False


def parse_json(raw: bytes) -> dict[str, Any]:
    """Bound depth before allocating nested JSON objects, reject duplicate keys."""
    depth = 0
    quoted = escaped = False
    for ch in raw:
        if quoted:
            if escaped:
                escaped = False
            elif ch == 92:
                escaped = True
            elif ch == 34:
                quoted = False
        elif ch == 34:
            quoted = True
        elif ch in (91, 123):
            depth += 1
            if depth > 64:
                raise McpFailure("mcp_response_invalid")
        elif ch in (93, 125):
            depth -= 1

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    try:
        result = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, RecursionError):
        raise McpFailure("mcp_response_invalid") from None
    if not isinstance(result, dict):
        raise McpFailure("mcp_response_invalid")
    return result


class BoundedPostTransport:
    def __init__(
        self,
        config: ServiceConfig,
        token: Callable[[], Awaitable[str]],
        *,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._token = token
        self._http_client = http_client
        self.last_failure: McpFailure | None = None
        self.sent_methods: list[str] = []
        self.catalog_annotations: dict[str, str] = {}
        self._context: Any = None
        seal_vendor_logs()

    async def __aenter__(
        self,
    ) -> tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ]:
        self._context = self._connect()
        return await self._context.__aenter__()  # type: ignore[no-any-return]

    async def __aexit__(self, *args: Any) -> None:
        await self._context.__aexit__(*args)

    @asynccontextmanager
    async def _connect(
        self,
    ) -> AsyncIterator[
        tuple[
            MemoryObjectReceiveStream[SessionMessage | Exception],
            MemoryObjectSendStream[SessionMessage],
        ]
    ]:
        incoming_send, incoming_read = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](1)
        outgoing_send, outgoing_read = anyio.create_memory_object_stream[SessionMessage](1)
        client = self._http_client or httpx2.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx2.Timeout(self.config.deadline_seconds, connect=5, pool=5),
        )
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(self._pump, client, outgoing_read, incoming_send)
                try:
                    yield incoming_read, outgoing_send
                finally:
                    group.cancel_scope.cancel()
        finally:
            await incoming_read.aclose()
            await incoming_send.aclose()
            await outgoing_send.aclose()
            await outgoing_read.aclose()
            if self._http_client is None:
                await client.aclose()

    async def _pump(
        self,
        client: httpx2.AsyncClient,
        outgoing: MemoryObjectReceiveStream[SessionMessage],
        incoming: MemoryObjectSendStream[SessionMessage | Exception],
    ) -> None:
        async for item in outgoing:
            payload = item.message.model_dump(by_alias=True, mode="json", exclude_none=True)
            request_id = payload.get("id")
            try:
                result = await self._post(client, payload, item.metadata)
                if result is not None:
                    if payload.get("method") == "tools/list":
                        listing = result.get("result")
                        tools = listing.get("tools") if isinstance(listing, dict) else None
                        if isinstance(tools, list):
                            # Hash raw metadata before SDK models can discard unknown keys.
                            self.catalog_annotations = {
                                tool["name"]: digest(tool.get("annotations"))
                                for tool in tools
                                if isinstance(tool, dict) and isinstance(tool.get("name"), str)
                            }
                    await incoming.send(SessionMessage(_MESSAGE.validate_python(result)))
            except Exception as exc:
                failure = exc if isinstance(exc, McpFailure) else McpFailure("mcp_transport_failed")
                self.last_failure = failure
                if request_id is None or failure.code == "mcp_authorization_rejected":
                    # Notifications have no response ID to wake an SDK request. Terminate
                    # the task group, so queued list/call packets cannot outlive rejection.
                    raise failure
                if request_id is not None:
                    await incoming.send(
                        SessionMessage(
                            _MESSAGE.validate_python(
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "error": {"code": -32099, "message": failure.code},
                                }
                            )
                        )
                    )

    async def _post(
        self,
        client: httpx2.AsyncClient,
        payload: dict[str, Any],
        metadata: Any,
    ) -> dict[str, Any] | None:
        method = payload.get("method")
        if method not in {
            "initialize",
            "notifications/initialized",
            "server/discover",
            "tools/list",
            "tools/call",
        }:
            raise McpFailure("mcp_method_denied")
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if len(raw) > 4 * 1024 * 1024:
            raise McpFailure("mcp_request_too_large")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "MCP-Protocol-Version": self.config.protocol_version,
        }
        if isinstance(metadata, ClientMessageMetadata):
            if metadata.resumption_token or metadata.on_resumption_token_update:
                raise McpFailure("mcp_resume_denied")
            for key, value in (metadata.headers or {}).items():
                if key.lower() not in {"mcp-protocol-version", "mcp-method", "mcp-name"}:
                    raise McpFailure("mcp_header_denied")
                headers[key] = value
        normalized = {k.lower(): v for k, v in headers.items()}
        if normalized["mcp-protocol-version"] != self.config.protocol_version:
            raise McpFailure("mcp_protocol_mismatch")
        if self.config.protocol_version == "2026-07-28":
            params = payload.get("params", {})
            meta = params.get("_meta", {})
            if (
                meta.get("io.modelcontextprotocol/protocolVersion") != self.config.protocol_version
                or normalized.get("mcp-method") != method
                or (method == "tools/call" and normalized.get("mcp-name") != params.get("name"))
            ):
                raise McpFailure("mcp_protocol_mismatch")
        sent = False
        try:
            with anyio.fail_after(self.config.deadline_seconds):
                headers["Authorization"] = "Bearer " + await self._token()
                sent = True
                self.sent_methods.append(method)
                async with client.stream(
                    "POST",
                    self.config.endpoint,
                    content=raw,
                    headers=headers,
                    follow_redirects=False,
                ) as response:
                    if response.headers.get("mcp-session-id"):
                        raise McpFailure("mcp_session_unsupported")
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise McpFailure("mcp_encoding_unsupported")
                    if response.status_code in (401, 403):
                        raise McpFailure("mcp_authorization_rejected")
                    if response.status_code not in (200, 202):
                        raise McpFailure("mcp_http_failed")
                    body = bytearray()
                    content_type = response.headers.get("content-type", "").split(";")[0]
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > self.config.response_limit:
                            raise McpFailure("mcp_response_too_large")
                        if content_type == "text/event-stream" and "id" in payload:
                            framed = bytes(body).replace(b"\r\n", b"\n")
                            boundary = framed.rfind(b"\n\n")
                            if boundary >= 0:
                                terminal = self._sse(
                                    framed[: boundary + 2], payload["id"], required=False
                                )
                                if terminal is not None:
                                    return terminal
                    if "id" not in payload:
                        if response.status_code != 202 or body:
                            raise McpFailure("mcp_notification_invalid")
                        return None
                    if response.status_code != 200:
                        raise McpFailure("mcp_response_invalid")
                    if content_type == "application/json":
                        return self._correlate(parse_json(bytes(body)), payload["id"])
                    if content_type == "text/event-stream":
                        return self._sse(bytes(body), payload["id"])
                    raise McpFailure("mcp_content_type_invalid")
        except Exception as exc:
            code = exc.code if isinstance(exc, McpFailure) else "mcp_transport_failed"
            raise McpFailure(code, may_have_sent=sent) from None

    @staticmethod
    def _correlate(result: dict[str, Any], request_id: Any) -> dict[str, Any]:
        if (
            result.get("jsonrpc") != "2.0"
            or type(result.get("id")) is not type(request_id)
            or result.get("id") != request_id
            or "method" in result
            or (("error" in result) == ("result" in result))
        ):
            raise McpFailure("mcp_response_invalid")
        if "error" in result:
            # Provider error strings/data never enter SDK logging or exceptions.
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32099, "message": "mcp_rpc_rejected"},
            }
        return result

    def _sse(self, body: bytes, request_id: Any, *, required: bool = True) -> dict[str, Any] | None:
        response = None
        events = body.replace(b"\r\n", b"\n").split(b"\n\n")
        if len(events) > 256:
            raise McpFailure("mcp_response_too_large")
        for event in events:
            lines = event.split(b"\n")
            if any(len(line) > self.config.response_limit for line in lines):
                raise McpFailure("mcp_response_too_large")
            data = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
            if not data:
                continue
            message = parse_json(data)
            if "id" not in message and message.get("method") == "notifications/progress":
                continue  # Do not expose provider progress text or extend the deadline.
            if response is not None:
                raise McpFailure("mcp_response_invalid")
            response = self._correlate(message, request_id)
        if response is None and required:
            raise McpFailure("mcp_response_invalid")
        return response


class BoundedOAuthHttp:
    """No SDK OAuth provider, refresh, discovery fallback, redirects or retries."""

    async def post(self, endpoint: str, payload: dict[str, Any], *, form: bool) -> dict[str, Any]:
        from urllib.parse import urlencode

        raw = urlencode(payload).encode() if form else json.dumps(payload).encode()
        if len(raw) > 16384:
            raise McpFailure("mcp_oauth_request_too_large")
        seal_vendor_logs()
        try:
            with anyio.fail_after(30):
                async with httpx2.AsyncClient(
                    follow_redirects=False,
                    trust_env=False,
                    timeout=httpx2.Timeout(30, connect=5, pool=5),
                ) as client:
                    async with client.stream(
                        "POST",
                        endpoint,
                        content=raw,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded"
                            if form
                            else "application/json",
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        if response.status_code not in (200, 201):
                            raise McpFailure("mcp_oauth_rejected")
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise McpFailure("mcp_oauth_invalid")
                        body = bytearray()
                        async for chunk in response.aiter_raw():
                            body.extend(chunk)
                            if len(body) > 16384:
                                raise McpFailure("mcp_oauth_response_too_large")
                        return parse_json(bytes(body))
        except Exception:
            raise McpFailure("mcp_oauth_failed") from None
