"""Mock OA adapter for Phase 0 execution-fabric contract tests."""

from __future__ import annotations

from typing import Any

from app.ports.adapter import MOCK_ERROR_MODE_TO_ERROR_CODE, AdapterResult

_PENDING_COLLECTION_FIELDS = frozenset(
    {"workflows", "returned_count", "authoritative_count", "is_complete"}
)
_PENDING_WORKFLOW_FIELDS = frozenset(
    {
        "todo_id",
        "title",
        "status",
        "received_at",
        "created_at",
        "workflow_type_id",
    }
)


class MockOAAdapter:
    """Deterministic OA adapter that returns AdapterResult without upstream I/O."""

    def __init__(self) -> None:
        self._mock_state: dict[str, Any] = {}

    def set_state(self, state: dict[str, Any]) -> None:
        self._mock_state = state

    def reset_state(self) -> None:
        self._mock_state = {}

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

        if self._mock_state:
            return self._build_from_state(capability_id, arguments)

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

    def _build_from_state(
        self,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> AdapterResult:
        cid = capability_id.lower()
        if "list_pending" in cid:
            key = "pending_workflows"
        elif "get_workflow" in cid or "workflow_status" in cid:
            key = "workflow_status"
        elif "submit" in cid:
            key = "submit_result"
        else:
            return AdapterResult(status="success", data=self._mock_state)

        if key not in self._mock_state:
            return AdapterResult(status="error", data=None, error_code="adapter_error")

        value = self._mock_state[key]
        if key == "pending_workflows":
            if isinstance(value, list):
                # Preserve the frozen GT-001 state convention and response shape.
                return AdapterResult(status="success", data={"workflows": value})
            if _is_valid_pending_collection(value):
                return AdapterResult(status="success", data=value)
            return AdapterResult(
                status="error",
                data=None,
                error_code="adapter_payload_invalid",
            )
        return AdapterResult(status="success", data=value)


def _is_valid_pending_collection(value: Any) -> bool:
    """Validate the structured Golden-only to-do state without infra imports."""

    if not isinstance(value, dict) or set(value) != _PENDING_COLLECTION_FIELDS:
        return False
    workflows = value["workflows"]
    returned_count = value["returned_count"]
    authoritative_count = value["authoritative_count"]
    if not isinstance(workflows, list):
        return False
    if not _is_non_negative_int(returned_count):
        return False
    if not _is_non_negative_int(authoritative_count):
        return False
    if value["is_complete"] is not True:
        return False
    if returned_count != len(workflows) or authoritative_count != len(workflows):
        return False
    if not all(_is_valid_pending_workflow(workflow) for workflow in workflows):
        return False
    todo_ids = [workflow["todo_id"] for workflow in workflows]
    return len(set(todo_ids)) == len(todo_ids)


def _is_valid_pending_workflow(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _PENDING_WORKFLOW_FIELDS:
        return False
    return all(
        isinstance(field_value, str)
        and bool(field_value.strip())
        and "<" not in field_value
        and ">" not in field_value
        for field_value in value.values()
    )


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
