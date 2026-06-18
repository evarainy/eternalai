"""Golden Task fixture loader and mock state injection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Generator, cast

import pytest

from app.execution_fabric.mock_adapters.error_injection import clear_injection
from app.execution_fabric.mock_adapters.hikvision_ivms.mock_hikvision_ivms_adapter import (
    MockHikvisionIVMSAdapter,
)
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.execution_fabric.mock_adapters.u8.mock_u8_adapter import MockU8Adapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(gt_id: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{gt_id}.json"
    with path.open(encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def apply_mock_state(adapter: Any, state: Any) -> None:
    """Inject state into adapter; skip if state is the sentinel string."""
    if isinstance(state, dict):
        adapter.set_state(state)
    elif state == "should_not_be_called":
        pass
    else:
        raise ValueError(f"Unexpected mock state value: {state!r}")


@pytest.fixture(scope="function")
def oa_adapter() -> Generator[MockOAAdapter, None, None]:
    adapter = MockOAAdapter()
    yield adapter
    adapter.reset_state()


@pytest.fixture(scope="function")
def u8_adapter() -> Generator[MockU8Adapter, None, None]:
    adapter = MockU8Adapter()
    yield adapter
    adapter.reset_state()


@pytest.fixture(scope="function")
def ivms_adapter() -> Generator[MockHikvisionIVMSAdapter, None, None]:
    adapter = MockHikvisionIVMSAdapter()
    yield adapter
    adapter.reset_state()


@pytest.fixture(autouse=True, scope="function")
def reset_error_injections() -> Generator[None, None, None]:
    yield
    clear_injection()
