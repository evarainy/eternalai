from __future__ import annotations

from copy import deepcopy

import pytest

from app.infra.observability.sanitizer import redact_trace_attributes


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
