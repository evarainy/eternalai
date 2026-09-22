from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.auth import _parse_login_credential
from app.contracts.sdui.models import UserAction
from app.event_loop import make_event_loop
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.auth.session_revocations import PostgreSQLSessionRevocationStore
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.main import create_app
from app.ports.auth import (
    AuthenticationError,
    LoginCredential,
    Principal,
    PrincipalOrgContext,
    SessionBindingError,
)
from app.ports.response_envelope import ResponseEnvelope
from tests.api.test_me import StubUserProfile
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.api.test_work_object_dispatch import run
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    MemorySessionRevocations,
    StaticSessionTokens,
    auth_cookies,
)
from tests.infra.auth.test_crypto import encoding_alias, legacy_ticket


def logout_client(db, tokens=None, store=None, **kwargs):
    tokens = tokens or _token_port()
    profile = StubUserProfile()
    application = create_app(
        session_tokens=tokens,
        session_revocations=store or PostgreSQLSessionRevocationStore(db.factory),
        user_profile=profile,
        authentication=SuccessfulAuthentication(_principal("logout")),
        session_cookie_ttl_seconds=3600,
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        **kwargs,
    )
    return (
        TestClient(
            application,
            base_url="https://testserver",
            backend_options={"loop_factory": make_event_loop},
        ),
        tokens,
        profile,
    )


def set_ticket(client, ticket):
    client.cookies.clear()
    client.cookies.set("eternalai_session", ticket, path="/api/v1")


def assert_auth_denied(response):
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"
    assert response.headers["www-authenticate"] == "Session"


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("logout_alias", [False, True])
def test_signature_encoding_aliases_share_revocation(dispatch_db, version, logout_alias):
    client, tokens, _ = logout_client(dispatch_db)
    principal = _principal("aliases")
    original = (
        legacy_ticket(principal, int(datetime.now(UTC).timestamp()))
        if version == 1
        else tokens.issue(principal)
    )
    alias = encoding_alias(original)
    independent = tokens.issue(principal)
    for ticket in (original, alias, independent):
        set_ticket(client, ticket)
        assert client.get("/api/v1/me").status_code == 200
    set_ticket(client, alias if logout_alias else original)
    assert client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS).status_code == 200
    for ticket in (original, alias):
        set_ticket(client, ticket)
        assert_auth_denied(client.get("/api/v1/me"))
    set_ticket(client, independent)
    assert client.get("/api/v1/me").status_code == 200
    assert revocation_count(dispatch_db) == 1
    client.close()


@pytest.mark.parametrize("secure", [False, True])
def test_logout_commits_then_expires_exact_login_cookie(dispatch_db, secure):
    client, tokens, _ = logout_client(dispatch_db, session_cookie_secure=secure)
    login = client.post(
        "/api/v1/auth/login",
        headers=TEST_CSRF_HEADERS,
        json={"loginid": "synthetic-login", "userpassword": "synthetic-password"},
    )
    assert login.status_code == 200
    original = client.cookies.get("eternalai_session")
    assert client.get("/api/v1/me").status_code == 200
    response = client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    for attr in ("Path=/api/v1", "HttpOnly", "SameSite=lax", "Max-Age=0", "expires="):
        assert attr in cookie
    assert ("Secure" in cookie) is secure
    assert "Domain=" not in cookie
    assert client.cookies.get("eternalai_session") is None
    assert run(
        PostgreSQLSessionRevocationStore(dispatch_db.factory).is_revoked(
            tokens.inspect(original).fingerprint
        )
    )
    set_ticket(client, original)
    assert_auth_denied(client.get("/api/v1/me"))
    client.close()


def test_logout_is_idempotent_for_revoked_absent_and_invalid_tickets(dispatch_db):
    client, tokens, _ = logout_client(dispatch_db)
    original = tokens.issue(_principal("idempotent"))
    for ticket in (original, original, "invalid", legacy_ticket(_principal("old"), 1000), None):
        client.cookies.clear()
        if ticket is not None:
            set_ticket(client, ticket)
        response = client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS)
        assert response.status_code == 200
        assert response.json() == {"authenticated": False}
        assert response.headers["cache-control"] == "no-store"
    assert revocation_count(dispatch_db) == 1
    client.close()


