"""Capability gateway pass-through skeleton for Phase 0."""

from __future__ import annotations

from typing import Any

from app.ports.adapter import AdapterPort, AdapterResult
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus, RequestOrgContext


def _map_adapter_status(adapter_result: AdapterResult) -> ExecutionStatus:
    if adapter_result.status == "success":
        return "completed"
    if adapter_result.error_code == "adapter_timeout":
        return "timeout"
    if adapter_result.error_code == "upstream_permission_denied":
        return "denied"
    return "failed"


class CapabilityGateway:
    """Minimal Gateway -> MockOAAdapter -> ExecutionResult pass-through."""

    def __init__(self, adapter: AdapterPort) -> None:
        self._adapter = adapter

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        execution_context: dict[str, Any] = {}
        if "mock_error_mode" in arguments:
            execution_context["mock_error_mode"] = arguments["mock_error_mode"]

        adapter_result = await self._adapter.execute(
            capability_id,
            arguments,
            execution_context,
        )

        return ExecutionResult(
            status=_map_adapter_status(adapter_result),
            data=adapter_result.data,
            error_code=adapter_result.error_code,
            trace_id=request_context.request_id,
        )
