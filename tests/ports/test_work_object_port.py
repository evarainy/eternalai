from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from app.ports.task_store import TaskRecord
from app.ports.work_object import (
    InternalWorkObjectRecord,
    OAPendingWorkSnapshotCollection,
    OAWorkObjectRecord,
    WorkObjectRecord,
)


def _record(**updates: object) -> WorkObjectRecord:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "work_object_id": "work-1",
        "state_authority": "external_snapshot",
        "source_system": "oa",
        "source_kind": "pending_workflow",
        "source_ref": "oa-todo-1",
        "assignee_ai_user_id": "user-a",
        "assignee_display_name": "User A",
        "due_at": None,
        "source_title": "Pending approval",
        "source_status": "pending",
        "source_received_at": "2026-08-18",
        "source_created_at": "2026-08-17",
        "source_workflow_type_id": "workflow-1",
        "source_fetched_at": now,
        "handling_mark": None,
        "handling_marked_by_ai_user_id": None,
        "handling_marked_at": None,
        "task_record_id": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(updates)
    return TypeAdapter(WorkObjectRecord).validate_python(values, strict=True)


def test_task_record_contract_includes_trusted_tenant_ownership() -> None:
    assert set(TaskRecord.model_fields) == {
        "task_id",
        "session_id",
        "ai_user_id",
        "tenant_id",
        "status",
        "trace_id",
        "capability_id",
        "error_code",
    }
    for record_model in (OAWorkObjectRecord, InternalWorkObjectRecord):
        assert "task_record_id" in record_model.model_fields
        assert record_model.model_fields["task_record_id"].annotation == str | None


def test_work_object_record_uses_state_authority_as_its_discriminator() -> None:
    internal = _record(
        state_authority="internal",
        source_system="eternalai",
        source_kind="internal_task",
        source_ref=None,
        source_title=None,
        source_status=None,
        source_received_at=None,
        source_created_at=None,
        source_workflow_type_id=None,
        source_fetched_at=None,
    )

    assert internal.state_authority == "internal"
    assert internal.source_ref is None
    with pytest.raises(ValidationError):
        _record(
            state_authority="internal",
            source_system="eternalai",
            source_kind="internal_task",
            source_ref=None,
            source_title="must-be-null",
            source_status=None,
            source_received_at=None,
            source_created_at=None,
            source_workflow_type_id=None,
            source_fetched_at=None,
        )


def test_oa_snapshot_collection_requires_complete_matching_unique_results() -> None:
    payload = {
        "workflows": [
            {
                "source_ref": "oa-todo-1",
                "title": "Pending approval",
                "status": "pending",
                "received_at": "2026-08-18",
                "created_at": "2026-08-17",
                "workflow_type_id": "workflow-1",
            }
        ],
        "returned_count": 1,
        "authoritative_count": 1,
        "is_complete": True,
    }
    collection = OAPendingWorkSnapshotCollection.model_validate(payload, strict=True)

    assert collection.workflows[0].source_ref == "oa-todo-1"

    with pytest.raises(ValidationError, match="counts must match"):
        OAPendingWorkSnapshotCollection.model_validate(
            {**payload, "authoritative_count": 2},
            strict=True,
        )
    with pytest.raises(ValidationError, match="must be unique"):
        OAPendingWorkSnapshotCollection.model_validate(
            {
                **payload,
                "workflows": payload["workflows"] * 2,
                "returned_count": 2,
                "authoritative_count": 2,
            },
            strict=True,
        )


def test_handling_mark_requires_actor_and_timestamp_as_one_record() -> None:
    now = datetime(2026, 8, 19, 12, 5, tzinfo=UTC)

    marked = _record(
        handling_mark="pending_sync_confirmation",
        handling_marked_by_ai_user_id="user-a",
        handling_marked_at=now,
    )
    assert marked.source_status == "pending"

    with pytest.raises(ValidationError, match="requires actor and timestamp"):
        _record(handling_mark="handled_elsewhere")
    with pytest.raises(ValidationError, match="requires a handling mark"):
        _record(handling_marked_by_ai_user_id="user-a")
