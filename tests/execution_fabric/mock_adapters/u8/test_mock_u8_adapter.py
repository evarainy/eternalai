"""Tests for the Phase 0 mock U8 adapter."""

import asyncio
import inspect

import pytest
from pydantic import ValidationError

from app.execution_fabric.mock_adapters.u8.mock_u8_adapter import (
    MockU8Adapter,
    MockU8BalanceQueryData,
    MockU8DocumentQueryData,
)
from app.ports.adapter import (
    MOCK_ERROR_MODE_TO_ERROR_CODE,
    AdapterPort,
    AdapterResult,
)


def test_mock_u8_adapter_satisfies_adapter_port_protocol() -> None:
    adapter = MockU8Adapter()
    port_view: AdapterPort = adapter
    assert hasattr(adapter, "execute")
    assert inspect.iscoroutinefunction(port_view.execute)
    sig = inspect.signature(port_view.execute)
    assert "capability_id" in sig.parameters
    assert "arguments" in sig.parameters
    assert "execution_context" in sig.parameters


@pytest.mark.anyio
async def test_document_query_returns_success_with_required_fields() -> None:
    adapter = MockU8Adapter()
    result = await adapter.execute(
        capability_id="u8.query_document",
        arguments={},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert set(result.data) == {
        "account_set_id",
        "document_no",
        "document_status",
        "amount",
        "currency",
    }
    assert isinstance(result.data["account_set_id"], str)
    assert isinstance(result.data["document_no"], str)
    assert result.data["document_status"] in ("draft", "posted", "voided")
    assert isinstance(result.data["amount"], float)
    assert isinstance(result.data["currency"], str)


@pytest.mark.parametrize("document_status", ["draft", "posted", "voided"])
@pytest.mark.anyio
async def test_document_status_all_literal_values_constructible(
    document_status: str,
) -> None:
    payload = MockU8DocumentQueryData(
        account_set_id="acct-001",
        document_no="U8-DOC-0001",
        document_status=document_status,
        amount=1280.5,
        currency="CNY",
    )
    assert payload.document_status == document_status

    adapter = MockU8Adapter()
    result = await adapter.execute(
        capability_id="u8.query_document",
        arguments={"mock_document_status": document_status},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert result.data["document_status"] == document_status


def test_document_status_invalid_value_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        MockU8DocumentQueryData(
            account_set_id="acct-001",
            document_no="U8-DOC-0001",
            document_status="invalid",
            amount=1280.5,
            currency="CNY",
        )


@pytest.mark.anyio
async def test_document_status_invalid_via_execute_returns_error_result() -> None:
    adapter = MockU8Adapter()
    result = await adapter.execute(
        capability_id="u8.query_document",
        arguments={"mock_document_status": "invalid"},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "error"
    assert result.data is None
    assert result.error_code == "adapter_payload_invalid"


@pytest.mark.anyio
async def test_balance_query_returns_success_with_required_fields() -> None:
    adapter = MockU8Adapter()
    result = await adapter.execute(
        capability_id="u8.query_vendor_balance",
        arguments={},
        execution_context={},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "success"
    assert result.data is not None
    assert set(result.data) == {
        "account_set_id",
        "vendor_id",
        "vendor_name",
        "balance",
        "currency",
    }
    assert isinstance(result.data["account_set_id"], str)
    assert isinstance(result.data["vendor_id"], str)
    assert isinstance(result.data["vendor_name"], str)
    assert isinstance(result.data["balance"], float)
    assert isinstance(result.data["currency"], str)


@pytest.mark.anyio
async def test_account_set_id_routing_reflects_arguments() -> None:
    adapter = MockU8Adapter()
    document_result = await adapter.execute(
        capability_id="u8.query_document",
        arguments={"account_set_id": "acct-alpha"},
        execution_context={},
    )
    balance_result = await adapter.execute(
        capability_id="u8.query_vendor_balance",
        arguments={"account_set_id": "acct-beta"},
        execution_context={},
    )

    assert document_result.data is not None
    assert document_result.data["account_set_id"] == "acct-alpha"
    assert balance_result.data is not None
    assert balance_result.data["account_set_id"] == "acct-beta"


@pytest.mark.parametrize("error_mode,expected_code", list(MOCK_ERROR_MODE_TO_ERROR_CODE.items()))
@pytest.mark.anyio
async def test_error_modes_return_exact_error_codes(
    error_mode: str,
    expected_code: str,
) -> None:
    adapter = MockU8Adapter()
    result = await adapter.execute(
        capability_id="u8.query_document",
        arguments={},
        execution_context={"mock_error_mode": error_mode},
    )
    assert isinstance(result, AdapterResult)
    assert result.status == "error"
    assert result.data is None
    assert result.error_code == expected_code


def test_adapter_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        AdapterResult(status="success", data=None, unexpected_field="bad")


def test_mock_u8_document_query_data_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        MockU8DocumentQueryData(
            account_set_id="acct-001",
            document_no="U8-DOC-0001",
            document_status="posted",
            amount=1280.5,
            currency="CNY",
            unexpected_field="bad",
        )


def test_mock_u8_balance_query_data_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        MockU8BalanceQueryData(
            account_set_id="acct-001",
            vendor_id="vendor-001",
            vendor_name="Mock Vendor",
            balance=3200.0,
            currency="CNY",
            unexpected_field="bad",
        )


@pytest.mark.anyio
async def test_currency_accepts_arbitrary_value() -> None:
    payload = MockU8DocumentQueryData(
        account_set_id="a",
        document_no="d",
        document_status="posted",
        amount=1.0,
        currency="USD",
    )
    assert payload.currency == "USD"

    adapter = MockU8Adapter()
    result = await adapter.execute(
        capability_id="u8.query_document",
        arguments={"mock_currency": "EUR"},
        execution_context={},
    )
    assert result.data is not None
    assert result.data["currency"] == "EUR"


def test_mock_u8_adapter_does_not_import_http_clients() -> None:
    import app.execution_fabric.mock_adapters.u8.mock_u8_adapter as module

    source = inspect.getsource(module)
    forbidden_imports = ("requests", "httpx", "aiohttp", "subprocess", "playwright")
    for forbidden_import in forbidden_imports:
        assert forbidden_import not in source


def test_execute_never_returns_bare_dict() -> None:
    adapter = MockU8Adapter()
    result = asyncio.run(adapter.execute("u8.query_document", {}, {}))
    assert isinstance(result, AdapterResult)
    assert not isinstance(result, dict)
