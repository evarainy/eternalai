"""Contract tests for TaskStorePort and SessionStorePort."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.task_store import (
    TASK_STORE_QUERY_LIMIT,
    SessionRecord,
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_STORE_SOURCE = REPO_ROOT / "app" / "ports" / "task_store.py"


def test_task_record_uses_spec_8_6_2_fields_and_status_values() -> None:
    record = TaskRecord(
        task_id="task-001",
        session_id="session-001",
        ai_user_id="ai-user-001",
        tenant_id="tenant-001",
        status="no_capability_found",
    )

    assert record.task_id == "task-001"
    assert record.session_id == "session-001"
    assert record.ai_user_id == "ai-user-001"
    assert record.tenant_id == "tenant-001"
    assert record.status == "no_capability_found"
    assert record.trace_id is None
    assert record.capability_id is None
    assert record.error_code is None
    assert set(get_args(TaskStatus)) == {
        "created",
        "running",
        "waiting_user",
        "completed",
        "failed",
        "no_capability_found",
    }


def test_task_record_rejects_status_outside_common_contract() -> None:
    try:
        TaskRecord(
            task_id="task-001",
            session_id="session-001",
            ai_user_id="ai-user-001",
            tenant_id="tenant-001",
            status="clarification_needed",
        )
    except ValidationError as exc:
        assert "Input should be" in str(exc)
    else:
        raise AssertionError("TaskRecord accepted a status outside spec section 8.6.2")


def test_task_record_requires_explicit_tenant_field() -> None:
    with pytest.raises(ValidationError, match="tenant_id"):
        TaskRecord(
            task_id="task-001",
            session_id="session-001",
            ai_user_id="ai-user-001",
            status="created",
        )


@pytest.mark.parametrize("tenant_id", ["", " ", "\t\r\n"])
def test_task_record_rejects_blank_tenant_before_store_write(tenant_id: str) -> None:
    with pytest.raises(ValidationError, match="tenant_id must not be blank"):
        TaskRecord(
            task_id="task-001",
            session_id="session-001",
            ai_user_id="ai-user-001",
            tenant_id=tenant_id,
            status="created",
        )


def test_task_record_allows_explicit_null_only_for_historical_hydration() -> None:
    record = TaskRecord(
        task_id="historical-task",
        session_id="historical-session",
        ai_user_id="historical-user",
        tenant_id=None,
        status="completed",
    )

    assert record.tenant_id is None


def test_session_record_stays_minimal_without_invented_identity_semantics() -> None:
    record = SessionRecord(session_id="session-001")

    assert record.session_id == "session-001"
    assert set(SessionRecord.model_fields) == {"session_id"}


def test_task_event_record_is_passive_event_contract() -> None:
    timestamp = datetime(2026, 6, 1, tzinfo=timezone.utc)
    event = TaskEventRecord(
        event_id="event-001",
        task_id="task-001",
        event_type="status_changed",
        timestamp=timestamp,
        payload={"status": "running"},
    )

    assert event.event_id == "event-001"
    assert event.task_id == "task-001"
    assert event.event_type == "status_changed"
    assert event.timestamp == timestamp
    assert event.payload == {"status": "running"}


class TestTaskStorePortProtocol:
    def test_protocol_is_not_runtime_checkable(self) -> None:
        assert hasattr(TaskStorePort, "__protocol_attrs__")
        assert not getattr(TaskStorePort, "_is_runtime_protocol", False)

    def test_create_task_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(TaskStorePort.create_task)
        signature = inspect.signature(TaskStorePort.create_task)

        assert list(signature.parameters) == ["self", "record"]
        assert hints["record"] is TaskRecord
        assert hints["return"] is TaskRecord
        assert inspect.iscoroutinefunction(TaskStorePort.create_task)

    def test_get_task_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(TaskStorePort.get_task)
        signature = inspect.signature(TaskStorePort.get_task)

        assert list(signature.parameters) == ["self", "task_id"]
        assert hints["task_id"] is str
        assert hints["return"] == TaskRecord | None
        assert inspect.iscoroutinefunction(TaskStorePort.get_task)

    def test_update_status_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(TaskStorePort.update_status)
        signature = inspect.signature(TaskStorePort.update_status)

        assert list(signature.parameters) == ["self", "task_id", "status", "error_code"]
        assert hints["task_id"] is str
        assert hints["status"] == TaskStatus
        assert hints["error_code"] == str | None
        assert signature.parameters["error_code"].default is None
        assert hints["return"] is TaskRecord
        assert inspect.iscoroutinefunction(TaskStorePort.update_status)

    def test_append_event_signature_is_minimal_passive_contract(self) -> None:
        hints = get_type_hints(TaskStorePort.append_event)
        signature = inspect.signature(TaskStorePort.append_event)

        assert list(signature.parameters) == ["self", "task_id", "event"]
        assert hints["task_id"] is str
        assert hints["event"] is TaskEventRecord
        assert hints["return"] is type(None)
        assert inspect.iscoroutinefunction(TaskStorePort.append_event)

    def test_list_tasks_signature_requires_bounded_filters(self) -> None:
        hints = get_type_hints(TaskStorePort.list_tasks)
        signature = inspect.signature(TaskStorePort.list_tasks)

        assert list(signature.parameters) == [
            "self",
            "session_id",
            "ai_user_id",
            "tenant_id",
        ]
        assert signature.parameters["session_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["ai_user_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["session_id"].default is None
        assert signature.parameters["ai_user_id"].default is None
        assert signature.parameters["tenant_id"].default is None
        assert hints["session_id"] == str | None
        assert hints["ai_user_id"] == str | None
        assert hints["tenant_id"] == str | None
        assert hints["return"] == list[TaskRecord]
        assert inspect.iscoroutinefunction(TaskStorePort.list_tasks)

    def test_list_events_signature_is_bounded_by_contract_constant(self) -> None:
        hints = get_type_hints(TaskStorePort.list_events)
        signature = inspect.signature(TaskStorePort.list_events)

        assert list(signature.parameters) == ["self", "task_id"]
        assert hints["task_id"] is str
        assert hints["return"] == list[TaskEventRecord]
        assert inspect.iscoroutinefunction(TaskStorePort.list_events)
        assert TASK_STORE_QUERY_LIMIT == 100


class TestSessionStorePortProtocol:
    def test_protocol_is_not_runtime_checkable(self) -> None:
        assert hasattr(SessionStorePort, "__protocol_attrs__")
        assert not getattr(SessionStorePort, "_is_runtime_protocol", False)

    def test_create_session_signature_is_minimal_contract(self) -> None:
        hints = get_type_hints(SessionStorePort.create_session)
        signature = inspect.signature(SessionStorePort.create_session)

        assert list(signature.parameters) == ["self", "record"]
        assert hints["record"] is SessionRecord
        assert hints["return"] is SessionRecord
        assert inspect.iscoroutinefunction(SessionStorePort.create_session)

    def test_get_session_signature_is_minimal_contract(self) -> None:
        hints = get_type_hints(SessionStorePort.get_session)
        signature = inspect.signature(SessionStorePort.get_session)

        assert list(signature.parameters) == ["self", "session_id"]
        assert hints["session_id"] is str
        assert hints["return"] == SessionRecord | None
        assert inspect.iscoroutinefunction(SessionStorePort.get_session)


def test_task_store_source_does_not_contain_concrete_storage_terms() -> None:
    source = TASK_STORE_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = ("sqlalchemy", "redis", "sqlite", "postgres", "open(")
    assert not any(term in source for term in forbidden_terms)


def test_task_event_payload_is_the_only_unstructured_extension_point() -> None:
    hints = get_type_hints(TaskEventRecord)

    assert hints["payload"] == dict[str, Any]
    assert "metadata" not in TaskRecord.model_fields
    assert "metadata" not in SessionRecord.model_fields