def test_logout_changes_only_one_of_two_same_user_sessions(dispatch_db):
    client, tokens, _ = logout_client(dispatch_db)
    a = _principal("shared")
    principals = (
        a,
        a,
        _principal("other"),
        a.model_copy(update={"org_ctx": PrincipalOrgContext(tenant_id="synthetic-second")}),
    )
    tickets = [tokens.issue(p) for p in principals]
    for ticket in tickets:
        set_ticket(client, ticket)
        assert client.get("/api/v1/me").status_code == 200
    set_ticket(client, tickets[0])
    assert client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS).status_code == 200
    for index, ticket in enumerate(tickets):
        set_ticket(client, ticket)
        assert client.get("/api/v1/me").status_code == (401 if index == 0 else 200)
    client.close()


def test_logout_does_not_revoke_oa_bindings_or_background_credentials(dispatch_db):
    from datetime import timedelta
    from uuid import uuid4

    from pydantic import SecretStr
    from sqlalchemy import event

    from app.infra.auth.postgresql import PostgreSQLCredentialStore
    from app.ports.auth import OASessionCredential
    from app.ports.credential_binding import PasswordBindingCredential

    store = PostgreSQLCredentialStore(
        session_factory=dispatch_db.factory, encryption_key=bytes(range(32))
    )
    principal = _principal("logout")
    run(
        store.store(
            principal.ai_user_id,
            "oa",
            OASessionCredential(
                oa_user_id=SecretStr(uuid4().hex),
                cookies={"synthetic": SecretStr(uuid4().hex)},
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )
    )
    run(
        store.bind_password(
            principal.ai_user_id,
            "oa",
            PasswordBindingCredential(
                login_id=SecretStr(uuid4().hex),
                password=SecretStr(uuid4().hex),
            ),
        )
    )
    with dispatch_db.sql.connect() as connection:
        before = list(connection.execute(text("SELECT * FROM oa_session_credentials")))
    assert len(before) == 1
    assert run(store.load(principal.ai_user_id, "oa")) is not None
    assert run(store.get_password_binding(principal.ai_user_id, "oa")).bound
    touched = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if "oa_session_credentials" in statement.lower():
            touched.append(True)

    client, tokens, _ = logout_client(dispatch_db)
    set_ticket(client, tokens.issue(principal))
    event.listen(dispatch_db.engine.sync_engine, "before_cursor_execute", observe)
    try:
        assert client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS).status_code == 200
        assert touched == []
    finally:
        event.remove(dispatch_db.engine.sync_engine, "before_cursor_execute", observe)
        client.close()
    with dispatch_db.sql.connect() as connection:
        preserved = list(connection.execute(text("SELECT * FROM oa_session_credentials"))) == before
    assert preserved
    assert run(store.load(principal.ai_user_id, "oa")) is not None
    assert run(store.get_password_binding(principal.ai_user_id, "oa")).bound
    assert revocation_count(dispatch_db) == 1


def test_logout_ignores_target_identity_inputs(dispatch_db):
    client, tokens, _ = logout_client(dispatch_db)
    a, b = tokens.issue(_principal("a")), tokens.issue(_principal("b"))
    set_ticket(client, a)
    assert (
        client.post(
            "/api/v1/auth/logout?user=b",
            headers={**TEST_CSRF_HEADERS, "X-User": "b"},
            json={"user": "b"},
        ).status_code
        == 200
    )
    set_ticket(client, b)
    assert client.get("/api/v1/me").status_code == 200
    set_ticket(client, a)
    assert_auth_denied(client.get("/api/v1/me"))
    client.close()


