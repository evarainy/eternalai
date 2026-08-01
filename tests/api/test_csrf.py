"""Acceptance tests for the Cookie-authenticated CSRF boundary."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.admin.registry import (
    AdminBindingMutationResponse,
    AdminCapabilityCreate,
    AdminRegistryServiceWithBindingMutations,
    AdminRequestContext,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.main import create_app
from app.ports.auth import LoginCredential, Principal
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_envelope import ResponseEnvelope
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    TEST_ORIGIN,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)

_CSRF_REJECTION = {
    "detail": {
        "code": "csrf_validation_failed",
        "message": "CSRF validation failed.",
    }
}
_SAME_SITE_DIFFERENT_ORIGIN = "https://evil.example.gov.cn"
_CROSS_SITE_ORIGIN = "https://attacker.invalid"
_BINDING_ID = "synthetic-binding"


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_user_message(
        self,
        channel: str,
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        self.calls += 1
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-csrf",
            task_id="task-csrf",
            session_id=session_id,
            message="ok",
            fallback_text="ok",
            trace_id="trace-csrf",
            status="completed",
        )


class RecordingAdminService(AdminRegistryServiceWithBindingMutations):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_capabilities(
        self,
        context: AdminRequestContext,
    ) -> list[CapabilitySpec]:
        self.calls.append("list")
        return []

    async def create_capability(
        self,
        payload: AdminCapabilityCreate,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        self.calls.append("create")
        raise AssertionError("CSRF rejection must precede Registry creation")

    async def enable_capability(
        self,
        capability_id: str,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        self.calls.append("enable")
        raise AssertionError("CSRF rejection must precede Registry enable")

    async def disable_capability(
        self,
        capability_id: str,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        self.calls.append("disable")
        raise AssertionError("CSRF rejection must precede Registry disable")

    async def revoke_binding(
        self,
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        self.calls.append("revoke")
        raise AssertionError("CSRF rejection must precede binding revocation")

    async def reset_binding(
        self,
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        self.calls.append("reset")
        raise AssertionError("CSRF rejection must precede binding reset")


class RecordingAuthentication:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.calls = 0

    async def authenticate(self, credential: LoginCredential) -> Principal:
        self.calls += 1
        return self.principal


def _client(
    *,
    runtime: RecordingRuntime | None = None,
    admin_service: RecordingAdminService | None = None,
    authentication: RecordingAuthentication | None = None,
    authenticated: bool = True,
) -> TestClient:
    client = TestClient(
        create_app(
            runtime=runtime,
            admin_registry_service=admin_service,
            authentication=authentication,
            session_tokens=StaticSessionTokens(),
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url=TEST_ORIGIN,
    )
    if authenticated:
        client.cookies.update(auth_cookies())
    return client


def _runtime_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": "csrf-client-session",
        "message": "hello",
        "client_capabilities": {},
    }


def _registry_body() -> dict[str, Any]:
    return {
        "capability_id": "oa.synthetic.query",
        "name": "Synthetic query",
        "type": "query",
        "intent_tags": ["synthetic"],
        "input_schema": {},
        "output_schema": {},
        "input_schema_digest": "sha256:input",
        "output_schema_digest": "sha256:output",
        "risk_level": "low",
        "owner": "csrf-tests",
        "version": "1.0.0",
        "short_description": "CSRF acceptance fixture",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_required": False,
        "policy_digest": None,
    }


def test_same_origin_custom_header_allows_cookie_authenticated_runtime() -> None:
    runtime = RecordingRuntime()

    response = _client(runtime=runtime).post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json=_runtime_body(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert runtime.calls == 1


def test_same_site_different_origin_bodyless_disable_is_rejected_without_mutation() -> None:
    admin_service = RecordingAdminService()

    response = _client(admin_service=admin_service).post(
        "/api/v1/admin/registry/oa.synthetic.query/disable",
        headers={"Origin": _SAME_SITE_DIFFERENT_ORIGIN},
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert admin_service.calls == []


def test_cross_site_origin_is_rejected_even_with_custom_header() -> None:
    runtime = RecordingRuntime()
    headers = {**TEST_CSRF_HEADERS, "Origin": _CROSS_SITE_ORIGIN}

    response = _client(runtime=runtime).post(
        "/api/v1/runtime/handle",
        headers=headers,
        json=_runtime_body(),
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert runtime.calls == 0


def test_missing_origin_is_rejected_even_with_custom_header() -> None:
    runtime = RecordingRuntime()

    response = _client(runtime=runtime).post(
        "/api/v1/runtime/handle",
        headers={"X-EternalAI-CSRF": "1"},
        json=_runtime_body(),
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert runtime.calls == 0


def test_allowed_origin_without_custom_header_is_rejected() -> None:
    runtime = RecordingRuntime()

    response = _client(runtime=runtime).post(
        "/api/v1/runtime/handle",
        headers={"Origin": TEST_ORIGIN},
        json=_runtime_body(),
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert runtime.calls == 0


@pytest.mark.parametrize(
    "headers",
    (
        pytest.param(
            [
                ("Origin", TEST_ORIGIN),
                ("Origin", TEST_ORIGIN),
                ("X-EternalAI-CSRF", "1"),
            ],
            id="duplicate-origin",
        ),
        pytest.param(
            [
                ("Origin", TEST_ORIGIN),
                ("X-EternalAI-CSRF", "1"),
                ("X-EternalAI-CSRF", "1"),
            ],
            id="duplicate-csrf-header",
        ),
    ),
)
def test_duplicate_csrf_security_headers_are_rejected(
    headers: list[tuple[str, str]],
) -> None:
    runtime = RecordingRuntime()

    response = _client(runtime=runtime).post(
        "/api/v1/runtime/handle",
        headers=headers,
        json=_runtime_body(),
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert runtime.calls == 0


def test_admin_get_without_csrf_headers_keeps_read_behavior() -> None:
    admin_service = RecordingAdminService()

    response = _client(admin_service=admin_service).get(
        "/api/v1/admin/registry",
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert admin_service.calls == ["list"]


def test_login_without_csrf_headers_is_rejected_before_authentication() -> None:
    authentication = RecordingAuthentication(StaticSessionTokens().principal)

    response = _client(authentication=authentication, authenticated=False).post(
        "/api/v1/auth/login",
        json={"loginid": "synthetic-login", "userpassword": "synthetic-password"},
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert authentication.calls == 0
    assert "set-cookie" not in response.headers


def test_login_with_same_origin_custom_header_succeeds() -> None:
    authentication = RecordingAuthentication(StaticSessionTokens().principal)

    response = _client(authentication=authentication, authenticated=False).post(
        "/api/v1/auth/login",
        headers=TEST_CSRF_HEADERS,
        json={"loginid": "synthetic-login", "userpassword": "synthetic-password"},
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert authentication.calls == 1
    assert "eternalai_session=" in response.headers["set-cookie"]


_COOKIE_AUTHENTICATED_WRITES = (
    pytest.param(
        "/api/v1/admin/registry",
        _registry_body(),
        id="registry-create",
    ),
    pytest.param(
        "/api/v1/admin/registry/oa.synthetic.query/enable",
        None,
        id="registry-enable",
    ),
    pytest.param(
        "/api/v1/admin/registry/oa.synthetic.query/disable",
        None,
        id="registry-disable",
    ),
    pytest.param(
        f"/api/v1/admin/bindings/{_BINDING_ID}/revoke",
        None,
        id="binding-revoke",
    ),
    pytest.param(
        f"/api/v1/admin/bindings/{_BINDING_ID}/reset",
        None,
        id="binding-reset",
    ),
    pytest.param(
        "/api/v1/runtime/handle",
        _runtime_body(),
        id="runtime-handle",
    ),
)

_INVALID_CSRF_REQUESTS = (
    pytest.param(
        {"Origin": _SAME_SITE_DIFFERENT_ORIGIN},
        id="same-site-different-origin",
    ),
    pytest.param(
        {"Origin": _CROSS_SITE_ORIGIN, "X-EternalAI-CSRF": "1"},
        id="cross-site-origin",
    ),
    pytest.param(
        {"X-EternalAI-CSRF": "1"},
        id="missing-origin",
    ),
)


@pytest.mark.parametrize(
    ("path", "payload"),
    _COOKIE_AUTHENTICATED_WRITES,
)
@pytest.mark.parametrize(
    "headers",
    _INVALID_CSRF_REQUESTS,
)
def test_all_cookie_authenticated_writes_reject_invalid_csrf_matrix(
    path: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
) -> None:
    admin_service = RecordingAdminService()
    runtime = RecordingRuntime()
    request_kwargs: dict[str, Any] = {
        "headers": headers,
    }
    if payload is not None:
        request_kwargs["json"] = payload

    response = _client(runtime=runtime, admin_service=admin_service).post(
        path,
        **request_kwargs,
    )

    assert response.status_code == 403
    assert response.json() == _CSRF_REJECTION
    assert response.content != b""
    assert admin_service.calls == []
    assert runtime.calls == 0
