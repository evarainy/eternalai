from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.api.v1.mcp import McpApiService
from app.event_loop import make_event_loop
from app.infra.mcp.transport import BoundedOAuthHttp
from app.infra.workflow.engine_adapter import WorkflowEngineAdapter
from app.main import create_app
from app.mcp.oauth import OAuthConnections
from app.ports.auth import VerifiedSessionToken, authenticated_session
from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS
from tests.infra.mcp.test_transport import serving
from tests.mcp.test_oauth import ApprovedSyntheticPolicy
from tests.workflow.test_mcp_recovery import BusinessPeer, confirm, harness


def test_api_auth_csrf_owner_recovery_and_duplicate_confirmation(migrated_database_url):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, "talk_preparation_save") as h:
                owner = authenticated_session.get()

                class Tokens:
                    def inspect(self, token):
                        return VerifiedSessionToken(
                            principal=owner.principal
                            if token == "synthetic"
                            else owner.principal.model_copy(update={"ai_user_id": "other-user"}),
                            fingerprint=b"synthetic-session",
                            expires_at=datetime.now(UTC) + timedelta(minutes=5),
                            version=2,
                        )

                service = McpApiService(
                    OAuthConnections(
                        {profile.service_config_id: profile},
                        h["store"],
                        BoundedOAuthHttp(),
                        h["revocations"],
                        ApprovedSyntheticPolicy(),
                    ),
                    h["operations"],
                    WorkflowEngineAdapter(h["workflow"]),
                )
                application = create_app(
                    session_tokens=Tokens(),
                    session_revocations=h["revocations"],
                    mcp_service=service,
                    csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application), base_url="https://testserver"
                ) as client:
                    assert (await client.get("/api/v1/mcp/services")).status_code == 401
                    client.cookies.set("eternalai_session", "synthetic")
                    services = await client.get("/api/v1/mcp/services")
                    assert services.status_code == 200
                    assert services.json() == [
                        {
                            "service_config_id": "service-a",
                            "display_name": "Synthetic",
                            "enabled": True,
                        }
                    ]
                    path = "/api/v1/mcp/operations/" + h["op"].operation_id
                    result = await client.get(path)
                    assert (
                        result.status_code == 200
                        and result.json()["state"] == "WAITING_LOCAL_CONFIRM"
                    )
                    assert set(result.json()) == {
                        "operation_id",
                        "service_config_id",
                        "state",
                        "revision",
                        "expires_at",
                        "review_url",
                        "recovery_action",
                        "action",
                        "service_name",
                        "argument_preview",
                        "preview_digest",
                        "result",
                    }
                    assert result.json()["argument_preview"] == {"personId": "p1"}
                    listing = await client.get("/api/v1/mcp/operations")
                    assert listing.status_code == 200 and listing.json() == [result.json()]
                    body = {
                        "action": "confirm",
                        "expected_revision": result.json()["revision"],
                        "preview_digest": result.json()["preview_digest"],
                    }
                    assert (await client.post(path + "/resume", json=body)).status_code == 403
                    extra = await client.post(
                        path + "/resume",
                        json={**body, "secret": "synthetic-must-not-echo"},
                        headers=TEST_CSRF_HEADERS,
                    )
                    assert extra.status_code == 422 and "synthetic-must-not-echo" not in extra.text
                    client.cookies.set("eternalai_session", "other")
                    assert (await client.get(path)).status_code == 409
                    assert (await client.get("/api/v1/mcp/operations")).json() == []
                    client.cookies.set("eternalai_session", "synthetic")
                    await client.get(
                        path
                    )  # Reconstruct authenticated server context for creating the real gate.
                    await confirm(h, decide=False)
                    stale = await client.post(
                        path + "/resume",
                        json={**body, "preview_digest": "0" * 64},
                        headers=TEST_CSRF_HEADERS,
                    )
                    assert stale.status_code == 409 and peer.effects == 0
                    # The endpoint creates the immutable decision, then replay is stale.
                    response = await client.post(
                        path + "/resume", json=body, headers=TEST_CSRF_HEADERS
                    )
                    assert (
                        response.status_code == 200
                        and response.json()["state"] == "VERIFIED_SUCCESS"
                    )
                    assert (
                        await client.post(path + "/resume", json=body, headers=TEST_CSRF_HEADERS)
                    ).status_code == 409
                    assert peer.effects == 1
                    callback = await client.get(
                        "/api/v1/mcp/oauth/callback?code=one&code=two&state=x&iss=x"
                    )
                    assert callback.status_code == 409
                    assert "one" not in callback.text and "two" not in callback.text

        asyncio.run(run(), loop_factory=make_event_loop)
