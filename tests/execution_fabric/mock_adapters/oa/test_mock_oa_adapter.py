"""Tests for the Phase 0 mock OA adapter."""

import asyncio
import inspect

import pytest
from pydantic import ValidationError

from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.ports.adapter import (
    MOCK_ERROR_MODE_TO_ERROR_CODE,
    AdapterPort,
    AdapterResult,
)

# HTTP import scan verified in CI step: no requests/httpx/aiohttp/subprocess/playwright imports
# in app/execution_fabric/mock_adapters/oa/mock_oa_adapter.py


def test_mock_oa_adapter_satisfies_adapter_port_protocol() -> None:
    adapter = MockOAAdapter()
    port_view: AdapterPort = adapter
    assert hasattr(adapter, "execute")
    assert inspect.iscoroutinefunction(port_view.execute)
    sig = inspect.signature(port_view.execute)
    assert "capability_id" in sig.parameters
    assert "arguments" in sig.parameters
    assert "execution_context" in sig.parameters


@pytest.mark.anyio
async def test_happy_path_pending_workflow_returns_success() -> None:
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.query_pending_workflows",
        arguments={},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert "workflow_id" in result.data
    assert isinstance(result.data["workflow_id"], str)
    assert "title" in result.data
    assert isinstance(result.data["title"], str)
    assert "status" in result.data
    assert result.data["status"] in ("pending", "approved", "rejected")
    assert "applicant" in result.data
    assert isinstance(result.data["applicant"], str)
    assert "created_at" in result.data
    assert isinstance(result.data["created_at"], str)


@pytest.mark.parametrize("status_value", ["pending", "approved", "rejected"])
@pytest.mark.anyio
async def test_pending_workflow_status_all_literal_values_constructible(
    status_value: str,
) -> None:
    """Verify MockOAAdapter can return each allowed status value."""
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.query_pending_workflows",
        arguments={"mock_status": status_value},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert result.data["status"] == status_value

    r = AdapterResult(
        status="success",
        data={
            "workflow_id": "wf-001",
            "title": "t",
            "status": status_value,
            "applicant": "a",
            "created_at": "2026-01-01",
        },
    )
    assert r.data is not None
    assert r.data["status"] == status_value


@pytest.mark.anyio
async def test_happy_path_workflow_status_returns_success() -> None:
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.get_workflow_status",
        arguments={},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert "workflow_id" in result.data
    assert isinstance(result.data["workflow_id"], str)
    assert "current_step" in result.data
    assert result.data["current_step"] in ("draft", "pending", "approved", "rejected")
    assert "approver" in result.data
    assert isinstance(result.data["approver"], str)


@pytest.mark.parametrize("step_value", ["draft", "pending", "approved", "rejected"])
@pytest.mark.anyio
async def test_workflow_status_current_step_all_literal_values_constructible(
    step_value: str,
) -> None:
    """Verify MockOAAdapter returns each allowed current_step value through execute()."""
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.get_workflow_status",
        arguments={"mock_current_step": step_value},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert result.data["current_step"] == step_value

    # Also verify model construction directly (Literal membership lock)
    r = AdapterResult(
        status="success",
        data={"workflow_id": "wf-001", "current_step": step_value, "approver": "mgr"},
    )
    assert r.data is not None
    assert r.data["current_step"] == step_value


@pytest.mark.parametrize("error_mode,expected_code", list(MOCK_ERROR_MODE_TO_ERROR_CODE.items()))
@pytest.mark.anyio
async def test_error_modes_return_correct_error_code(
    error_mode: str,
    expected_code: str,
) -> None:
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.query_pending_workflows",
        arguments={},
        execution_context={"mock_error_mode": error_mode},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "error"
    assert result.error_code == expected_code


@pytest.mark.anyio
async def test_malformed_json_no_uncaught_exception() -> None:
    """Gateway must receive standard AdapterResult; no uncaught exception."""
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.query_pending_workflows",
        arguments={},
        execution_context={"mock_error_mode": "malformed_json"},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"


@pytest.mark.anyio
async def test_missing_required_field_data_is_none() -> None:
    """data must be None -- no silent auto-fill."""
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.query_pending_workflows",
        arguments={},
        execution_context={"mock_error_mode": "missing_required_field"},
    )
    assert result.status == "error"
    assert result.error_code == "adapter_missing_required_field"
    assert result.data is None


@pytest.mark.anyio
async def test_permission_denied_status_is_not_success() -> None:
    adapter = MockOAAdapter()
    result = await adapter.execute(
        capability_id="oa.query_pending_workflows",
        arguments={},
        execution_context={"mock_error_mode": "permission_denied"},
    )
    assert result.status != "success"
    assert result.status == "error"
    assert result.error_code == "upstream_permission_denied"


def test_adapter_result_extra_forbid() -> None:
    """AdapterResult.extra='forbid' is enforced on top-level fields."""
    with pytest.raises(ValidationError):
        AdapterResult(status="success", data=None, unexpected_field="bad")  # type: ignore[call-arg]


def test_adapter_result_never_bare_dict() -> None:
    """execute returns AdapterResult instance, never a bare dict."""
    adapter = MockOAAdapter()
    result = asyncio.run(adapter.execute("oa.query_pending_workflows", {}, {}))
    assert isinstance(result, AdapterResult)
    assert not isinstance(result, dict)