@pytest.mark.parametrize(
    "fault", ["missing_store", "read", "write", "missing_tokens", "inspect", "ack_loss"]
)
def test_logout_storage_failure_preserves_cookie_and_returns_503(dispatch_db, fault):
    tokens = _token_port()
    original = tokens.issue(_principal("failure"))
    healthy = PostgreSQLSessionRevocationStore(dispatch_db.factory)

    class FaultStore:
        async def is_revoked(self, fingerprint):
            if fault == "read":
                raise RuntimeError("synthetic-read-failure")
            return await healthy.is_revoked(fingerprint)

        async def revoke(self, fingerprint, *, expires_at):
            if fault == "ack_loss":
                await healthy.revoke(fingerprint, expires_at=expires_at)
            raise RuntimeError("synthetic-write-failure")

    class FaultTokens:
        def inspect(self, token):
            raise RuntimeError("synthetic-inspect-failure")

    profile = StubUserProfile()
    app = create_app(
        session_tokens=None
        if fault == "missing_tokens"
        else FaultTokens()
        if fault == "inspect"
        else tokens,
        session_revocations=None if fault == "missing_store" else FaultStore(),
        user_profile=profile,
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    with TestClient(
        app, base_url="https://testserver", backend_options={"loop_factory": make_event_loop}
    ) as client:
        contexts = _capture_http_exception_contexts(app)
        set_ticket(client, original)
        if fault in {"missing_store", "read", "missing_tokens", "inspect"}:
            response = client.get("/api/v1/me")
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "authentication_unavailable"
            assert profile.profile_calls == []
        response = client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS)
        assert response.status_code == 503
        assert response.json() == {
            "detail": {
                "code": "logout_unavailable",
                "message": "Logout is temporarily unavailable.",
            }
        }
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert all(context is None for context in contexts)
    if fault == "ack_loss":
        assert run(healthy.is_revoked(tokens.inspect(original).fingerprint))
    recovered, _, _ = logout_client(dispatch_db, tokens=tokens)
    set_ticket(recovered, original)
    assert recovered.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS).status_code == 200
    recovered.close()


class SuccessfulAuthentication:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.calls = 0

    async def authenticate(self, credential: LoginCredential) -> Principal:
        self.calls += 1
        assert credential.loginid.get_secret_value()
        assert credential.userpassword.get_secret_value()
        return self.principal


class FailedAuthentication:
    async def authenticate(self, credential: LoginCredential) -> Principal:
        raise AuthenticationError("synthetic upstream detail")


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def handle_user_message(
        self,
        channel: str,
        principal: Principal,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        self.calls.append((principal.ai_user_id, session_id))
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-auth",
            task_id="task-auth",
            session_id=session_id,
            message="ok",
            fallback_text="ok",
            trace_id="trace-auth",
            status="completed",
        )

    async def handle_user_action(
        self,
        channel: str,
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        del channel, action
        self.calls.append((principal.ai_user_id, session_id))
        return ResponseEnvelopeBuilder().build_message(
            "response-auth-action",
            "task-auth-action",
            session_id,
            "ok",
            "ok",
            "trace-auth-action",
        )


def _principal(label: str, *, roles: tuple[str, ...] = ("admin",)) -> Principal:
    return Principal(
        ai_user_id=f"usr_v1_{label}",
        display_name=f"Synthetic {label}",
        roles=roles,
        org_ctx=PrincipalOrgContext(),
    )


def _token_port() -> HMACSessionToken:
    return HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=3600)


def _binder() -> PrincipalSessionBinder:
    return PrincipalSessionBinder(binding_key=bytes(reversed(range(32))))


def _capture_http_exception_contexts(
    application: FastAPI,
) -> list[BaseException | None]:
    contexts: list[BaseException | None] = []

    async def capture(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPException)
        contexts.append(exc.__context__)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    application.add_exception_handler(HTTPException, capture)
    return contexts


def _action_body(session_id: str = "action-client-session") -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": session_id,
        "action": {
            "action_type": "confirm",
            "response_id": "response-auth-action",
            "confirmed": True,
        },
    }


