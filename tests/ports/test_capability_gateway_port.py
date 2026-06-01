"""Contract tests for CapabilityGatewayPort gateway-facing models."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.capability_gateway import (
    CapabilityGatewayPort,
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestChannel,
    RequestOrgContext,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_GATEWAY_SOURCE = REPO_ROOT / "app" / "ports" / "capability_gateway.py"

EXPECTED_REQUEST_ORG_CONTEXT_FIELDS = {
    "request_id",
    "tenant_id",
    "org_id",
    "department_id",
    "roles",
    "channel",
    "locale",
    "account_set_id",
    "device_domain_id",
    "resource_scope",
}

EXPECTED_REQUEST_CHANNEL_VALUES = ("web", "cli", "api", "mock")

EXPECTED_ERROR_CODE_VALUES = (
    "identity_unbound",
    "identity_expired",
    "identity_revoked",
    "needs_binding_scope",
    "policy_denied",
    "confirm_required",
    "adapter_timeout",
    "capability_not_found",
    "adapter_error",
    "adapter_payload_invalid",
    "adapter_missing_required_field",
    "adapter_empty_response",
    "adapter_http_500",
    "upstream_permission_denied",
    "internal_error",
)

EXPECTED_EXECUTION_RESULT_FIELDS = {
    "status",
    "data",
    "error_code",
    "trace_id",
}

EXPECTED_EXECUTION_STATUS_VALUES = (
    "completed",
    "failed",
    "denied",
    "binding_required",
    "timeout",
    "no_capability_found",
    "waiting_user",
)


def test_request_org_context_field_set_matches_spec_8_6_1() -> None:
    assert set(RequestOrgContext.model_fields.keys()) == EXPECTED_REQUEST_ORG_CONTEXT_FIELDS


def test_request_org_context_defaults_match_spec_8_6_1() -> None:
    context = RequestOrgContext(request_id="request-001")

    assert context.request_id == "request-001"
    assert context.tenant_id == "default"
    assert context.org_id is None
    assert context.department_id is None
    assert context.roles == []
    assert context.channel == "web"
    assert context.locale == "zh-CN"
    assert context.account_set_id is None
    assert context.device_domain_id is None
    assert context.resource_scope is None


def test_request_org_context_roles_default_is_isolated_between_instances() -> None:
    first = RequestOrgContext(request_id="request-001")
    second = RequestOrgContext(request_id="request-002")

    first.roles.append("operator")

    assert first.roles == ["operator"]
    assert second.roles == []


def test_request_org_context_rejects_channel_outside_spec_8_6_1() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RequestOrgContext(request_id="request-001", channel="mobile")

    assert "Input should be" in str(exc_info.value)


def test_request_channel_literal_values_match_spec_8_6_1() -> None:
    assert get_args(RequestChannel) == EXPECTED_REQUEST_CHANNEL_VALUES


def test_error_code_literal_values_match_spec_8_6_3() -> None:
    assert get_args(ErrorCode) == EXPECTED_ERROR_CODE_VALUES


def test_execution_result_field_set_matches_spec_8_6_3() -> None:
    assert set(ExecutionResult.model_fields.keys()) == EXPECTED_EXECUTION_RESULT_FIELDS


def test_execution_status_literal_values_match_spec_8_6_3() -> None:
    assert get_args(ExecutionStatus) == EXPECTED_EXECUTION_STATUS_VALUES


def test_execution_result_accepts_valid_error_codes() -> None:
    for error_code in EXPECTED_ERROR_CODE_VALUES:
        result = ExecutionResult(status="failed", error_code=error_code, trace_id="trace-001")

        assert result.error_code == error_code


def test_execution_result_accepts_valid_data_payload() -> None:
    result = ExecutionResult(
        status="completed",
        data={"employee_id": "employee-001"},
        trace_id="trace-001",
    )

    assert result.data == {"employee_id": "employee-001"}
    assert result.error_code is None


def test_execution_result_rejects_status_outside_spec_8_6_3() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ExecutionResult(status="clarification_needed", trace_id="trace-001")

    assert "Input should be" in str(exc_info.value)


def test_execution_result_requires_trace_id() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ExecutionResult(status="completed")

    assert "trace_id" in str(exc_info.value)


class TestCapabilityGatewayPortProtocol:
    def test_protocol_is_not_runtime_checkable(self) -> None:
        assert hasattr(CapabilityGatewayPort, "__protocol_attrs__")
        assert not getattr(CapabilityGatewayPort, "_is_runtime_protocol", False)

    def test_protocol_defines_only_execute_capability(self) -> None:
        assert set(CapabilityGatewayPort.__protocol_attrs__) == {"execute_capability"}

    def test_execute_capability_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(CapabilityGatewayPort.execute_capability)
        signature = inspect.signature(CapabilityGatewayPort.execute_capability)

        assert CapabilityGatewayPort.execute_capability.__name__ == "execute_capability"
        assert list(signature.parameters) == [
            "self",
            "task_id",
            "session_id",
            "ai_user_id",
            "capability_id",
            "arguments",
            "request_context",
        ]
        assert hints["task_id"] is str
        assert hints["session_id"] is str
        assert hints["ai_user_id"] is str
        assert hints["capability_id"] is str
        assert hints["arguments"] == dict[str, Any]
        assert hints["request_context"] is RequestOrgContext
        assert hints["return"] is ExecutionResult
        assert inspect.iscoroutinefunction(CapabilityGatewayPort.execute_capability)


def test_capability_gateway_source_does_not_contain_concrete_execution_dependencies() -> None:
    source = CAPABILITY_GATEWAY_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = (
        "requests",
        "httpx",
        "Adapter",
        "PolicyGuard",
        "IdentityMapping",
        "TracePort(",
        "CapabilityRegistryPort",
        "CapabilitySpec",
        "sqlalchemy",
        "redis",
        "arq",
        "Repository",
        "app.runtime",
        "app.gateway",
        "app.execution_fabric",
    )
    assert not any(term in source for term in forbidden_terms)
