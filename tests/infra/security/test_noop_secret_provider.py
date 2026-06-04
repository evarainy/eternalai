from __future__ import annotations

import inspect
import json
import pathlib

import pytest
from pydantic import ValidationError

from app.ports.secret_provider import SecretInjectionResult, SecretResolutionResult

# =====================================================================
# Test group 1: Protocol duck-type
# =====================================================================


def test_noop_secret_provider_satisfies_secret_provider_port() -> None:
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        NoopSecretProvider,
    )

    provider = NoopSecretProvider()
    assert hasattr(provider, "resolve_secret_ref")
    assert hasattr(provider, "inject_execution_secret")
    assert inspect.iscoroutinefunction(provider.resolve_secret_ref)
    assert inspect.iscoroutinefunction(provider.inject_execution_secret)
    # signature check resolve_secret_ref
    sig_r = inspect.signature(provider.resolve_secret_ref)
    assert "credential_ref" in sig_r.parameters
    assert "task_id" in sig_r.parameters
    assert "capability_id" in sig_r.parameters
    # signature check inject_execution_secret
    sig_i = inspect.signature(provider.inject_execution_secret)
    assert "execution_context" in sig_i.parameters
    assert "credential_ref" in sig_i.parameters


# =====================================================================
# Test group 2: resolve_secret_ref return shape
# HARD MANDATE: must use SecretResolutionResult(...).model_dump()
# Returns {"credential_ref": ..., "redacted_placeholder": "<redacted>"}
# =====================================================================


@pytest.mark.anyio
async def test_resolve_secret_ref_returns_credential_ref_and_redacted_placeholder() -> None:
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        NoopSecretProvider,
    )

    provider = NoopSecretProvider()
    result = await provider.resolve_secret_ref(
        credential_ref="oa_service_account",
        task_id="task-001",
        capability_id="oa.query_pending_workflows",
    )
    assert isinstance(result, dict)
    # Exact key set: only credential_ref and redacted_placeholder
    assert set(result.keys()) == {"credential_ref", "redacted_placeholder"}
    assert result["credential_ref"] == "oa_service_account"
    assert result["redacted_placeholder"] == "<redacted>"


@pytest.mark.anyio
async def test_resolve_secret_ref_result_equals_model_dump() -> None:
    """Return must equal SecretResolutionResult(...).model_dump()."""
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        NoopSecretProvider,
    )

    provider = NoopSecretProvider()
    result = await provider.resolve_secret_ref("my_cred", "t1", "cap1")
    expected = SecretResolutionResult(credential_ref="my_cred").model_dump()
    assert result == expected


# =====================================================================
# Test group 3: inject_execution_secret return shape
# HARD MANDATE: SecretInjectionResult has credential_ref (required) + mock_secret_injected
# Returns {"credential_ref": ..., "mock_secret_injected": True}  -- BOTH fields
# =====================================================================


@pytest.mark.anyio
async def test_inject_execution_secret_returns_both_fields() -> None:
    """Must return BOTH credential_ref AND mock_secret_injected=True."""
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        NoopSecretProvider,
    )

    provider = NoopSecretProvider()
    result = await provider.inject_execution_secret(
        execution_context={"task_type": "query"},
        credential_ref="oa_service_account",
    )
    assert isinstance(result, dict)
    # BOTH fields required by SecretInjectionResult model
    assert "credential_ref" in result
    assert result["credential_ref"] == "oa_service_account"
    assert "mock_secret_injected" in result
    assert result["mock_secret_injected"] is True


@pytest.mark.anyio
async def test_inject_execution_secret_result_equals_model_dump() -> None:
    """Return must equal SecretInjectionResult(credential_ref=...).model_dump()."""
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        NoopSecretProvider,
    )

    provider = NoopSecretProvider()
    result = await provider.inject_execution_secret({}, "my_cred")
    expected = SecretInjectionResult(credential_ref="my_cred").model_dump()
    assert result == expected


@pytest.mark.anyio
async def test_inject_execution_secret_does_not_leak_execution_context() -> None:
    """execution_context body must NOT appear as substring in inject output.

    HARD MANDATE: explicit negative-content assertion using json.dumps.
    """
    from app.infra.security.noop_secret_provider.noop_secret_provider import (
        NoopSecretProvider,
    )

    provider = NoopSecretProvider()
    context_value = "some-context-value-that-must-not-leak"
    session_value = "sess-abc123"
    token_like_context = {
        "internal_" + "token": "Bearer " + context_value,
        "session_" + "id": session_value,
    }
    result = await provider.inject_execution_secret(
        execution_context=token_like_context,
        credential_ref="svc_account",
    )
    result_json = json.dumps(result)
    # Must NOT contain any of the context body values
    assert context_value not in result_json
    assert session_value not in result_json
    # Must contain the expected safe fields only
    assert "svc_account" in result_json
    assert "mock_secret_injected" in result_json


# =====================================================================
# Test group 4: Literal field validation -- positive and negative
# =====================================================================


def test_secret_resolution_result_redacted_placeholder_default() -> None:
    r = SecretResolutionResult(credential_ref="x")
    assert r.redacted_placeholder == "<redacted>"


def test_secret_injection_result_mock_secret_injected_default() -> None:
    r = SecretInjectionResult(credential_ref="x")
    assert r.mock_secret_injected is True


def test_secret_resolution_result_invalid_placeholder_raises() -> None:
    """RedactedSecretPlaceholder is Literal['<redacted>'] -- wrong value must raise."""
    with pytest.raises(ValidationError):
        SecretResolutionResult(credential_ref="x", redacted_placeholder="wrong_value")


def test_secret_injection_result_invalid_mock_secret_injected_raises() -> None:
    """MockSecretInjected is Literal[True] -- False must raise."""
    with pytest.raises(ValidationError):
        SecretInjectionResult(credential_ref="x", mock_secret_injected=False)


# =====================================================================
# Test group 5: extra="forbid" enforcement
# =====================================================================


def test_secret_resolution_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SecretResolutionResult(credential_ref="x", unexpected="bad")


def test_secret_injection_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SecretInjectionResult(credential_ref="x", unexpected="bad")


# =====================================================================
# Test group 6: No Vault/KMS/forbidden imports
# =====================================================================


def test_no_forbidden_imports_in_noop_provider() -> None:
    """Implementation must not import vault/kms/requests/httpx."""
    src = pathlib.Path(
        "app/infra/security/noop_secret_provider/noop_secret_provider.py"
    ).read_text()
    forbidden = ["vault", "kms", "oauth2", "requests", "httpx", "aiohttp"]
    for pattern in forbidden:
        assert pattern not in src.lower(), (
            f"Forbidden import pattern '{pattern}' found in implementation"
        )
