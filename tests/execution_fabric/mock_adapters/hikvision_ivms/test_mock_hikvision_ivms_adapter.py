"""Tests for the Phase 0 mock Hikvision iVMS adapter."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.execution_fabric.mock_adapters.hikvision_ivms.mock_hikvision_ivms_adapter import (
    MockHikvisionIVMSAdapter,
)
from app.ports.adapter import (
    MOCK_ERROR_MODE_TO_ERROR_CODE,
    AdapterPort,
    AdapterResult,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
ADAPTER_SOURCE = (
    REPO_ROOT
    / "app"
    / "execution_fabric"
    / "mock_adapters"
    / "hikvision_ivms"
    / "mock_hikvision_ivms_adapter.py"
)


def test_mock_hikvision_ivms_adapter_satisfies_adapter_port_protocol() -> None:
    adapter = MockHikvisionIVMSAdapter()
    port_view: AdapterPort = adapter

    assert hasattr(adapter, "execute")
    assert inspect.iscoroutinefunction(port_view.execute)

    signature = inspect.signature(port_view.execute)
    assert "capability_id" in signature.parameters
    assert "arguments" in signature.parameters
    assert "execution_context" in signature.parameters


@pytest.mark.anyio
async def test_device_status_happy_path_returns_success_with_required_fields() -> None:
    adapter = MockHikvisionIVMSAdapter()

    result = await adapter.execute(
        capability_id="ivms.get_device_status",
        arguments={},
        execution_context={},
    )

    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert "device_domain_id" in result.data
    assert isinstance(result.data["device_domain_id"], str)
    assert "device_id" in result.data
    assert isinstance(result.data["device_id"], str)
    assert "online" in result.data
    assert isinstance(result.data["online"], bool)
    assert "last_seen_at" in result.data
    assert isinstance(result.data["last_seen_at"], str)
    assert "video_frame_included" in result.data


@pytest.mark.anyio
async def test_device_status_video_frame_included_is_always_false() -> None:
    adapter = MockHikvisionIVMSAdapter()

    result = await adapter.execute(
        capability_id="ivms.get_device_status",
        arguments={
            "mock_online": True,
            "video_frame_included": True,
            "video_frames": b"fakedata",
        },
        execution_context={},
    )

    assert result.data is not None
    assert result.data["video_frame_included"] is False


@pytest.mark.anyio
async def test_alarm_summary_excludes_video_frames_and_raw_heartbeat_bytes() -> None:
    adapter = MockHikvisionIVMSAdapter()

    result = await adapter.execute("ivms.get_alarm_summary", {}, {})

    assert result.status == "success"
    assert result.data is not None
    forbidden_keys = {
        "video_frame",
        "video_frames",
        "frame_data",
        "heartbeat_stream",
        "raw_heartbeat",
        "device_stream",
    }
    assert forbidden_keys.isdisjoint(result.data.keys())


@pytest.mark.parametrize("domain_id", ["domain-alpha", "domain-beta"])
@pytest.mark.anyio
async def test_device_domain_id_routing_reflects_argument_values(
    domain_id: str,
) -> None:
    adapter = MockHikvisionIVMSAdapter()

    result = await adapter.execute(
        capability_id="ivms.get_device_status",
        arguments={"device_domain_id": domain_id},
        execution_context={},
    )

    assert result.data is not None
    assert result.data["device_domain_id"] == domain_id


@pytest.mark.parametrize("online", [True, False])
@pytest.mark.anyio
async def test_device_status_online_field_accepts_true_and_false(
    online: bool,
) -> None:
    adapter = MockHikvisionIVMSAdapter()

    result = await adapter.execute(
        capability_id="ivms.get_device_status",
        arguments={"mock_online": online},
        execution_context={},
    )

    assert result.data is not None
    assert result.data["online"] is online


@pytest.mark.parametrize(
    "error_mode,expected_code",
    list(MOCK_ERROR_MODE_TO_ERROR_CODE.items()),
)
@pytest.mark.anyio
async def test_error_modes_return_exact_mock_error_code_mapping(
    error_mode: str,
    expected_code: str,
) -> None:
    adapter = MockHikvisionIVMSAdapter()

    result = await adapter.execute(
        capability_id="ivms.get_device_status",
        arguments={},
        execution_context={"mock_error_mode": error_mode},
    )

    assert result.status == "error"
    assert result.data is None
    assert result.error_code == expected_code


def test_adapter_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        AdapterResult(status="success", data=None, unexpected_field="bad")  # type: ignore[call-arg]


def test_mock_hikvision_ivms_adapter_has_no_http_client_imports() -> None:
    source = ADAPTER_SOURCE.read_text(encoding="utf-8")
    forbidden_imports = (
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import aiohttp",
        "from aiohttp",
        "import subprocess",
        "from subprocess",
        "import playwright",
        "from playwright",
    )

    assert not any(term in source for term in forbidden_imports)


def test_device_status_last_seen_at_accepts_arbitrary_string() -> None:
    adapter = MockHikvisionIVMSAdapter()
    result = asyncio.run(
        adapter.execute(
            capability_id="ivms.get_device_status",
            arguments={"mock_last_seen_at": "not-a-real-timestamp-xyzzy"},
            execution_context={},
        )
    )

    assert result.data is not None
    assert result.data["last_seen_at"] == "not-a-real-timestamp-xyzzy"
