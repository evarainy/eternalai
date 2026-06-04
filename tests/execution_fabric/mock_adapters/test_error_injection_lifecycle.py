"""Lifecycle tests for Phase 0 mock adapter error injections."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.execution_fabric.mock_adapters.error_injection import (
    InjectionAwareAdapter,
    clear_injection,
    get_injection,
    set_injection,
)
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.ports.adapter import MOCK_ERROR_MODE_TO_ERROR_CODE


@pytest.fixture(autouse=True)
def reset_injections() -> Iterator[None]:
    clear_injection()
    yield
    clear_injection()


@pytest.mark.anyio
async def test_next_1_call_injection_clears_after_one_adapter_execute() -> None:
    set_injection("oa.query", "timeout", "next_1_call")
    adapter = InjectionAwareAdapter(MockOAAdapter())

    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "error"
    assert result.error_code == "adapter_timeout"

    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "success"
    assert result.error_code is None


@pytest.mark.anyio
async def test_next_3_calls_injection_clears_after_three_adapter_executes() -> None:
    set_injection("oa.query", "http_500", "next_3_calls")
    adapter = InjectionAwareAdapter(MockOAAdapter())

    for _ in range(3):
        result = await adapter.execute("oa.query", {}, {})
        assert result.status == "error"
        assert result.error_code == "adapter_http_500"

    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "success"
    assert result.error_code is None


@pytest.mark.anyio
async def test_permanent_injection_persists_until_clear() -> None:
    set_injection("oa.query", "permission_denied", "permanent")
    adapter = InjectionAwareAdapter(MockOAAdapter())

    for _ in range(3):
        result = await adapter.execute("oa.query", {}, {})
        assert result.status == "error"
        assert result.error_code == "upstream_permission_denied"

    clear_injection("oa.query")
    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "success"


@pytest.mark.anyio
async def test_clear_injection_resets_state_for_teardown() -> None:
    capability_id = "oa.query"
    set_injection(capability_id, "timeout", "permanent")
    assert get_injection(capability_id) is not None

    clear_injection()
    assert get_injection(capability_id) is None

    adapter = InjectionAwareAdapter(MockOAAdapter())
    result = await adapter.execute(capability_id, {}, {})
    assert result.status == "success"


@pytest.mark.parametrize("error_mode,expected_code", list(MOCK_ERROR_MODE_TO_ERROR_CODE.items()))
@pytest.mark.anyio
async def test_all_error_modes_work_end_to_end_with_mock_oa_adapter(
    error_mode: str,
    expected_code: str,
) -> None:
    adapter = InjectionAwareAdapter(MockOAAdapter())
    set_injection("oa.query", error_mode, "next_1_call")

    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "error"
    assert result.error_code == expected_code

    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "success"
    assert result.error_code is None


@pytest.mark.anyio
async def test_reset_prevents_state_leakage_between_test_steps() -> None:
    set_injection("oa.query", "timeout", "permanent")
    clear_injection()

    adapter = InjectionAwareAdapter(MockOAAdapter())
    result = await adapter.execute("oa.query", {}, {})
    assert result.status == "success"
