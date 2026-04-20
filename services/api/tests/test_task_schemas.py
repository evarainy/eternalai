from pydantic import ValidationError

from services.api.app.schemas.task import GetTaskResponse
from services.api.tests.contract_helpers import get_contract_operation_block, get_resource_block


def test_get_task_response_accepts_error_payloads() -> None:
    response = GetTaskResponse(
        task_id="task_123",
        task_type="answer",
        status="failed",
        created_at="2026-04-20T12:00:00Z",
        updated_at="2026-04-20T12:02:00Z",
        error={"code": "answer_failed", "message": "No sources available"},
    )
    assert response.error is not None
    assert response.error.code == "answer_failed"


def test_get_task_response_rejects_invalid_status() -> None:
    try:
        GetTaskResponse(
            task_id="task_123",
            task_type="answer",
            status="pending",
            created_at="2026-04-20T12:00:00Z",
            updated_at="2026-04-20T12:02:00Z",
        )
    except ValidationError as exc:
        assert "queued" in str(exc)
    else:
        raise AssertionError("GetTaskResponse accepted an unsupported status")


def test_task_contract_and_resource_spec_reference_schema_models() -> None:
    task_block = get_contract_operation_block("getTask")
    resource_block = get_resource_block("assistant_task")

    assert "schema_ref: services.api.app.schemas.task.GetTaskResponse" in task_block
    assert "schema_ref: services.api.app.schemas.common.NotFoundErrorResponse" in task_block
    assert "services.api.app.schemas.task.GetTaskResponse" in resource_block
