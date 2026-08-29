from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.v1.auth import _parse_login_credential
from app.contracts.sdui.models import UserAction
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
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
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    StaticSessionTokens,
    auth_cookies,
)


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
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        self.calls.append((ai_user_id, session_id))
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
    response = TestClient(
        create_app(csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS)
    ).post(
        "/api/v1/runtime/handle",
        headers={"X-EternalAI-Roles": "admin"},
        json={"unexpected": "body"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


def test_runtime_action_requires_authentication_before_body_validation() -> None:
    response = TestClient(
        create_app(csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS)
    ).post(
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
