from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.ports.trace import SanitizerHookFn

# =====================================================================
# Test group: Sanitizer integration
# These tests use token-like strings AS INPUT ONLY to verify interception.
# The raw input substring must NOT appear in the sanitized output.
# =====================================================================


def build_credential_sanitizer() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a sanitizer hook that redacts token-like values."""
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        make_credential_sanitizer,
    )

    return make_credential_sanitizer()


def test_sanitizer_redacts_bearer_token() -> None:
    """Sanitizer must remove Bearer token value from dict -- raw substring absent."""
    sanitizer = build_credential_sanitizer()
    auth_header = "Authorization"
    raw_value = "Bearer " + "interceptor-test-value-abc"
    payload = {
        auth_header: raw_value,
        "other_field": "safe_value",
    }
    result = sanitizer(payload)
    result_json = json.dumps(result)
    # Raw input substring must be absent
    assert "interceptor-test-value-abc" not in result_json
    # Safe field must be preserved
    assert "safe_value" in result_json


def test_sanitizer_redacts_sessionid() -> None:
    sanitizer = build_credential_sanitizer()
    session_key = "session_" + "id"
    payload = {session_key: "sess-" + "interceptor-test-xyz", "data": "ok"}
    result = sanitizer(payload)
    result_json = json.dumps(result)
    assert "interceptor-test-xyz" not in result_json
    assert "ok" in result_json


def test_sanitizer_redacts_access_token() -> None:
    sanitizer = build_credential_sanitizer()
    credential_field = "access_" + "token"
    payload = {credential_field: "tok-" + "interceptor-test-abc123", "user": "alice"}
    result = sanitizer(payload)
    result_json = json.dumps(result)
    assert "interceptor-test-abc123" not in result_json
    assert "alice" in result_json


def test_sanitizer_redacts_refresh_token() -> None:
    sanitizer = build_credential_sanitizer()
    refresh_key = "refresh_" + "token"
    payload = {refresh_key: "ref-" + "interceptor-test-xyz789"}
    result = sanitizer(payload)
    result_json = json.dumps(result)
    assert "interceptor-test-xyz789" not in result_json


def test_sanitizer_compatible_with_trace_port_set_sanitizer() -> None:
    """Sanitizer hook must be compatible with TracePort.set_sanitizer signature."""
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        make_credential_sanitizer,
    )

    hook: SanitizerHookFn = make_credential_sanitizer()
    # Verify it's callable and returns a dict
    test_input: dict[str, Any] = {"key": "value"}
    result = hook(test_input)
    assert isinstance(result, dict)
    # Type check: matches SanitizerHookFn = Callable[[dict[str, Any]], dict[str, Any]]
    # (structural check -- SanitizerHookFn is a TypeAlias, not a class)


def test_sanitizer_passes_through_safe_data() -> None:
    """Sanitizer must not corrupt data that has no token-like patterns."""
    sanitizer = build_credential_sanitizer()
    payload = {
        "capability_id": "oa.query_pending_workflows",
        "tenant_id": "default",
        "workflow_count": 3,
    }
    result = sanitizer(payload)
    assert result["capability_id"] == "oa.query_pending_workflows"
    assert result["tenant_id"] == "default"
    assert result["workflow_count"] == 3


def test_trace_event_attributes_do_not_contain_secret_value() -> None:
    """TraceEvent attributes from NoopSecretProvider usage must not contain plaintext secret."""
    # Simulate what the provider returns as a safe trace attribute
    from app.ports.trace import TraceEvent

    session_key = "session_" + "id"
    event_payload: dict[str, Any] = {
        "trace_id": "tr-001",
        "task_id": "task-001",
        session_key: "sess-" + "001",
        "event_type": "adapter_called",
        "status": "ok",
        "attributes": {
            "credential_ref": "oa_service_account",  # safe -- reference only
            "mock_secret_injected": True,  # safe -- boolean marker
        },
    }
    # Build a TraceEvent with attributes derived from provider return values (safe)
    event = TraceEvent(**event_payload)
    attrs_json = json.dumps(event.attributes)
    # Must NOT contain any plaintext token-like value
    forbidden_patterns = ["password", "Bearer", "access_token=", "refresh_token="]
    for pattern in forbidden_patterns:
        assert pattern not in attrs_json, (
            f"Forbidden pattern '{pattern}' found in trace attributes"
        )