def test_login_sets_only_a_secure_http_only_session_cookie() -> None:
    principal = _principal("login")
    authentication = SuccessfulAuthentication(principal)
    tokens = _token_port()
    client = TestClient(
        create_app(
            authentication=authentication,
            session_revocations=MemorySessionRevocations(),
            session_tokens=tokens,
            session_binder=_binder().bind,
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    synthetic_loginid = "1" * 17 + "X"
    synthetic_password = "synthetic-" + "password"

    response = client.post(
        "/api/v1/auth/login",
        headers=TEST_CSRF_HEADERS,
        json={"loginid": synthetic_loginid, "userpassword": synthetic_password},
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    set_cookie = response.headers["set-cookie"]
    assert "eternalai_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/v1" in set_cookie
    assert synthetic_loginid not in response.text
    assert synthetic_password not in response.text
    assert authentication.calls == 1


def test_login_can_disable_only_secure_for_an_http_deployment() -> None:
    origin = "http://testserver"
    authentication = SuccessfulAuthentication(_principal("http-login"))
    client = TestClient(
        create_app(
            authentication=authentication,
            session_revocations=MemorySessionRevocations(),
            session_tokens=_token_port(),
            session_binder=_binder().bind,
            session_cookie_ttl_seconds=3600,
            session_cookie_secure=False,
            csrf_allowed_origins=frozenset({origin}),
        ),
        base_url=origin,
    )

    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": origin, "X-EternalAI-CSRF": "1"},
        json={"loginid": "synthetic-login", "userpassword": "synthetic-secret"},
    )

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "eternalai_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" not in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/v1" in set_cookie


def test_login_failure_is_generic_and_sets_no_cookie() -> None:
    application = create_app(
        authentication=FailedAuthentication(),
        session_revocations=MemorySessionRevocations(),
        session_tokens=_token_port(),
        session_binder=_binder().bind,
        session_cookie_ttl_seconds=3600,
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    contexts = _capture_http_exception_contexts(application)
    client = TestClient(
        application,
        base_url="https://testserver",
    )

    response = client.post(
        "/api/v1/auth/login",
        headers=TEST_CSRF_HEADERS,
        json={"loginid": "synthetic-login", "userpassword": "synthetic-secret"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "code": "authentication_failed",
            "message": "Authentication failed.",
        }
    }
    assert "set-cookie" not in response.headers
    assert "upstream" not in response.text
    assert contexts == [None]


def test_malformed_login_body_is_generic_401_without_credential_echo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = create_app(
        authentication=FailedAuthentication(),
        session_revocations=MemorySessionRevocations(),
        session_tokens=_token_port(),
        session_binder=_binder().bind,
        session_cookie_ttl_seconds=3600,
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    contexts = _capture_http_exception_contexts(application)
    client = TestClient(
        application,
        base_url="https://testserver",
    )
    loginid_marker = "MARKER-LOGINID-MUST-NOT-ECHO"
    password_marker = "MARKER-PASSWORD-MUST-NOT-ECHO"
    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/api/v1/auth/login",
        headers=TEST_CSRF_HEADERS,
        json={
            "loginid": {"raw": loginid_marker},
            "userpassword": {"raw": password_marker},
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "code": "authentication_failed",
            "message": "Authentication failed.",
        }
    }
    assert loginid_marker not in response.text
    assert password_marker not in response.text
    assert loginid_marker not in caplog.text
    assert password_marker not in caplog.text
    assert "set-cookie" not in response.headers
    assert contexts == [None]


def test_declared_oversized_login_body_is_rejected_before_body_read() -> None:
    async def unread_receive() -> dict[str, Any]:
        raise AssertionError("request body must not be read")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [(b"content-length", b"16385")],
        },
        unread_receive,
    )

    assert asyncio.run(_parse_login_credential(request)) is None


def test_login_openapi_contract_declares_login_credential_body() -> None:
    operation = create_app().openapi()["paths"]["/api/v1/auth/login"]["post"]

    request_body = operation["requestBody"]
    assert request_body["required"] is True
    schema = request_body["content"]["application/json"]["schema"]
    assert schema["title"] == "LoginCredential"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"loginid", "userpassword"}
    assert schema["properties"]["loginid"]["format"] == "password"
    assert schema["properties"]["loginid"]["writeOnly"] is True
    assert schema["properties"]["userpassword"]["format"] == "password"
    assert schema["properties"]["userpassword"]["writeOnly"] is True


