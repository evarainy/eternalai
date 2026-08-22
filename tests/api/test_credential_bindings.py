"""Credential binding API isolation and secret-leak canaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.credential_bindings import CredentialBindingService, make_router
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.auth import LoginCredential, Principal, PrincipalOrgContext
from app.ports.credential_binding import (
    CredentialBindingView,
    CredentialTargetSystem,
    PasswordBindingCredential,
)

PRINCIPAL = Principal(
    ai_user_id="usr_v1_synthetic",
    display_name="Synthetic User",
    roles=(),
    org_ctx=PrincipalOrgContext(),
)


class FakeAuthentication:
    def __init__(self, *, fail_with: str | None = None) -> None:
        self.fail_with = fail_with
        self.calls = 0

    async def verify_for_binding(self, credential: LoginCredential) -> Principal:
        self.calls += 1
        if self.fail_with is not None:
            raise RuntimeError(self.fail_with)
        assert credential.loginid.get_secret_value()
        assert credential.userpassword.get_secret_value()
        return PRINCIPAL


class FakeStore:
    def __init__(self) -> None:
        self.views: dict[CredentialTargetSystem, CredentialBindingView] = {}

    async def bind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        credential: PasswordBindingCredential,
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
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
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
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        assert ai_user_id == PRINCIPAL.ai_user_id
        view = (await self.get_password_binding(ai_user_id, target_system)).model_copy(
            update={"poll_status": "unbound", "bound": False}
        )
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
