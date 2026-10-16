"""Credential binding API isolation and secret-leak canaries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.credential_bindings import CredentialBindingService, make_router
from app.api.v1.csrf import make_csrf_protected_principal, make_require_csrf
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.auth import (
    AuthenticatedSessionContext,
    CredentialSnapshot,
    CredentialWriteStamp,
    LoginCredential,
    OASessionCredential,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.credential_binding import (
    BrowserBindingActivationRequest,
    BrowserBindingActivationView,
    BrowserBindingStatusView,
    CredentialBindingVerification,
    CredentialBindingView,
    CredentialTargetSystem,
    PasswordBindingCredential,
)


def _verification(principal: Principal) -> CredentialBindingVerification:
    subject = principal.org_ctx.directory_user_id or "synthetic-oa"
    return CredentialBindingVerification(
        principal=principal,
        session_credential=OASessionCredential(
            oa_user_id=SecretStr(subject),
            cookies={"synthetic-session": SecretStr("synthetic-cookie")},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
    )


def _activation_fixture():
    principal = PRINCIPAL.model_copy(update={
        "org_ctx": PrincipalOrgContext(tenant_id="default", directory_user_id="synthetic-oa"),
    })
    captured = AuthenticatedSessionContext(
        principal, bytes(range(32)), datetime.now(UTC) + timedelta(minutes=10),
    )
    store = FakeStore()
    store.browser_activation_snapshot = AsyncMock(return_value=CredentialSnapshot(
        "default", principal.ai_user_id, "oa", "synthetic-binding", 3, 2, 1,
    ))
    store.activate_browser_binding = AsyncMock(return_value=BrowserBindingActivationView(
        binding_id="synthetic-binding", binding_revision=4, verified_at=datetime.now(UTC),
    ))
    store.get_browser_binding_status = AsyncMock(return_value=BrowserBindingStatusView(
        binding_id="synthetic-binding", binding_revision=3, binding_state="unverified",
    ))
    verifier = Mock()
    verifier.verify_for_binding = AsyncMock(return_value=_verification(principal))
    request = BrowserBindingActivationRequest(
        login_id=SecretStr("ACTIVATION-LOGIN-CANARY"),
        password=SecretStr("ACTIVATION-PASSWORD-CANARY"),
        binding_id="synthetic-binding", binding_revision=3,
    )
    service = CredentialBindingService(store=cast(Any, store), verifier=verifier)
    return service, store, verifier, request, captured

PRINCIPAL = Principal(
    ai_user_id="usr_v1_synthetic",
    display_name="Synthetic User",
    roles=(),
    org_ctx=PrincipalOrgContext(tenant_id="default"),
)


class FakeAuthentication:
    def __init__(self, *, fail_with: str | None = None) -> None:
        self.fail_with = fail_with
        self.calls = 0

    async def verify_for_binding(
        self, credential: LoginCredential
    ) -> CredentialBindingVerification:
        self.calls += 1
        if self.fail_with is not None:
            raise RuntimeError(self.fail_with)
        assert credential.loginid.get_secret_value()
        assert credential.userpassword.get_secret_value()
        return _verification(PRINCIPAL.model_copy(update={
            "org_ctx": PRINCIPAL.org_ctx.model_copy(update={"directory_user_id": "synthetic-oa"}),
        }))


class FakeStore:
    def __init__(self) -> None:
        self.views: dict[CredentialTargetSystem, CredentialBindingView] = {}

    async def snapshot(self, ai_user_id, target_system, *, tenant_id):
        return CredentialSnapshot(
            tenant_id, ai_user_id, target_system, "synthetic-binding", 1, 0, 0
        )

    async def claim_write(self, snapshot):
        return CredentialWriteStamp(
            snapshot, "synthetic-operation", datetime(2099, 1, 1, tzinfo=UTC)
        )

    @asynccontextmanager
    async def poll_lock(self, ai_user_id, target_system, *, tenant_id):
        yield True

    async def bind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        credential: PasswordBindingCredential,
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> CredentialBindingView:
        assert ai_user_id == PRINCIPAL.ai_user_id
        assert credential.password.get_secret_value()
        view = CredentialBindingView(
            target_system=target_system,
            poll_status="active",
            poll_failure_count=0,
            updated_at=datetime.now(UTC),
            bound=True,
        )
        self.views[target_system] = view
        return view

    async def get_password_binding(
        self, ai_user_id: str, target_system: CredentialTargetSystem, *, tenant_id: str
    ) -> CredentialBindingView:
        assert ai_user_id == PRINCIPAL.ai_user_id
        return self.views.get(
            target_system,
            CredentialBindingView(
                target_system=target_system,
                poll_status="unbound",
                poll_failure_count=0,
                updated_at=None,
                bound=False,
            ),
        )

    async def unbind_password(
        self, ai_user_id: str, target_system: CredentialTargetSystem, *, tenant_id: str
    ) -> CredentialBindingView:
        assert ai_user_id == PRINCIPAL.ai_user_id
        view = (
            await self.get_password_binding(ai_user_id, target_system, tenant_id="default")
        ).model_copy(update={"poll_status": "unbound", "bound": False})
        self.views[target_system] = view
        return view


def _client(authentication: FakeAuthentication, store: FakeStore) -> TestClient:
    async def require_principal(_request: Request) -> Principal:
        return PRINCIPAL

    app = FastAPI()
    app.include_router(
        make_router(
            CredentialBindingService(
                store=store,
                verifier=authentication,
            ),
            require_principal,
        ),
        prefix="/api/v1/credential-bindings",
    )
    return TestClient(app)


def test_bind_get_and_unbind_return_only_value_free_status() -> None:
    password = "SECRET-PASSWORD-CANARY"
    login_id = "SECRET-LOGIN-CANARY"
    client = _client(FakeAuthentication(), FakeStore())

    bound = client.put(
        "/api/v1/credential-bindings/oa",
        json={"login_id": login_id, "password": password},
    )
    queried = client.get("/api/v1/credential-bindings/oa")
    unbound = client.delete("/api/v1/credential-bindings/oa")

    assert bound.status_code == 200
    assert queried.status_code == 200
    assert unbound.status_code == 200
    rendered = bound.text + queried.text + unbound.text
    assert password not in rendered
    assert login_id not in rendered
    assert bound.json()["poll_status"] == "active"
    assert unbound.json()["poll_status"] == "unbound"


def test_authentication_failure_does_not_leak_plaintext_to_response_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    password = "SECRET-FAILURE-PASSWORD-CANARY"
    login_id = "SECRET-FAILURE-LOGIN-CANARY"
    client = _client(FakeAuthentication(fail_with=password), FakeStore())

    response = client.put(
        "/api/v1/credential-bindings/oa",
        json={"login_id": login_id, "password": password},
    )

    assert response.status_code == 400
    rendered = response.text + caplog.text
    assert password not in rendered
    assert login_id not in rendered
    assert response.json()["detail"]["code"] == "credential_binding_failed"


def test_password_binding_model_masks_plaintext_in_repr_and_json() -> None:
    password = "SECRET-MODEL-PASSWORD-CANARY"
    credential = PasswordBindingCredential(
        login_id=SecretStr("SECRET-MODEL-LOGIN-CANARY"),
        password=SecretStr(password),
    )

    assert password not in repr(credential)
    assert password not in credential.model_dump_json()


def test_non_oa_binding_is_stored_independently_without_using_oa_authentication() -> None:
    authentication = FakeAuthentication()
    client = _client(authentication, FakeStore())

    bound = client.put(
        "/api/v1/credential-bindings/u8",
        json={"login_id": "synthetic-u8", "password": "synthetic-secret"},
    )
    oa = client.get("/api/v1/credential-bindings/oa")

    assert bound.status_code == 200
    assert bound.json()["target_system"] == "u8"
    assert bound.json()["bound"] is True
    assert oa.json()["bound"] is False
    assert authentication.calls == 0


def test_binding_canary_never_calls_trace_or_response_envelope(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "CREDENTIAL-BOUNDARY-CANARY"
    trace_probe = AsyncMock(side_effect=AssertionError(f"trace:{canary}"))
    envelope_probe = Mock(side_effect=AssertionError(f"envelope:{canary}"))
    monkeypatch.setattr(PostgreSQLTraceWriter, "record_event", trace_probe)
    monkeypatch.setattr(ResponseEnvelopeBuilder, "_build_envelope", envelope_probe)
    client = _client(FakeAuthentication(), FakeStore())

    response = client.put(
        "/api/v1/credential-bindings/oa",
        json={"login_id": "synthetic-login", "password": canary},
    )

    assert response.status_code == 200
    assert canary not in response.text
    assert canary not in caplog.text
    trace_probe.assert_not_awaited()
    envelope_probe.assert_not_called()

    with pytest.raises(AssertionError, match=canary):
        asyncio.run(trace_probe(object()))
    with pytest.raises(AssertionError, match=canary):
        envelope_probe()


def test_browser_activation_uses_submitted_credential_and_exact_write_stamp() -> None:
    async def exercise():
        service, store, verifier, request, captured = _activation_fixture()
        token = authenticated_session.set(captured)
        try:
            view = await service.activate_browser_binding(captured.principal, request)
        finally:
            authenticated_session.reset(token)
        assert view.binding_revision == 4
        assert view.binding_state == "active"
        store.browser_activation_snapshot.assert_awaited_once_with(
            captured.principal, request.binding_id, 3,
        )
        supplied = verifier.verify_for_binding.await_args.args[0]
        assert supplied.loginid == request.login_id
        assert supplied.userpassword == request.password
        written = store.activate_browser_binding.await_args
        assert written.args[0].password == request.password
        assert written.kwargs["verified"] == captured.principal
        assert (written.kwargs["session_credential"]
                is verifier.verify_for_binding.return_value.session_credential)
        assert written.kwargs["captured_session"] == captured
        assert written.kwargs["expected_write"].snapshot.binding_revision == 3
        assert "ACTIVATION-PASSWORD-CANARY" not in view.model_dump_json()
        assert "ACTIVATION-LOGIN-CANARY" not in view.model_dump_json()

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["tenant", "user", "subject", "missing_subject"])
def test_browser_activation_rejects_verifier_identity_without_writing(fault) -> None:
    async def exercise():
        service, store, verifier, request, captured = _activation_fixture()
        principal = captured.principal
        if fault == "user":
            principal = principal.model_copy(update={"ai_user_id": "other-user"})
        else:
            principal = principal.model_copy(update={"org_ctx": PrincipalOrgContext(
                tenant_id="other-tenant" if fault == "tenant" else "default",
                directory_user_id=(None if fault == "missing_subject" else
                                   "other-subject" if fault == "subject" else "synthetic-oa"),
            )})
        verifier.verify_for_binding.return_value = _verification(principal)
        token = authenticated_session.set(captured)
        try:
            with pytest.raises(HTTPException) as error:
                await service.activate_browser_binding(captured.principal, request)
        finally:
            authenticated_session.reset(token)
        assert error.value.detail["code"] == "credential_binding_failed"
        store.activate_browser_binding.assert_not_awaited()

    asyncio.run(exercise())


def test_browser_activation_stale_selection_stops_before_upstream_login() -> None:
    async def exercise():
        service, store, verifier, request, captured = _activation_fixture()
        store.browser_activation_snapshot.return_value = CredentialSnapshot(
            "default", captured.principal.ai_user_id, "oa", "synthetic-binding", 4, 3, 1,
        )
        token = authenticated_session.set(captured)
        try:
            with pytest.raises(HTTPException) as error:
                await service.activate_browser_binding(captured.principal, request)
        finally:
            authenticated_session.reset(token)
        assert error.value.status_code == 400
        verifier.verify_for_binding.assert_not_awaited()
        store.activate_browser_binding.assert_not_awaited()

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["missing", "expired", "changed_during_verify"])
def test_browser_activation_requires_live_unchanged_request_session(fault) -> None:
    async def exercise():
        service, store, verifier, request, captured = _activation_fixture()
        if fault == "expired":
            captured = AuthenticatedSessionContext(
                captured.principal, captured.fingerprint, datetime.now(UTC) - timedelta(seconds=1),
            )
        if fault == "changed_during_verify":
            async def verify(_credential):
                authenticated_session.set(None)
                return _verification(captured.principal)
            verifier.verify_for_binding.side_effect = verify
        token = authenticated_session.set(None if fault == "missing" else captured)
        try:
            with pytest.raises(HTTPException) as error:
                await service.activate_browser_binding(captured.principal, request)
        finally:
            authenticated_session.reset(token)
        assert error.value.detail["code"] == "credential_binding_failed"
        store.activate_browser_binding.assert_not_awaited()
        if fault != "changed_during_verify":
            verifier.verify_for_binding.assert_not_awaited()
            store.browser_activation_snapshot.assert_not_awaited()

    asyncio.run(exercise())


def test_browser_activation_route_keeps_csrf_and_value_free_errors() -> None:
    service, store, verifier, request, captured = _activation_fixture()

    async def require_principal(_request: Request) -> Principal:
        authenticated_session.set(captured)
        return captured.principal

    app = FastAPI()
    app.include_router(make_router(service, make_csrf_protected_principal(
        require_principal, make_require_csrf({"http://testserver"}),
    )), prefix="/api/v1/credential-bindings")
    client = TestClient(app)
    body = {"binding_id": request.binding_id, "binding_revision": 3,
            "login_id": "ACTIVATION-LOGIN-CANARY", "password": "ACTIVATION-PASSWORD-CANARY"}
    path = "/api/v1/credential-bindings/oa/browser-activation"
    selected = client.get(path)
    assert selected.status_code == 200
    assert selected.json()["binding_id"] == request.binding_id
    assert selected.json()["binding_revision"] == request.binding_revision
    assert selected.json()["binding_state"] == "unverified"
    store.get_browser_binding_status.assert_awaited_once_with(captured.principal)
    denied = client.post(path, json=body)
    assert denied.status_code == 403
    verifier.verify_for_binding.assert_not_awaited()
    headers = {"Origin": "http://testserver", "X-EternalAI-CSRF": "1"}
    successful = client.post(path, json=body, headers=headers)
    assert successful.status_code == 200
    assert successful.json()["binding_revision"] == 4
    verifier.verify_for_binding.side_effect = RuntimeError("ACTIVATION-PASSWORD-CANARY")
    failed = client.post(path, json=body, headers=headers)
    assert failed.status_code == 400
    assert failed.json()["detail"]["code"] == "credential_binding_failed"
    for value in (selected.text, successful.text, failed.text):
        assert "ACTIVATION-PASSWORD-CANARY" not in value
        assert "ACTIVATION-LOGIN-CANARY" not in value
    assert store.activate_browser_binding.await_count == 1
