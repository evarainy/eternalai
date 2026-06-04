"""Integration tests for the Phase 0 capability gateway skeleton."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext


def _execute_gateway(
    arguments: dict[str, Any],
    request_id: str = "trace-001",
) -> ExecutionResult:
    gateway = CapabilityGateway(MockOAAdapter())
    request_context = RequestOrgContext(request_id=request_id)

    return asyncio.run(
        gateway.execute_capability(
            "task-001",
            "session-001",
            "ai-user-001",
            "oa.workflow_status.get",
            arguments,
            request_context,
        )
    )


def test_happy_path_returns_execution_result_with_adapter_data_and_trace_id() -> None:
    result = _execute_gateway({"mock_current_step": "manager_review"})

    assert isinstance(result, ExecutionResult)
    assert result.status == "completed"
    assert result.data == {
        "workflow_id": "wf-mock-001",
        "current_step": "manager_review",
        "approver": "mock-approver",
    }
    assert result.error_code is None
    assert result.trace_id == "trace-001"


def test_trace_id_accepts_arbitrary_request_id_without_generation_or_format_lock() -> None:
    result = _execute_gateway({}, request_id="arbitrary-trace-xyz-123")

    assert isinstance(result, ExecutionResult)
    assert result.trace_id == "arbitrary-trace-xyz-123"


@pytest.mark.parametrize(
    ("mock_error_mode", "expected_status", "expected_error_code"),
    (
        ("timeout", "timeout", "adapter_timeout"),
        ("permission_denied", "denied", "upstream_permission_denied"),
        ("malformed_json", "failed", "adapter_payload_invalid"),
        ("empty_response", "failed", "adapter_empty_response"),
        ("http_500", "failed", "adapter_http_500"),
        ("missing_required_field", "failed", "adapter_missing_required_field"),
    ),
)
def test_error_modes_return_execution_result_with_mapped_status_and_error_code(
    mock_error_mode: str,
    expected_status: str,
    expected_error_code: str,
) -> None:
    result = _execute_gateway(
        {"mock_error_mode": mock_error_mode},
        request_id="trace-error-001",
    )

    assert isinstance(result, ExecutionResult)
    assert result.status == expected_status
    assert result.error_code == expected_error_code
    assert result.trace_id == "trace-error-001"
    assert result.data is None
