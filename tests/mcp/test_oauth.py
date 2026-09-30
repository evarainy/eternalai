"""OAuth against real loopback HTTP and real encrypted PostgreSQL records."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from app.event_loop import make_event_loop
from app.infra.mcp.transport import BoundedOAuthHttp
from app.mcp.models import McpFailure
from app.mcp.oauth import OAuthConnections, PendingIdentityPolicy
from app.ports.auth import AuthenticatedSessionContext, Principal, PrincipalOrgContext
from tests.infra.persistence.test_mcp_store import config, database


class ApprovedSyntheticPolicy:
    approved, version = True, "synthetic-test-only"

    async def verify(self, **kwargs):
        assert kwargs["user_id"] == "synthetic-user"
        assert set(kwargs["token_metadata"]) == {"scope", "issuer", "registration_id"}
        return "synthetic-same-subject-proof"


def session():
    return AuthenticatedSessionContext(
        principal=Principal(
            ai_user_id="synthetic-user",
            display_name="Synthetic",
            roles=(),
            org_ctx=PrincipalOrgContext(tenant_id="default"),
        ),
        fingerprint=b"synthetic-session",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


@contextmanager
def oauth_peer(fault=""):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            payload = (
                json.loads(raw)
                if self.path == "/register"
                else {key: value[0] for key, value in parse_qs(raw.decode()).items()}
            )
            calls.append((self.path, payload))
            if self.path == "/register":
                result = {**payload, "client_id": "synthetic-stable-client"}
            else:
                result = {
                    "access_token": "synthetic-oauth-canary",
                    "token_type": "Bearer",
                    "expires_in": 300,
                }
                if fault == "refresh":
                    result["refresh_token"] = "synthetic-refresh-canary"
            self.send_response(302 if fault == "redirect" else 200)
            self.send_header("Content-Type", "application/json")
            if fault == "redirect":
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    profile = config().model_copy(
        update={
            "issuer": origin,
            "endpoint": origin + "/mcp",
            "resource": origin + "/business",
            "registration_endpoint": origin + "/register",
            "authorization_endpoint": origin + "/authorize",
            "token_endpoint": origin + "/token",
        }
    )
    try:
        yield profile, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("approved", [False, True])
def test_code_pkce_resource_registration_once_and_identity_fail_closed(
    migrated_database_url, approved
):
    with oauth_peer() as (profile, calls):

        async def run():
            async with database(migrated_database_url) as (store, _, revocations):
                await store.configure(profile)
                service = OAuthConnections(
                    {profile.service_config_id: profile},
                    store,
                    BoundedOAuthHttp(),
                    revocations,
                    ApprovedSyntheticPolicy() if approved else PendingIdentityPolicy(),
                )
                login = session()
                url = await service.authorize(profile.service_config_id, login)
                query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
                assert query["resource"] == profile.resource
                assert query["code_challenge_method"] == "S256"
                assert "code_verifier" not in query
                for patch in (
                    {"issuer": "https://wrong.invalid"},
                    {"state": "other-state"},
                    {"callback_uri": "https://client.invalid/other"},
                ):
                    args = {
                        "state": query["state"],
                        "code": "synthetic-code",
                        "issuer": profile.issuer,
                        "callback_uri": profile.callback_uri,
                        **patch,
                    }
                    with pytest.raises(McpFailure):
                        await service.callback(login, **args)
                assert [path for path, _ in calls] == ["/register"]
                await service.callback(
                    login,
                    state=query["state"],
                    code="synthetic-code",
                    issuer=profile.issuer,
                    callback_uri=profile.callback_uri,
                )
                token_request = calls[1][1]
                expected = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(token_request["code_verifier"].encode()).digest()
                    )
                    .decode()
                    .rstrip("=")
                )
                assert query["code_challenge"] == expected
                assert (
                    token_request["resource"] == profile.resource
                    and token_request["client_id"] == query["client_id"]
                )
                assert set(token_request) == {
                    "grant_type",
                    "code",
                    "client_id",
                    "redirect_uri",
                    "resource",
                    "code_verifier",
                }
                with pytest.raises(McpFailure):
                    await service.callback(
                        login,
                        state=query["state"],
                        code="synthetic-code",
                        issuer=profile.issuer,
                        callback_uri=profile.callback_uri,
                    )
                assert len(calls) == 2
                connection = await store.connection(
                    tenant_id="default",
                    user_id="synthetic-user",
                    service_config_id=profile.service_config_id,
                )
                assert connection.state == ("ACTIVE" if approved else "PENDING_IDENTITY")
                # A reconstructed service reuses DCR; authorization is a new one-time transaction.
                restarted = OAuthConnections(
                    service.configs, store, BoundedOAuthHttp(), revocations, service.identity_policy
                )
                await restarted.authorize(profile.service_config_id, login)
                assert len(calls) == 2

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("fault", ["refresh", "redirect"])
def test_refresh_or_redirect_never_replays_code_or_registration(migrated_database_url, fault):
    with oauth_peer(fault) as (profile, calls):

        async def run():
            async with database(migrated_database_url) as (store, _, revocations):
                await store.configure(profile)
                service = OAuthConnections(
                    {profile.service_config_id: profile},
                    store,
                    BoundedOAuthHttp(),
                    revocations,
                    ApprovedSyntheticPolicy(),
                )
                if fault == "redirect":
                    for _ in range(2):
                        with pytest.raises(McpFailure):
                            await service.authorize(profile.service_config_id, session())
                    assert len(calls) == 1
                else:
                    url = await service.authorize(profile.service_config_id, session())
                    state = parse_qs(urlsplit(url).query)["state"][0]
                    for _ in range(2):
                        with pytest.raises(McpFailure):
                            await service.callback(
                                session(),
                                state=state,
                                code="synthetic",
                                issuer=profile.issuer,
                                callback_uri=profile.callback_uri,
                            )
                    assert len(calls) == 2
                    connection = await store.connection(
                        tenant_id="default",
                        user_id="synthetic-user",
                        service_config_id=profile.service_config_id,
                    )
                    assert connection.state == "AUTHORIZING"

        asyncio.run(run(), loop_factory=make_event_loop)
