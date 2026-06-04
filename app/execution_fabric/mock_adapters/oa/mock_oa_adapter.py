"""Mock OA adapter for Phase 0 execution-fabric contract tests."""

from __future__ import annotations

from typing import Any

from app.ports.adapter import MOCK_ERROR_MODE_TO_ERROR_CODE, AdapterResult


class MockOAAdapter:
    """Deterministic OA adapter that returns AdapterResult without upstream I/O."""

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

        if "workflow_status" in capability_id or "get_workflow" in capability_id:
            current_step = arguments.get("mock_current_step", "pending")
            return AdapterResult(
                status="success",
                data={
                    "workflow_id": "wf-mock-001",
                    "current_step": current_step,
                    "approver": "mock-approver",
                },
            )

        status = arguments.get("mock_status", "pending")
        return AdapterResult(
            status="success",
            data={
                "workflow_id": "wf-mock-001",
                "title": "Mock Workflow",
                "status": status,
                "applicant": "mock-applicant",
                "created_at": "2026-01-01T00:00:00",
            },
        )
