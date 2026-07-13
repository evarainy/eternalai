"""Verify mock state injection and isolation."""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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
    assert result.data is not None
    workflows = result.data.get("workflows") or result.data.get("pending_workflows")
    assert isinstance(workflows, list)
    assert len(workflows) == 3


def test_gt001_oa_state_reset_clears_injected_data(
    oa_adapter: MockOAAdapter,
) -> None:
    fixture = load_fixture("GT-001")
    apply_mock_state(oa_adapter, fixture["given"]["mock_oa_state"])
    oa_adapter.reset_state()

    result = asyncio.run(oa_adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    data_str = str(result.data)
    assert "OA-WF-2026-0001" not in data_str


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
