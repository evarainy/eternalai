from __future__ import annotations

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
