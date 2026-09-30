"""Real SDK client orchestration with explicitly selected protocol profiles."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from app.infra.mcp.transport import BoundedPostTransport
from app.mcp.models import McpFailure, ServiceConfig, digest
from app.ports.mcp import McpAuthorizationContext, McpTokenResolverPort
from mcp import Client
from mcp.types import DiscoverResult


class McpDriver:
    def __init__(self, configs: dict[str, ServiceConfig], tokens: McpTokenResolverPort) -> None:
        self._configs = configs
        self._tokens = tokens
        self._global = anyio.Semaphore(4)
        self._connections: dict[tuple[str, str, str, str], anyio.Lock] = {}
        self._connection_users: dict[tuple[str, str, str, str], int] = {}
        self._queued = 0

    async def call(
        self,
        context: McpAuthorizationContext,
        tool: str,
        arguments: dict[str, Any],
        *,
        input_digest: str,
        safety_digest: str,
        write: bool,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        config = self._configs.get(context.service_config_id)
        if (
            config is None
            or not config.enabled
            or config.tenant_id != context.tenant_id
            or config.service_config_version != context.service_config_version
        ):
            raise McpFailure("mcp_service_unavailable")
        key = (context.tenant_id, context.user_id, context.service_config_id, context.connection_id)
        if self._queued >= 20:
            raise McpFailure("mcp_queue_full")
        lock = self._connections.setdefault(key, anyio.Lock())
        self._connection_users[key] = self._connection_users.get(key, 0) + 1
        self._queued += 1
        acquired_global = acquired_connection = False
        try:
            with anyio.fail_after(5):
                await self._global.acquire()
                acquired_global = True
                await lock.acquire()
                acquired_connection = True
            self._queued -= 1
            return await self._attempt(
                config,
                context,
                tool,
                arguments,
                input_digest=input_digest,
                safety_digest=safety_digest,
                before_send=before_send,
            )
        except TimeoutError:
            raise McpFailure("mcp_queue_timeout") from None
        finally:
            if not acquired_connection:
                self._queued -= 1
            if acquired_connection:
                lock.release()
            if acquired_global:
                self._global.release()
            self._connection_users[key] -= 1
            if self._connection_users[key] == 0:
                self._connection_users.pop(key)
                self._connections.pop(key)

    async def _attempt(
        self,
        config: ServiceConfig,
        context: McpAuthorizationContext,
        tool: str,
        arguments: dict[str, Any],
        *,
        input_digest: str,
        safety_digest: str,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        async def token() -> str:
            value = await self._tokens.resolve(context)
            if before_send is not None:
                await before_send()
            return value

        transport = BoundedPostTransport(config, token)
        called = False
        contract_failure: McpFailure | None = None
        try:
            with anyio.fail_after(config.deadline_seconds):
                async with Client(
                    transport,
                    mode="legacy" if config.protocol_version == "2025-11-25" else "2026-07-28",
                    cache=None,
                    read_timeout_seconds=config.deadline_seconds,
                ) as client:
                    if config.protocol_version == "2026-07-28":
                        discovery = DiscoverResult.model_validate(
                            await client.session.send_discover("2026-07-28")
                        )
                        if "2026-07-28" not in discovery.supported_versions:
                            raise McpFailure("mcp_protocol_mismatch")
                        client.session.adopt(discovery)
                    if client.protocol_version != config.protocol_version:
                        raise McpFailure("mcp_protocol_mismatch")
                    # This provider's complete per-grant catalog has ttlMs=0 and no pagination.
                    listing = await client.list_tools(cache_mode="bypass")
                    if listing.next_cursor or len(listing.tools) > 256:
                        raise McpFailure("mcp_catalog_invalid")
                    matches = [item for item in listing.tools if item.name == tool]
                    if len(matches) != 1 or digest(matches[0].input_schema) != input_digest:
                        contract_failure = McpFailure("mcp_catalog_drift")
                        raise contract_failure
                    annotations = matches[0].annotations
                    if (
                        transport.catalog_annotations.get(tool) != safety_digest
                        or annotations is None
                        or digest(
                            annotations.model_dump(
                                by_alias=True,
                                exclude_unset=True,
                                mode="json",
                            )
                        )
                        != safety_digest
                    ):
                        contract_failure = McpFailure("mcp_catalog_drift")
                        raise contract_failure
                    called = True
                    result = await client.call_tool(tool, arguments)
                    return result.model_dump(by_alias=True, mode="json", exclude_none=True)
        except Exception as exc:
            failure = transport.last_failure
            if failure is not None and failure.code == "mcp_authorization_rejected":
                await self._tokens.reject(context)
            code = (
                contract_failure.code
                if contract_failure
                else failure.code
                if failure
                else exc.code
                if isinstance(exc, McpFailure)
                else "mcp_protocol_failed"
            )
            raise McpFailure(code, may_have_sent=called) from None
