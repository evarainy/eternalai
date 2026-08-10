"""Verify mock state injection and isolation."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import pytest

from app.execution_fabric.mock_adapters.hikvision_ivms.mock_hikvision_ivms_adapter import (
    MockHikvisionIVMSAdapter,
)
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.execution_fabric.mock_adapters.u8.mock_u8_adapter import MockU8Adapter

if TYPE_CHECKING:
    apply_mock_state: Callable[[object, object], None]
    load_fixture: Callable[[str], dict[str, Any]]
else:
    from scripts.golden_task_fixture_support import apply_mock_state, load_fixture


def test_gt001_oa_state_can_be_injected_and_returned(
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-001")
    mock_state = fixture["given"]["mock_oa_state"]
    apply_mock_state(oa_adapter, mock_state)

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    assert result.data == {"workflows": mock_state["pending_workflows"]}
    assert set(result.data) == {"workflows"}
    assert len(result.data["workflows"]) == 3


def test_gt027_structured_oa_state_returns_the_complete_collection(
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-027")
    mock_state = fixture["given"]["mock_oa_state"]
    apply_mock_state(oa_adapter, mock_state)

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    assert result.error_code is None
    assert result.data == mock_state["pending_workflows"]


def test_gt028_count_mismatch_returns_payload_invalid_without_success_data(
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-028")
    apply_mock_state(oa_adapter, fixture["given"]["mock_oa_state"])

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


@pytest.mark.parametrize("count_field", ["returned_count", "authoritative_count"])
def test_structured_pending_counts_must_match_the_workflow_list(
    count_field: str,
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-027")
    pending = deepcopy(fixture["given"]["mock_oa_state"]["pending_workflows"])
    pending[count_field] += 1
    apply_mock_state(oa_adapter, {"pending_workflows": pending})

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


def test_structured_pending_rejects_duplicate_todo_ids(
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-027")
    pending = deepcopy(fixture["given"]["mock_oa_state"]["pending_workflows"])
    pending["workflows"][1]["todo_id"] = pending["workflows"][0]["todo_id"]
    apply_mock_state(oa_adapter, {"pending_workflows": pending})

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


@pytest.mark.parametrize(
    "field",
    [
        "todo_id",
        "title",
        "status",
        "received_at",
        "created_at",
        "workflow_type_id",
    ],
)
def test_structured_pending_rejects_html_in_every_business_field(
    field: str,
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-027")
    pending = deepcopy(fixture["given"]["mock_oa_state"]["pending_workflows"])
    pending["workflows"][0][field] = "<span>synthetic</span>"
    apply_mock_state(oa_adapter, {"pending_workflows": pending})

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


@pytest.mark.parametrize(
    "shape_case",
    [
        "missing_workflow_field",
        "extra_workflow_field",
        "extra_collection_field",
        "incomplete_flag",
        "non_string_field",
        "workflows_not_list",
    ],
)
def test_structured_pending_rejects_invalid_model_shapes(
    shape_case: str,
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-027")
    pending = deepcopy(fixture["given"]["mock_oa_state"]["pending_workflows"])
    if shape_case == "missing_workflow_field":
        pending["workflows"][0].pop("workflow_type_id")
    elif shape_case == "extra_workflow_field":
        pending["workflows"][0]["message_id"] = "SYN-MSG-INVALID"
    elif shape_case == "extra_collection_field":
        pending["unexpected"] = "synthetic"
    elif shape_case == "incomplete_flag":
        pending["is_complete"] = False
    elif shape_case == "non_string_field":
        pending["workflows"][0]["status"] = 1
    else:
        pending["workflows"] = {}
    apply_mock_state(oa_adapter, {"pending_workflows": pending})

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


def test_gt001_oa_state_reset_clears_injected_data(
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-001")
    apply_mock_state(oa_adapter, fixture["given"]["mock_oa_state"])
    oa_adapter.reset_state()

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    data_str = str(result.data)
    assert "OA-MSG-2026-0001" not in data_str


def test_two_successive_gt_loads_do_not_leak_state(
    oa_adapter: MockOAAdapter,
) -> None:
    gt001 = load_fixture("GT-001")
    gt002 = load_fixture("GT-002")

    apply_mock_state(oa_adapter, gt001["given"]["mock_oa_state"])
    oa_adapter.reset_state()
    apply_mock_state(oa_adapter, gt002["given"]["mock_oa_state"])

    result = asyncio.run(
        oa_adapter.execute(
            "oa.get_workflow_status",
            {"workflow_id": "OA-WF-2026-0001"},
            {},
        )
    )

    assert result.status == "success"
    assert result.data is not None
    data_str = str(result.data)
    assert "approved" in data_str
    assert "办公用品采购审批" not in data_str


def test_gt003_u8_state_can_be_injected(
    u8_adapter: MockU8Adapter,
) -> None:
    fixture = load_fixture("GT-003")
    apply_mock_state(u8_adapter, fixture["given"]["mock_u8_state"])

    result = asyncio.run(
        u8_adapter.execute(
            "u8.get_document_status",
            {
                "account_set_id": "acctset_hunan_01",
                "document_no": "U8-AP-2026-0033",
            },
            {},
        )
    )

    assert result.status == "success"
    data_str = str(result.data)
    assert "posted" in data_str


def test_gt005_ivms_state_can_be_injected(
    ivms_adapter: MockHikvisionIVMSAdapter,
) -> None:
    fixture = load_fixture("GT-005")
    apply_mock_state(ivms_adapter, fixture["given"]["mock_ivms_state"])

    result = asyncio.run(
        ivms_adapter.execute(
            "ivms.get_device_online_status",
            {
                "device_domain_id": "prison_area_a",
                "device_id": "CAM-A-001",
            },
            {},
        )
    )

    assert result.status == "success"
    data_str = str(result.data)
    assert "CAM-A-001" in data_str


def test_function_scope_fixture_gives_fresh_adapter(
    oa_adapter: MockOAAdapter,
) -> None:
    assert oa_adapter._mock_state == {}


def test_sentinel_oa_state_is_not_injected(oa_adapter: MockOAAdapter) -> None:
    fixture = load_fixture("GT-008")
    state = fixture["given"]["mock_oa_state"]
    assert state == "should_not_be_called"
    apply_mock_state(oa_adapter, state)
    assert oa_adapter._mock_state == {}


def test_sentinel_u8_state_is_not_injected(u8_adapter: MockU8Adapter) -> None:
    fixture = load_fixture("GT-009")
    state = fixture["given"]["mock_u8_state"]
    assert state == "should_not_be_called"
    apply_mock_state(u8_adapter, state)
    assert u8_adapter._mock_state == {}
