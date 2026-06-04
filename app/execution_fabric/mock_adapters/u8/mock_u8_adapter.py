"""Mock U8 adapter for Phase 0 execution-fabric contract tests."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.ports.adapter import MOCK_ERROR_MODE_TO_ERROR_CODE, AdapterResult


class MockU8DocumentQueryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_set_id: str
    document_no: str
    document_status: Literal["draft", "posted", "voided"]
    amount: float
    currency: str


class MockU8BalanceQueryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_set_id: str
    vendor_id: str
    vendor_name: str
    balance: float
    currency: str


class MockU8Adapter:
    """Deterministic U8 adapter returning AdapterResult without upstream I/O."""

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        error_mode = execution_context.get("mock_error_mode")
        if error_mode is not None:
            return AdapterResult(
                status="error",
                data=None,
                error_code=MOCK_ERROR_MODE_TO_ERROR_CODE.get(error_mode),
            )

        account_set_id = str(arguments.get("account_set_id", "mock-account-set-001"))
        cid = capability_id.lower()

        if "balance" in cid or "vendor" in cid:
            try:
                balance_payload = MockU8BalanceQueryData(
                    account_set_id=account_set_id,
                    vendor_id="vendor-001",
                    vendor_name="Mock Vendor",
                    balance=3200.0,
                    currency=str(arguments.get("mock_currency", "CNY")),
                )
            except Exception:
                return AdapterResult(
                    status="error",
                    data=None,
                    error_code="adapter_payload_invalid",
                )
            return AdapterResult(status="success", data=balance_payload.model_dump())

        document_status = arguments.get("mock_document_status", "posted")
        try:
            document_payload = MockU8DocumentQueryData(
                account_set_id=account_set_id,
                document_no=str(arguments.get("document_no", "U8-DOC-0001")),
                document_status=document_status,
                amount=1280.5,
                currency=str(arguments.get("mock_currency", "CNY")),
            )
        except Exception:
            return AdapterResult(
                status="error",
                data=None,
                error_code="adapter_payload_invalid",
            )
        return AdapterResult(status="success", data=document_payload.model_dump())
