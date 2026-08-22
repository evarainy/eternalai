"""Typed OA credential-acquisition failure classification tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr

from app.infra.auth.background import OAPasswordCredentialAcquirer
from app.infra.auth.oa import (
    OAAuthenticationError,
    OAInvalidResponseError,
    OANetworkUnavailableError,
    OATimeoutError,
    OAUpstreamServerError,
)
from app.ports.auth import LoginCredential, Principal, PrincipalOrgContext
from app.ports.credential_binding import (
    CredentialAcquisitionError,
    CredentialAcquisitionFailureCode,
    CredentialPollCandidate,
    CredentialTargetSystem,
    PasswordBindingCredential,
)

CANDIDATE = CredentialPollCandidate(
    ai_user_id="usr_v1_synthetic",
    target_system="oa",
    poll_failure_count=0,
    updated_at=datetime(2026, 8, 21, tzinfo=UTC),
)
PRINCIPAL = Principal(
    ai_user_id=CANDIDATE.ai_user_id,
    display_name="Synthetic User",
    roles=(),
    org_ctx=PrincipalOrgContext(),
)


class FakeSession:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.payload = (
            {"loginSetting": {"hasValidateCode": False}}
            if payload is None
            else payload
        )
        self.failure = failure

    async def get_json(
        self,
        path: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        del path, params
        raise AssertionError("captcha preflight must use post_form")

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        assert path == "/api/hrm/login/getLoginForm"
        assert fields == {}
        if self.failure is not None:
            raise self.failure
        return self.payload

    def cookies(self) -> dict[str, str]:
        return {}


class FakePasswordReader:
    def __init__(self) -> None:
        self.calls = 0

    async def load_password_for_poll(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> PasswordBindingCredential:
        assert (ai_user_id, target_system) == (CANDIDATE.ai_user_id, "oa")
        self.calls += 1
        return PasswordBindingCredential(
            login_id=SecretStr("LOGIN-CANARY"),
            password=SecretStr("PASSWORD-CANARY"),
        )


class FakeAuthentication:
    def __init__(self, failure: OAAuthenticationError | None = None) -> None:
        self.failure = failure
        self.calls = 0

    async def authenticate(
        self,
        credential: LoginCredential,
        *,
        reactivate_revoked_session: bool = True,
    ) -> Principal:
        assert credential.loginid.get_secret_value() == "LOGIN-CANARY"
        assert credential.userpassword.get_secret_value() == "PASSWORD-CANARY"
        assert reactivate_revoked_session is False
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return PRINCIPAL


def _acquirer(
    session: FakeSession,
    *,
    authentication: FakeAuthentication | None = None,
    reader: FakePasswordReader | None = None,
) -> tuple[OAPasswordCredentialAcquirer, FakeAuthentication, FakePasswordReader]:
    resolved_authentication = authentication or FakeAuthentication()
    resolved_reader = reader or FakePasswordReader()
    return (
        OAPasswordCredentialAcquirer(
            session_factory=lambda: session,
            authentication=resolved_authentication,
            binding_store=resolved_reader,
        ),
        resolved_authentication,
        resolved_reader,
    )


@pytest.mark.parametrize("captcha_value", [True, 1, "1", "true", " TRUE "])
def test_captcha_preflight_stops_before_password_or_authentication(
    captcha_value: object,
) -> None:
    acquirer, authentication, reader = _acquirer(
        FakeSession({"loginSetting": {"hasValidateCode": captcha_value}})
    )

    with pytest.raises(CredentialAcquisitionError) as captured:
        asyncio.run(acquirer.acquire(CANDIDATE))

    assert captured.value.code == "captcha_required"
    assert authentication.calls == 0
    assert reader.calls == 0


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (OANetworkUnavailableError(), "network_unreachable"),
        (OATimeoutError(), "timeout"),
        (OAUpstreamServerError(), "upstream_5xx"),
        (OAInvalidResponseError(), "invalid_response"),
    ],
)
def test_only_explicit_oa_external_failures_receive_countable_codes(
    failure: Exception,
    expected_code: CredentialAcquisitionFailureCode,
) -> None:
    acquirer, _, _ = _acquirer(FakeSession(failure=failure))

    with pytest.raises(CredentialAcquisitionError) as captured:
        asyncio.run(acquirer.acquire(CANDIDATE))

    assert captured.value.code == expected_code


def test_password_rejection_keeps_a_distinct_non_retry_code() -> None:
    authentication = FakeAuthentication(
        OAAuthenticationError(
            "oa_credentials_rejected",
            failure_kind="credentials_rejected",
        )
    )
    acquirer, _, _ = _acquirer(
        FakeSession(),
        authentication=authentication,
    )

    with pytest.raises(CredentialAcquisitionError) as captured:
        asyncio.run(acquirer.acquire(CANDIDATE))

    assert captured.value.code == "credentials_rejected"
    assert "PASSWORD-CANARY" not in str(captured.value)


def test_unclassified_preflight_failure_is_local_and_non_countable() -> None:
    acquirer, _, _ = _acquirer(FakeSession(failure=RuntimeError("unknown")))

    with pytest.raises(CredentialAcquisitionError) as captured:
        asyncio.run(acquirer.acquire(CANDIDATE))

    assert captured.value.code == "local_failure"
