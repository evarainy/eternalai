from __future__ import annotations

import asyncio

import pytest

from app.infra.mcp.driver import McpDriver
from app.mcp.contracts import input_digest, safety_digest
from app.mcp.models import McpFailure
from tests.infra.mcp.test_transport import Peer, Tokens, authorization, serving


def test_missing_service_never_falls_back_to_other_server() -> None:
    peer = Peer("2025-11-25")
    with serving(peer) as config:
        with pytest.raises(McpFailure, match="mcp_service_unavailable"):
            asyncio.run(
                McpDriver({"service-a": config}, Tokens()).call(
                    authorization("service-b"),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=False,
                )
            )
    assert peer.calls == []


def test_two_same_named_tools_remain_bound_to_separate_servers() -> None:
    first, second = Peer("2025-11-25"), Peer("2026-07-28")
    with serving(first) as config_a, serving(second) as original_b:
        config_b = original_b.model_copy(update={"service_config_id": "service-b"})

        async def run() -> None:
            driver = McpDriver({"service-a": config_a, "service-b": config_b}, Tokens())
            for service in ("service-a", "service-b"):
                await driver.call(
                    authorization(service),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=False,
                )

        asyncio.run(run())
    assert first.effects == 1
    assert second.effects == 1
    assert first.calls[0][2]["method"] == "initialize"
    assert second.calls[0][2]["method"] == "server/discover"


def test_queue_bound_cancellation_and_late_revocation_leave_zero_sends() -> None:
    peer = Peer("2025-11-25")
    with serving(peer) as profile:

        class RevocableTokens(Tokens):
            revoked = False

            async def resolve(self, context):
                if self.revoked:
                    raise McpFailure("mcp_authorization_invalid")
                return await super().resolve(context)

        async def run():
            tokens = RevocableTokens()
            driver = McpDriver({"service-a": profile}, tokens)

            async def call():
                return await driver.call(
                    authorization(),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=False,
                )

            for _ in range(4):
                await driver._global.acquire()
            pending = [asyncio.create_task(call()) for _ in range(20)]
            await asyncio.sleep(0)
            with pytest.raises(McpFailure, match="mcp_queue_full"):
                await call()
            assert driver._queued == 20 and len(driver._connections) == 1
            for task in pending[1:]:
                task.cancel()
            await asyncio.gather(*pending[1:], return_exceptions=True)
            tokens.revoked = True
            for _ in range(4):
                driver._global.release()
            with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                await pending[0]
            assert (
                driver._queued == 0 and driver._connections == {} and driver._connection_users == {}
            )
            assert peer.calls == []
            tokens.revoked = False
            result = await call()
            assert result["structuredContent"] == {"synthetic": True} and peer.effects == 1

        asyncio.run(run())


@pytest.mark.parametrize("change", ["read_only", "destructive", "missing", "extra"])
def test_safety_annotation_drift_prevents_tools_call(change: str) -> None:
    class ChangedPeer(Peer):
        def reply(self, request):
            reply = super().reply(request)
            if request["method"] == "tools/list":
                item = reply["result"]["tools"][0]
                if change == "missing":
                    item.pop("annotations")
                elif change == "extra":
                    item["annotations"]["syntheticUnapprovedHint"] = True
                else:
                    key = "readOnlyHint" if change == "read_only" else "destructiveHint"
                    item["annotations"][key] = not item["annotations"][key]
            return reply

    peer = ChangedPeer("2025-11-25")
    with serving(peer) as profile:
        with pytest.raises(McpFailure, match="mcp_catalog_drift"):
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
    assert peer.effects == 0
    assert not any(body["method"] == "tools/call" for _, _, body in peer.calls)


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("phase", ["http", "initialized"])
def test_actual_http_authorization_rejection_invalidates_exact_context(
    status: int, phase: str
) -> None:
    class RejectedTokens(Tokens):
        def __init__(self):
            self.rejected = []

        async def reject(self, context):
            self.rejected.append(context)

    peer = Peer("2025-11-25", fault=f"{phase}{status}")
    tokens = RejectedTokens()
    with serving(peer) as profile:
        with pytest.raises(McpFailure, match="^mcp_authorization_rejected$") as caught:
            asyncio.run(
                McpDriver({"service-a": profile}, tokens).call(
                    authorization(),
                    "business_context_get",
                    {},
                    input_digest=input_digest("business_context_get"),
                    safety_digest=safety_digest("business_context_get"),
                    write=False,
                )
            )
    assert tokens.rejected == [authorization()]
    assert caught.value.may_have_sent is False
    assert peer.effects == 0
    assert [body["method"] for _, _, body in peer.calls] == (
        ["initialize", "notifications/initialized"] if phase == "initialized" else ["initialize"]
    )