def test_missing_token_wins_over_invalid_runtime_body_and_role_header() -> None:
    response = TestClient(create_app(csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS)).post(
        "/api/v1/runtime/handle",
        headers={"X-EternalAI-Roles": "admin"},
        json={"unexpected": "body"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


def test_runtime_action_requires_authentication_before_body_validation() -> None:
    response = TestClient(create_app(csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS)).post(
        "/api/v1/runtime/action",
        headers={"X-EternalAI-Roles": "admin"},
        json={"unexpected": "body"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


def test_runtime_action_hides_session_binding_errors_before_runtime() -> None:
    runtime = RecordingRuntime()
    session_tokens = StaticSessionTokens()

    def reject_session(_principal: Principal, _session_id: str) -> str:
        raise SessionBindingError("synthetic binding detail")

    client = TestClient(
        create_app(
            runtime=runtime,
            session_revocations=MemorySessionRevocations(),
            session_tokens=session_tokens,
            session_binder=reject_session,
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())

    response = client.post(
        "/api/v1/runtime/action",
        headers=TEST_CSRF_HEADERS,
        json=_action_body(),
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_not_found"
    assert runtime.calls == []


def test_runtime_action_fails_closed_without_runtime_provider() -> None:
    client = TestClient(
        create_app(
            session_revocations=MemorySessionRevocations(),
            session_tokens=StaticSessionTokens(),
            session_binder=_binder().bind,
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())

    response = client.post(
        "/api/v1/runtime/action",
        headers=TEST_CSRF_HEADERS,
        json=_action_body(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_unavailable"


def test_cross_principal_bound_session_is_hidden_before_runtime() -> None:
    tokens = _token_port()
    binder = _binder()
    runtime = RecordingRuntime()
    app = create_app(
        runtime=runtime,
        session_revocations=MemorySessionRevocations(),
        session_tokens=tokens,
        session_binder=binder.bind,
        session_cookie_ttl_seconds=3600,
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    client = TestClient(app, base_url="https://testserver")
    body = {
        "channel": "web",
        "session_id": "shared-client-session",
        "message": "hello",
        "client_capabilities": {},
    }

    token_b = tokens.issue(_principal("b"))
    response_b = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        cookies={"eternalai_session": token_b},
        json=body,
    )
    bound_b = response_b.json()["session_id"]

    token_a = tokens.issue(_principal("a"))
    response_a = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        cookies={"eternalai_session": token_a},
        json={**body, "session_id": bound_b},
    )

    assert response_b.status_code == 200
    assert response_a.status_code == 404
    assert response_a.json()["detail"]["code"] == "session_not_found"
    assert runtime.calls == [("usr_v1_b", bound_b)]


def revocation_count(db):
    with db.sql.connect() as c:
        return c.execute(text("SELECT count(*) FROM auth_session_revocations")).scalar_one()


def test_requests_authenticated_after_logout_commit_are_denied(dispatch_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()
    healthy = PostgreSQLSessionRevocationStore(dispatch_db.factory)

    class BarrierStore:
        async def is_revoked(self, fingerprint):
            return await healthy.is_revoked(fingerprint)

        async def revoke(self, fingerprint, *, expires_at):
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
            await healthy.revoke(fingerprint, expires_at=expires_at)

    client, tokens, _ = logout_client(dispatch_db, store=BarrierStore())
    peer, _, profile = logout_client(dispatch_db, tokens=tokens)
    original = tokens.issue(_principal("barrier"))
    set_ticket(client, original)
    set_ticket(peer, original)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, "/api/v1/auth/logout", headers=TEST_CSRF_HEADERS)
        try:
            assert entered.wait(10)
            assert not pending.done()
            assert peer.get("/api/v1/me").status_code == 200
        finally:
            release.set()
        assert pending.result(10).status_code == 200
    calls = len(profile.profile_calls)
    for _ in range(3):
        assert_auth_denied(peer.get("/api/v1/me"))
    assert len(profile.profile_calls) == calls
    client.close()
    peer.close()


def test_legacy_ticket_copies_are_one_session_without_revoking_other_tickets(dispatch_db):
    client, tokens, _ = logout_client(dispatch_db)
    principal = _principal("legacy")
    now = int(datetime.now(UTC).timestamp())
    original = legacy_ticket(principal, now)
    independent = (legacy_ticket(principal, now - 1), tokens.issue(principal))
    for ticket in (original, *independent):
        set_ticket(client, ticket)
        assert client.get("/api/v1/me").status_code == 200
    set_ticket(client, original)
    assert client.post("/api/v1/auth/logout", headers=TEST_CSRF_HEADERS).status_code == 200
    # Separately constructed apps and connections share only PostgreSQL state.
    for ticket in (original, encoding_alias(original)):
        rebuilt, _, _ = logout_client(dispatch_db, tokens=tokens)
        set_ticket(rebuilt, ticket)
        assert_auth_denied(rebuilt.get("/api/v1/me"))
        rebuilt.close()
    for ticket in independent:
        set_ticket(client, ticket)
        assert client.get("/api/v1/me").status_code == 200
    client.close()
