"""Contract tests for SecretProviderPort safe credential boundary."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import BaseModel, ValidationError

from app.ports.secret_provider import (
    MockSecretInjected,
    RedactedSecretPlaceholder,
    SecretInjectionResult,
    SecretProviderPort,
    SecretResolutionResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_PROVIDER_SOURCE = REPO_ROOT / "app" / "ports" / "secret_provider.py"

PLAINTEXT_CREDENTIAL_FIELD_NAMES = {
    "secret",
    "password",
    "token",
    "cookie",
    "sessionid",
    "access_token",
    "refresh_token",
}


def flattened_values(payload: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for value in payload.values():
        if isinstance(value, dict):
            values.extend(flattened_values(value))
        elif isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def test_secret_provider_port_is_protocol_only() -> None:
    assert hasattr(SecretProviderPort, "__protocol_attrs__")
    assert set(SecretProviderPort.__protocol_attrs__) == {
        "resolve_secret_ref",
        "inject_execution_secret",
    }
    assert not getattr(SecretProviderPort, "_is_runtime_protocol", False)


def test_resolve_secret_ref_signature_matches_spec_8_6_8() -> None:
    hints = get_type_hints(SecretProviderPort.resolve_secret_ref)
    signature = inspect.signature(SecretProviderPort.resolve_secret_ref)

    assert SecretProviderPort.resolve_secret_ref.__name__ == "resolve_secret_ref"
    assert list(signature.parameters) == [
        "self",
        "credential_ref",
        "task_id",
        "capability_id",
    ]
    assert hints["credential_ref"] is str
    assert hints["task_id"] is str
    assert hints["capability_id"] is str
    assert hints["return"] == dict[str, Any]
    assert inspect.iscoroutinefunction(SecretProviderPort.resolve_secret_ref)


def test_inject_execution_secret_signature_matches_spec_8_6_8() -> None:
    hints = get_type_hints(SecretProviderPort.inject_execution_secret)
    signature = inspect.signature(SecretProviderPort.inject_execution_secret)

    assert SecretProviderPort.inject_execution_secret.__name__ == "inject_execution_secret"
    assert list(signature.parameters) == [
        "self",
        "execution_context",
        "credential_ref",
    ]
    assert hints["execution_context"] == dict[str, Any]
    assert hints["credential_ref"] is str
    assert hints["return"] == dict[str, Any]
    assert inspect.iscoroutinefunction(SecretProviderPort.inject_execution_secret)


def test_protocol_methods_return_dict_str_any_not_helper_models() -> None:
    resolve_hints = get_type_hints(SecretProviderPort.resolve_secret_ref)
    inject_hints = get_type_hints(SecretProviderPort.inject_execution_secret)

    assert resolve_hints["return"] == dict[str, Any]
    assert resolve_hints["return"] is not SecretResolutionResult
    assert inject_hints["return"] == dict[str, Any]
    assert inject_hints["return"] is not SecretInjectionResult


def test_helper_models_are_phase0_safe_contracts_only() -> None:
    assert issubclass(SecretResolutionResult, BaseModel)
    assert issubclass(SecretInjectionResult, BaseModel)


def test_resolution_helper_field_names_do_not_define_plaintext_secret_slots() -> None:
    assert set(SecretResolutionResult.model_fields) == {
        "credential_ref",
        "redacted_placeholder",
    }
    assert PLAINTEXT_CREDENTIAL_FIELD_NAMES.isdisjoint(SecretResolutionResult.model_fields)


def test_injection_helper_field_names_do_not_define_plaintext_secret_slots() -> None:
    assert set(SecretInjectionResult.model_fields) == {
        "credential_ref",
        "mock_secret_injected",
    }
    assert PLAINTEXT_CREDENTIAL_FIELD_NAMES.isdisjoint(SecretInjectionResult.model_fields)


def test_resolution_helper_rejects_extra_plaintext_credential_fields() -> None:
    for field_name in PLAINTEXT_CREDENTIAL_FIELD_NAMES:
        payload = {"credential_ref": "credential-ref-001", field_name: "<redacted>"}

        with pytest.raises(ValidationError) as exc_info:
            SecretResolutionResult.model_validate(payload)

        assert field_name in str(exc_info.value)


def test_injection_helper_rejects_extra_plaintext_credential_fields() -> None:
    for field_name in PLAINTEXT_CREDENTIAL_FIELD_NAMES:
        payload = {
            "credential_ref": "credential-ref-001",
            "mock_secret_injected": True,
            field_name: "<redacted>",
        }

        with pytest.raises(ValidationError) as exc_info:
            SecretInjectionResult.model_validate(payload)

        assert field_name in str(exc_info.value)


def test_resolution_model_dump_contains_no_plaintext_credential_values() -> None:
    payload = SecretResolutionResult(credential_ref="credential-ref-001").model_dump()

    assert payload == {
        "credential_ref": "credential-ref-001",
        "redacted_placeholder": "<redacted>",
    }
    assert not any(
        isinstance(value, str) and value.lower() in PLAINTEXT_CREDENTIAL_FIELD_NAMES
        for value in flattened_values(payload)
    )


def test_injection_model_dump_contains_no_plaintext_credential_values() -> None:
    payload = SecretInjectionResult(credential_ref="credential-ref-001").model_dump()

    assert payload == {
        "credential_ref": "credential-ref-001",
        "mock_secret_injected": True,
    }
    assert not any(
        isinstance(value, str) and value.lower() in PLAINTEXT_CREDENTIAL_FIELD_NAMES
        for value in flattened_values(payload)
    )


def test_redacted_placeholder_alias_is_limited_to_redacted_marker() -> None:
    assert get_args(RedactedSecretPlaceholder) == ("<redacted>",)


def test_mock_secret_injected_alias_is_limited_to_safe_true_signal() -> None:
    assert get_args(MockSecretInjected) == (True,)


def test_secret_provider_module_imports_no_concrete_secret_dependencies() -> None:
    source = SECRET_PROVIDER_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = (
        "Vault",
        "OAuth",
        "requests",
        "httpx",
        "boto",
        "azure",
        "gcloud",
        "keyring",
        "open(",
    )
    assert not any(term in source for term in forbidden_terms)


def test_secret_provider_module_defines_no_gateway_adapter_or_concrete_provider_handler() -> None:
    source = SECRET_PROVIDER_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = (
        "app.runtime",
        "app.execution_fabric.real_adapters",
        "app.control_plane.identity",
    )
    assert not any(term in source for term in forbidden_terms)
