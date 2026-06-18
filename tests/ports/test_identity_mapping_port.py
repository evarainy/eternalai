"""Contract tests for IdentityMappingPort identity binding models."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityBindStatus,
    IdentityCheckResult,
    IdentityMappingPort,
    TargetSystem,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_MAPPING_SOURCE = REPO_ROOT / "app" / "ports" / "identity_mapping.py"

EXPECTED_IDENTITY_CHECK_RESULT_FIELDS = {
    "bind_status",
    "binding_id",
    "target_system",
    "execution_identity",
    "binding_scope",
    "account_set_id",
    "device_domain_id",
    "reason_code",
}

EXPECTED_IDENTITY_BIND_STATUS_VALUES = (
    "active",
    "unbound",
    "expired",
    "revoked",
    "verification_failed",
    "needs_binding_scope",
)

EXPECTED_TARGET_SYSTEM_VALUES = ("oa", "u8", "hikvision_ivms")

EXPECTED_EXECUTION_IDENTITY_VALUES = (
    "user_delegated",
    "system_scope",
    "admin_approved_proxy",
)


def make_identity_check_result(
    *,
    bind_status: str = "active",
    target_system: str = "oa",
    execution_identity: str = "user_delegated",
) -> IdentityCheckResult:
    return IdentityCheckResult(
        bind_status=bind_status,
        target_system=target_system,
        execution_identity=execution_identity,
    )


def test_identity_check_result_field_set_matches_spec_8_6_6() -> None:
    assert set(IdentityCheckResult.model_fields.keys()) == EXPECTED_IDENTITY_CHECK_RESULT_FIELDS
    assert "status" not in IdentityCheckResult.model_fields


def test_identity_bind_status_literal_values_match_spec_8_6_6() -> None:
    assert get_args(IdentityBindStatus) == EXPECTED_IDENTITY_BIND_STATUS_VALUES


def test_identity_check_result_accepts_all_bind_status_values() -> None:
    for bind_status in EXPECTED_IDENTITY_BIND_STATUS_VALUES:
        result = make_identity_check_result(bind_status=bind_status)

        assert result.bind_status == bind_status


def test_identity_check_result_rejects_bind_status_outside_contract() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_identity_check_result(bind_status="pending_review")

    assert "bind_status" in str(exc_info.value)


def test_target_system_literal_values_match_spec() -> None:
    assert get_args(TargetSystem) == EXPECTED_TARGET_SYSTEM_VALUES


def test_identity_check_result_accepts_all_target_system_values() -> None:
    for target_system in EXPECTED_TARGET_SYSTEM_VALUES:
        result = make_identity_check_result(target_system=target_system)

        assert result.target_system == target_system


def test_identity_check_result_rejects_target_system_outside_contract() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_identity_check_result(target_system="invalid_system")

    assert "target_system" in str(exc_info.value)


def test_execution_identity_literal_values_match_spec() -> None:
    assert get_args(ExecutionIdentity) == EXPECTED_EXECUTION_IDENTITY_VALUES


def test_identity_check_result_accepts_all_execution_identity_values() -> None:
    for execution_identity in EXPECTED_EXECUTION_IDENTITY_VALUES:
        result = make_identity_check_result(execution_identity=execution_identity)

        assert result.execution_identity == execution_identity


def test_identity_check_result_rejects_execution_identity_outside_contract() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_identity_check_result(execution_identity="invalid_identity")

    assert "execution_identity" in str(exc_info.value)


def test_identity_check_result_defaults_optional_fields_to_none() -> None:
    result = make_identity_check_result()

    assert result.binding_id is None
    assert result.binding_scope is None
    assert result.account_set_id is None
    assert result.device_domain_id is None
    assert result.reason_code is None


def test_identity_check_result_accepts_arbitrary_reason_code() -> None:
    result = IdentityCheckResult(
        bind_status="verification_failed",
        target_system="oa",
        execution_identity="user_delegated",
        reason_code="custom_reason_42",
    )

    assert result.reason_code == "custom_reason_42"


def test_identity_check_result_accepts_arbitrary_scope_values() -> None:
    result = IdentityCheckResult(
        bind_status="active",
        target_system="u8",
        execution_identity="system_scope",
        binding_scope="scope_abc_99",
        account_set_id="acct_xyz",
        device_domain_id="dom_99",
    )

    assert result.binding_scope == "scope_abc_99"
    assert result.account_set_id == "acct_xyz"
    assert result.device_domain_id == "dom_99"


def test_identity_check_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        IdentityCheckResult(
            bind_status="active",
            target_system="oa",
            execution_identity="user_delegated",
            password="x",
        )

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_identity_check_result_requires_core_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        IdentityCheckResult()

    error_text = str(exc_info.value)
    assert "bind_status" in error_text
    assert "target_system" in error_text
    assert "execution_identity" in error_text


def test_identity_check_result_defines_no_plaintext_credential_slots() -> None:
    forbidden_field_names = {
        "password",
        "token",
        "cookie",
        "sessionid",
        "access_token",
        "refresh_token",
        "api_key",
        "private_key",
        "secret",
    }

    assert forbidden_field_names.isdisjoint(IdentityCheckResult.model_fields.keys())


def test_identity_mapping_port_protocol_defines_expected_methods() -> None:
    assert set(IdentityMappingPort.__protocol_attrs__) == {
        "resolve_execution_identity",
        "get_mapping",
        "list_mappings",
    }


def test_resolve_execution_identity_signature_matches_spec_8_6_8() -> None:
    hints = get_type_hints(IdentityMappingPort.resolve_execution_identity)
    signature = inspect.signature(IdentityMappingPort.resolve_execution_identity)

    assert list(signature.parameters) == [
        "self",
        "ai_user_id",
        "target_system",
        "execution_identity",
        "request_context",
    ]
    assert hints["ai_user_id"] is str
    assert hints["target_system"] is TargetSystem
    assert hints["execution_identity"] is ExecutionIdentity
    assert hints["request_context"] is RequestOrgContext
    assert hints["return"] is IdentityCheckResult
    assert inspect.iscoroutinefunction(IdentityMappingPort.resolve_execution_identity)


def test_get_mapping_signature_supports_scope_filters() -> None:
    hints = get_type_hints(IdentityMappingPort.get_mapping)
    signature = inspect.signature(IdentityMappingPort.get_mapping)

    assert list(signature.parameters) == [
        "self",
        "ai_user_id",
        "target_system",
        "binding_scope",
        "account_set_id",
        "device_domain_id",
    ]
    assert hints["ai_user_id"] is str
    assert hints["target_system"] is TargetSystem
    assert hints["binding_scope"] == str | None
    assert hints["account_set_id"] == str | None
    assert hints["device_domain_id"] == str | None
    assert hints["return"] == IdentityCheckResult | None


def test_list_mappings_signature_supports_scope_filters() -> None:
    hints = get_type_hints(IdentityMappingPort.list_mappings)
    signature = inspect.signature(IdentityMappingPort.list_mappings)

    assert list(signature.parameters) == [
        "self",
        "ai_user_id",
        "target_system",
        "binding_scope",
        "account_set_id",
        "device_domain_id",
    ]
    assert hints["ai_user_id"] is str
    assert hints["target_system"] == TargetSystem | None
    assert hints["binding_scope"] == str | None
    assert hints["account_set_id"] == str | None
    assert hints["device_domain_id"] == str | None
    assert hints["return"] == list[IdentityCheckResult]


def test_concrete_mock_identity_mapping_port_can_be_instantiated_and_called() -> None:
    expected = IdentityCheckResult(
        bind_status="active",
        target_system="oa",
        execution_identity="user_delegated",
    )

    class MockIdentityMappingPort:
        async def resolve_execution_identity(
            self,
            ai_user_id: str,
            target_system: TargetSystem,
            execution_identity: ExecutionIdentity,
            request_context: RequestOrgContext,
        ) -> IdentityCheckResult:
            assert ai_user_id == "ai-user-1"
            assert target_system == "oa"
            assert execution_identity == "user_delegated"
            assert request_context.request_id == "test-req-1"
            return expected

        async def get_mapping(
            self,
            ai_user_id: str,
            target_system: TargetSystem,
            binding_scope: str | None = None,
            account_set_id: str | None = None,
            device_domain_id: str | None = None,
        ) -> IdentityCheckResult | None:
            assert ai_user_id == "ai-user-1"
            assert target_system == "oa"
            assert binding_scope is None
            assert account_set_id is None
            assert device_domain_id is None
            return expected

        async def list_mappings(
            self,
            ai_user_id: str,
            target_system: TargetSystem | None = None,
            binding_scope: str | None = None,
            account_set_id: str | None = None,
            device_domain_id: str | None = None,
        ) -> list[IdentityCheckResult]:
            assert ai_user_id == "ai-user-1"
            assert target_system == "oa"
            assert binding_scope is None
            assert account_set_id is None
            assert device_domain_id is None
            return [expected]

    async def exercise_port(port: IdentityMappingPort) -> tuple[
        IdentityCheckResult,
        IdentityCheckResult | None,
        list[IdentityCheckResult],
    ]:
        request_context = RequestOrgContext(request_id="test-req-1")
        resolved = await port.resolve_execution_identity(
            ai_user_id="ai-user-1",
            target_system="oa",
            execution_identity="user_delegated",
            request_context=request_context,
        )
        mapping = await port.get_mapping(ai_user_id="ai-user-1", target_system="oa")
        mappings = await port.list_mappings(ai_user_id="ai-user-1", target_system="oa")
        return resolved, mapping, mappings

    resolved_result, mapping_result, mapping_results = asyncio.run(
        exercise_port(MockIdentityMappingPort())
    )

    assert isinstance(resolved_result, IdentityCheckResult)
    assert resolved_result == expected
    assert mapping_result == expected
    assert mapping_results == [expected]


def test_identity_mapping_source_imports_no_storage_or_provider_dependencies() -> None:
    source = IDENTITY_MAPPING_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = ("Vault", "OAuth", "sqlalchemy", "redis", "requests", "httpx", "open(")
    assert not any(term in source for term in forbidden_terms)
