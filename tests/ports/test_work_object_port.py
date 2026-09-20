from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from app.ports.task_store import TaskRecord
from app.ports.work_object import (
    InternalWorkObjectRecord,
    OAObservation,
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
    if updates.get("state_authority", "external_snapshot") == "external_snapshot":
        values["oa_observation"] = OAObservation(
            pending_state="legacy_unverified", revision=0, last_seen_at=now, last_checked_at=None,
        )
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


@pytest.mark.parametrize("target", ["user", "department"])
def test_internal_lifecycle_fields_are_consistent(target):
    now = datetime(2026, 9, 20, tzinfo=UTC)
    initial = dict(
        work_object_id="synthetic-lifecycle-model",
        state_authority="internal",
        source_system="eternalai",
        source_kind="manual_dispatch",
        tenant_id="default",
        owner_department_id="office-a",
        initiator_ai_user_id="sender",
        target_kind=target,
        assignee_directory_user_id="recipient" if target == "user" else None,
        assignee_ai_user_id=None,
        assignee_display_name=None if target == "user" else "Synthetic office",
        title="Synthetic",
        kind="工作任务",
        requirement="",
        receipt_requirement="",
        status="assigned" if target == "user" else "department_pending",
        reminder_choices=[],
        version=1,
        created_at=now,
        updated_at=now,
    )
    nullable = dict(
        source_ref=None,
        source_title=None,
        source_status=None,
        source_received_at=None,
        source_created_at=None,
        source_workflow_type_id=None,
        source_fetched_at=None,
        due_at=None,
        handling_mark=None,
        handling_marked_by_ai_user_id=None,
        handling_marked_at=None,
        task_record_id=None,
    )
    initial.update(nullable)
    assert InternalWorkObjectRecord.model_validate(initial).accepted_at is None
    progress = {
        **initial,
        "status": "in_progress",
        "version": 2,
        "accepted_at": now,
        "accepted_by_ai_user_id": "recipient",
    }
    completed = {
        **progress,
        "status": "completed",
        "version": 3,
        "completed_at": now,
        "completed_by_ai_user_id": "recipient",
    }
    assert InternalWorkObjectRecord.model_validate(progress).status == "in_progress"
    assert InternalWorkObjectRecord.model_validate(completed).status == "completed"
    for source, updates in (
        (initial, {"accepted_at": now}),
        (progress, {"accepted_by_ai_user_id": None}),
        (progress, {"version": 1}),
        (progress, {"completed_at": now}),
        (completed, {"completed_by_ai_user_id": "other"}),
        (completed, {"completed_at": None}),
        (completed, {"version": 2}),
    ):
        with pytest.raises(ValidationError):
            InternalWorkObjectRecord.model_validate({**source, **updates})
    legacy = dict(
        work_object_id="synthetic-legacy",
        state_authority="internal",
        source_system="eternalai",
        source_kind="internal_task",
        assignee_ai_user_id="legacy",
        created_at=now,
        updated_at=now,
    )
    legacy.update(nullable, assignee_display_name="Synthetic legacy")
    assert InternalWorkObjectRecord.model_validate(legacy).accepted_at is None
    with pytest.raises(ValidationError):
        InternalWorkObjectRecord.model_validate({**legacy, "accepted_by_ai_user_id": "recipient"})
    with pytest.raises(ValidationError):
        _record(accepted_by_ai_user_id="recipient")

@pytest.mark.parametrize("updates", [
    {"returned_count": True}, {"authoritative_count": False},
    {"returned_count": -1}, {"is_complete": False}, {"unexpected": "field"},
])
def test_reconciliation_accepts_only_complete_unique_collections(
    updates: dict[str, object],
) -> None:
    payload = {"workflows": [], "returned_count": 0, "authoritative_count": 0, "is_complete": True}
    assert OAPendingWorkSnapshotCollection.model_validate(payload).workflows == []
    with pytest.raises(ValidationError):
        OAPendingWorkSnapshotCollection.model_validate({**payload, **updates})


def test_observation_requires_explicit_legacy_or_checked_state() -> None:
    from datetime import timedelta

    now = datetime(2026, 9, 16, tzinfo=UTC)
    assert OAObservation(pending_state="legacy_unverified", revision=0,
                         last_seen_at=now, last_checked_at=None).revision == 0
    assert OAObservation(pending_state="current", revision=1,
                         last_seen_at=now, last_checked_at=now).pending_state == "current"
    assert OAObservation(pending_state="unconfirmed", revision=2,
                         last_seen_at=now, last_checked_at=now + timedelta(seconds=1)).revision == 2
    for update in ({"revision": 0}, {"revision": True}, {"last_checked_at": None},
                   {"last_checked_at": now - timedelta(seconds=1)},
                   {"last_seen_at": now.replace(tzinfo=None)}):
        with pytest.raises(ValidationError):
            OAObservation.model_validate({"pending_state": "unconfirmed", "revision": 1,
                                         "last_seen_at": now, "last_checked_at": now, **update})


def test_sync_subject_ticket_and_status_are_strict_and_default_only() -> None:
    from app.ports.work_object import OASyncStatus, OASyncSubject, OASyncTicket

    now = datetime(2026, 9, 16, tzinfo=UTC)
    subject = OASyncSubject(tenant_id="default", ai_user_id="person-a")
    with pytest.raises(ValidationError):
        OASyncSubject(tenant_id="other", ai_user_id="person-a")
    with pytest.raises(ValidationError):
        OASyncSubject(tenant_id="default", ai_user_id="  ")
    for generation in (True, 0, -1, "1"):
        with pytest.raises(ValidationError):
            OASyncTicket(subject=subject, stream="pending", generation=generation, started_at=now)
    state = OASyncStatus(
        subject=subject,
        stream="pending",
        issued_generation=1,
        applied_generation=0,
        last_attempt_status="failed",
        last_attempt_started_at=now,
        last_attempt_finished_at=now,
        last_success_at=None,
        last_error_code="invalid_response",
    )
    assert state.to_view().attempt_revision == 1
    assert state.to_view().failure_code == "invalid_response"
    assert set(state.to_view().model_dump()) == {
        "status",
        "revision",
        "attempt_revision",
        "last_attempt_at",
        "last_success_at",
        "failure_code",
    }
    for update in ({"last_error_code": None}, {"last_attempt_status": "running"},
                   {"applied_generation": 1}, {"last_attempt_finished_at": None}):
        with pytest.raises(ValidationError):
            OASyncStatus.model_validate({**state.model_dump(), **update})


def test_collection_completeness_does_not_coerce_one_to_true() -> None:
    for value in (1, 1.0, "true", None):
        with pytest.raises(ValidationError, match="requires boolean true"):
            OAPendingWorkSnapshotCollection.model_validate({"workflows": [], "returned_count": 0,
                "authoritative_count": 0, "is_complete": value})
