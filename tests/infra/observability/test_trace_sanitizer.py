from __future__ import annotations

from copy import deepcopy

import pytest

from app.infra.observability.sanitizer import redact_trace_attributes
from app.ports.trace import redact_trace_attributes as contract_redact_trace_attributes


def test_infra_sanitizer_is_the_trace_contract_compatibility_export() -> None:
    assert redact_trace_attributes is contract_redact_trace_attributes


def test_redacts_bearer_token_value() -> None:
    payload = {"auth_header": "Bearer " + "eyABC123"}

    assert redact_trace_attributes(payload) == {"auth_header": "[REDACTED]"}


def test_redacts_sessionid_value() -> None:
    session_key = "session_" + "id"
    payload = {session_key: "abc123"}

    assert redact_trace_attributes(payload) == {session_key: "[REDACTED]"}


def test_redacts_access_token_value() -> None:
    credential_key = "access_" + "token"
    payload = {credential_key: "abc123"}

    assert redact_trace_attributes(payload) == {credential_key: "[REDACTED]"}


def test_redacts_refresh_token_value() -> None:
    credential_key = "refresh_" + "token"
    payload = {credential_key: "abc123"}

    assert redact_trace_attributes(payload) == {credential_key: "[REDACTED]"}


def test_redacts_cookie_value() -> None:
    payload = {"cookie": "abc123"}

    assert redact_trace_attributes(payload) == {"cookie": "[REDACTED]"}


def test_redacts_set_cookie_value() -> None:
    payload = {"set-cookie": "abc123"}

    assert redact_trace_attributes(payload) == {"set-cookie": "[REDACTED]"}


def test_redacts_value_under_innocuous_key() -> None:
    payload = {"auth_header": "Bearer " + "eyABC123"}

    assert redact_trace_attributes(payload) == {"auth_header": "[REDACTED]"}


def test_preserves_safe_attributes_unchanged() -> None:
    payload = {
        "action": "query",
        "capability_id": "oa_pending_workflows",
        "safe_nested": {"count": 2, "items": ["todo", "approved"]},
    }

    assert redact_trace_attributes(payload) == payload
    assert redact_trace_attributes(payload) is not payload


def test_non_str_value_passes_through() -> None:
    payload = {"count": 42}

    result = redact_trace_attributes(payload)

    assert result == {"count": 42}
    assert result["count"] is payload["count"]


@pytest.mark.parametrize(
    "credential_key",
    (
        "Authorization",
        "Proxy-Authorization",
        "X-Api-Key",
        "X-Auth-Token",
        "X-Access-Token",
        "X-CSRF-Token",
        "token",
        "PASSWORD",
        "passwd",
        "Api-Key",
        "api_key",
        "SECRET",
        "Client_Secret",
        "PRIVATE-KEY",
        "SessionID",
        "accessToken",
        "refreshToken",
        "apiKey",
        "clientSecret",
        "privateKey",
    ),
)
def test_redacts_all_required_credential_key_families(credential_key: str) -> None:
    payload = {credential_key: "synthetic-" + "credential-value"}

    assert redact_trace_attributes(payload) == {credential_key: "[REDACTED]"}


@pytest.mark.parametrize(
    "credential_assignment",
    (
        "password=" + "synthetic-value",
        "passwd=" + "synthetic-value",
        "api_key=" + "synthetic-value",
        "client-secret=" + "synthetic-value",
        "private_key=" + "synthetic-value",
    ),
)
def test_redacts_credential_assignments_under_safe_key(
    credential_assignment: str,
) -> None:
    payload = {"message": credential_assignment}

    assert redact_trace_attributes(payload) == {"message": "[REDACTED]"}


@pytest.mark.parametrize(
    "credential_uri",
    (
        "postgresql+psycopg://alice:hunter2@db/app",
        "https://service-user:synthetic-password@example.test/resource",
    ),
)
def test_redacts_uri_userinfo_under_safe_key(credential_uri: str) -> None:
    assert redact_trace_attributes({"dsn": credential_uri}) == {
        "dsn": "[REDACTED]"
    }


def test_nested_redaction_does_not_mutate_input() -> None:
    payload = {
        "safe": [
            {"Password": "synthetic-" + "password-value"},
            {"nested": {"client_secret": "synthetic-" + "secret-value"}},
        ],
        "token_count": 42,
    }
    original = deepcopy(payload)

    result = redact_trace_attributes(payload)

    assert result == {
        "safe": [
            {"Password": "[REDACTED]"},
            {"nested": {"client_secret": "[REDACTED]"}},
        ],
        "token_count": 42,
    }
    assert payload == original
    assert result is not payload
    assert result["safe"] is not payload["safe"]


def test_safe_telemetry_names_are_not_over_redacted() -> None:
    payload = {
        "token_count": 512,
        "session_duration": 12.5,
        "secretary_name": "synthetic assistant",
        "password_policy": "minimum length twelve",
        "capability_id": "oa.synthetic.query",
    }

    assert redact_trace_attributes(payload) == payload


@pytest.mark.parametrize(
    "credential_key",
    (
        "loginid",
        "login_id",
        "userpassword",
        "oa_userid",
        "userid",
        "ecology_JSessionid",
        "loginidweaver",
        "loginuuids",
        "__clusterSessionCookieName",
        "__clusterSessionIDCookieName",
        "oa_cookies",
        "credential_ciphertext",
        "encrypted_loginid",
        "encrypted_userpassword",
        "rsa_code",
    ),
)
def test_redacts_oa_authentication_credential_keys(credential_key: str) -> None:
    payload = {credential_key: "synthetic-" + "oa-auth-value"}

    assert redact_trace_attributes(payload) == {credential_key: "[REDACTED]"}


@pytest.mark.parametrize("digits", [15, 17])
def test_redacts_national_identity_number_under_safe_nested_key(digits: int) -> None:
    suffix = "X" if digits == 17 else ""
    synthetic_identity_number = "1" * digits + suffix
    payload = {"safe": [{"message": synthetic_identity_number}]}

    result = redact_trace_attributes(payload)

    assert result == {"safe": [{"message": "[REDACTED]"}]}
    assert synthetic_identity_number not in str(result)
