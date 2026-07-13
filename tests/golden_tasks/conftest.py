"""Golden Task fixture loader and mock state injection."""

from __future__ import annotations

from typing import Generator

import pytest

from app.execution_fabric.mock_adapters.error_injection import clear_injection
from app.execution_fabric.mock_adapters.hikvision_ivms.mock_hikvision_ivms_adapter import (
    MockHikvisionIVMSAdapter,
)
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.execution_fabric.mock_adapters.u8.mock_u8_adapter import MockU8Adapter


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
