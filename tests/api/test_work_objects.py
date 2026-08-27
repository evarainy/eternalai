from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.api.v1.work_objects import (
    OAWorkObjectView,
    WorkObjectService,
    _resolve_handling_capability,
    _view_from_record,
)
from app.main import create_app
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.capability_gateway import ExecutionResult
from app.ports.capability_registry import (
    CapabilityAutomationLevel,
    CapabilityRegistryPort,
    CapabilitySpec,
    CapabilityStatus,
)
from app.ports.credential_binding import BackgroundWorkObjectSyncError
from app.ports.request_context import RequestOrgContext
from app.ports.work_object import (
    InternalWorkObjectRecord,
    OAPendingWorkSnapshot,
    OAWorkObjectRecord,
    WorkObjectHandlingMark,
    WorkObjectRecord,
)
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)
from tests.runtime.registry_fakes import StaticCapabilityRegistry, active_capability

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


class MemoryWorkObjectStore:
    def __init__(self, records: list[WorkObjectRecord] | None = None) -> None:
        self.records = {
            (record.assignee_ai_user_id, record.source_ref): record
            for record in records or []
        }
        self.upsert_calls = 0

    async def upsert_oa_pending_workflows(
        self,
        *,
        assignee_ai_user_id: str,
        assignee_display_name: str,
        snapshots: list[OAPendingWorkSnapshot],
        fetched_at: datetime,
    ) -> None:
        self.upsert_calls += 1
        for snapshot in snapshots:
            key = (assignee_ai_user_id, snapshot.source_ref)
            current = self.records.get(key)
            self.records[key] = OAWorkObjectRecord(
                work_object_id=(
                    current.work_object_id
                    if current is not None
                    else f"work-{assignee_ai_user_id}-{snapshot.source_ref}"
                ),
                state_authority="external_snapshot",
                source_system="oa",
                source_kind="pending_workflow",
                source_ref=snapshot.source_ref,
                assignee_ai_user_id=assignee_ai_user_id,
                assignee_display_name=assignee_display_name,
                due_at=None,
                source_title=snapshot.title,
                source_status=snapshot.status,
                source_received_at=snapshot.received_at,
                source_created_at=snapshot.created_at,
                source_workflow_type_id=snapshot.workflow_type_id,
                source_fetched_at=fetched_at,
                handling_mark=current.handling_mark if current else None,
                handling_marked_by_ai_user_id=(
                    current.handling_marked_by_ai_user_id if current else None
                ),
                handling_marked_at=current.handling_marked_at if current else None,
                task_record_id=current.task_record_id if current else None,
                created_at=current.created_at if current else fetched_at,
                updated_at=fetched_at,
            )

    async def list_for_assignee(
        self,
        assignee_ai_user_id: str,
        *,
        limit: int = 201,
    ) -> list[WorkObjectRecord]:
        return [
            record
            for record in self.records.values()
            if record.assignee_ai_user_id == assignee_ai_user_id
        ][:limit]

    async def get_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
    ) -> WorkObjectRecord | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.work_object_id == work_object_id
                and record.assignee_ai_user_id == assignee_ai_user_id
            ),
            None,
        )

    async def set_handling_mark_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None:
        record = await self.get_for_assignee(work_object_id, assignee_ai_user_id)
        if record is None:
            return None
        marked = record.model_copy(
            update={
                "handling_mark": mark,
                "handling_marked_by_ai_user_id": assignee_ai_user_id,
                "handling_marked_at": marked_at,
                "updated_at": marked_at,
            }
        )
        self.records[(assignee_ai_user_id, record.source_ref)] = marked
        return marked


class RecordingGateway:
    def __init__(self, result: ExecutionResult | None = None) -> None:
        self.result = result or _success_result()
        self.calls: list[dict[str, Any]] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append(
            {
                "task_id": task_id,
                "session_id": session_id,
                "ai_user_id": ai_user_id,
                "capability_id": capability_id,
                "arguments": arguments,
                "request_context": request_context,
            }
        )
        return self.result


def _success_result(*, title: str = "Pending approval") -> ExecutionResult:
    return ExecutionResult(
        status="completed",
        trace_id="trace-work-object",
        data={
            "workflows": [
                {
                    "todo_id": "oa-todo-1",
                    "title": title,
                    "status": "OA_PENDING",
                    "received_at": "2026-08-18",
                    "created_at": "2026-08-17",
                    "workflow_type_id": "workflow-1",
                }
            ],
            "returned_count": 1,
            "authoritative_count": 1,
            "is_complete": True,
        },
    )


def _record(
    *,
    owner: str = "user-a",
    source_ref: str = "oa-todo-1",
    index: int = 1,
) -> WorkObjectRecord:
    return OAWorkObjectRecord(
        work_object_id=f"work-{owner}-{index}",
        state_authority="external_snapshot",
        source_system="oa",
        source_kind="pending_workflow",
        source_ref=source_ref,
        assignee_ai_user_id=owner,
        assignee_display_name=f"Display {owner}",
        due_at=None,
        source_title=f"Pending approval {index}",
        source_status="OA_PENDING",
        source_received_at="2026-08-18",
        source_created_at="2026-08-17",
        source_workflow_type_id="workflow-1",
        source_fetched_at=NOW - timedelta(minutes=index),
        handling_mark=None,
        handling_marked_by_ai_user_id=None,
        handling_marked_at=None,
        task_record_id=None,
        created_at=NOW - timedelta(minutes=index),
        updated_at=NOW - timedelta(minutes=index),
    )


def _handling_capability(
    capability_id: str,
    *,
    automation_level: CapabilityAutomationLevel,
    source_system: str = "oa",
    source_kind: str = "pending_workflow",
    source_workflow_type_id: str | None = "workflow-1",
    status: CapabilityStatus = "active",
) -> CapabilitySpec:
    base = active_capability(capability_id)
    return CapabilitySpec.model_validate(
        {
            **base.model_dump(mode="python"),
            "automation_level": automation_level,
            "status": status,
            "handles_work_objects": [
                {
                    "source_system": source_system,
                    "source_kind": source_kind,
                    "source_workflow_type_id": source_workflow_type_id,
                }
            ],
        }
    )


def _client(
    store: MemoryWorkObjectStore,
    gateway: RecordingGateway,
    *,
    user_id: str = "user-a",
    capabilities: tuple[CapabilitySpec, ...] = (),
) -> TestClient:
    tokens = StaticSessionTokens(roles=("user",))
    tokens.principal = Principal(
        ai_user_id=user_id,
        display_name=f"Display {user_id}",
        roles=("user",),
        org_ctx=PrincipalOrgContext(tenant_id="tenant-1", department_id="dept-1"),
    )
    service = WorkObjectService(
        store=store,
        gateway=gateway,
        capability_registry=cast(
            CapabilityRegistryPort,
            StaticCapabilityRegistry(*capabilities),
        ),
        clock=lambda: NOW,
        id_factory=lambda: "operation-1",
    )
    client = TestClient(
        create_app(
            work_object_service=service,
            session_tokens=tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())
    return client


def test_online_sync_uses_trusted_principal_and_is_idempotent() -> None:
    store = MemoryWorkObjectStore()
    gateway = RecordingGateway()
    client = _client(store, gateway)

    first = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    second = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(store.records) == 1
    assert store.upsert_calls == 2
    assert first.json()["items"][0]["source_status"] == "OA_PENDING"
    assert first.json()["items"][0]["handling_action"] == "go_source_system"
    assert first.json()["items"][0]["handling_capability_id"] is None
    assert first.json()["items"][0]["state_authority"] == "external_snapshot"
    assert first.json()["items"][0]["source_fetched_at"] == NOW.isoformat().replace(
        "+00:00", "Z"
    )
    assert "assignee_ai_user_id" not in first.json()["items"][0]
    call = gateway.calls[0]
    assert call["ai_user_id"] == "user-a"
    assert call["capability_id"] == "oa.list_pending_workflows"
    assert call["arguments"] == {}
    assert call["request_context"].tenant_id == "tenant-1"
    assert call["request_context"].department_id == "dept-1"


def test_api_serializes_the_internal_arm_without_oa_snapshot_fields() -> None:
    internal = InternalWorkObjectRecord(
        work_object_id="work-internal-1",
        state_authority="internal",
        source_system="eternalai",
        source_kind="internal_task",
        source_ref=None,
        assignee_ai_user_id="user-a",
        assignee_display_name="Display user-a",
        due_at=None,
        source_title=None,
        source_status=None,
        source_received_at=None,
        source_created_at=None,
        source_workflow_type_id=None,
        source_fetched_at=None,
        handling_mark=None,
        handling_marked_by_ai_user_id=None,
        handling_marked_at=None,
        task_record_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    client = _client(MemoryWorkObjectStore([internal]), RecordingGateway())

    response = client.get("/api/v1/work-objects")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "work_object_id": "work-internal-1",
            "state_authority": "internal",
            "source_system": "eternalai",
            "source_kind": "internal_task",
            "source_ref": None,
            "assignee_display_name": "Display user-a",
            "due_at": None,
            "source_title": None,
            "source_status": None,
            "source_received_at": None,
            "source_created_at": None,
            "source_workflow_type_id": None,
            "source_fetched_at": None,
            "handling_mark": None,
            "handling_marked_at": None,
            "task_record_id": None,
            "handling_action": "view_only",
            "handling_capability_id": None,
        }
    ]


@pytest.mark.parametrize(
    ("result", "authentication_denied", "failure_code"),
    [
        (
            ExecutionResult(
                status="binding_required",
                error_code="identity_expired",
                trace_id="trace-expired",
            ),
            True,
            None,
        ),
        (
            ExecutionResult(
                status="timeout",
                error_code="adapter_timeout",
                trace_id="trace-timeout",
            ),
            False,
            "timeout",
        ),
        (
            ExecutionResult(
                status="failed",
                error_code="adapter_error",
                trace_id="trace-unknown-adapter-error",
            ),
            False,
            None,
        ),
    ],
)
def test_background_sync_exposes_only_authentication_denial_classification(
    result: ExecutionResult,
    authentication_denied: bool,
    failure_code: str | None,
) -> None:
    service = WorkObjectService(
        store=MemoryWorkObjectStore(),
        gateway=RecordingGateway(result),
        capability_registry=cast(
            CapabilityRegistryPort,
            StaticCapabilityRegistry(),
        ),
        clock=lambda: NOW,
        id_factory=lambda: "operation-background",
    )
    principal = Principal(
        ai_user_id="user-a",
        display_name="Display user-a",
        roles=("user",),
        org_ctx=PrincipalOrgContext(tenant_id="tenant-1", department_id="dept-1"),
    )

    with pytest.raises(BackgroundWorkObjectSyncError) as captured:
        asyncio.run(service.sync_for_background(principal))

    assert captured.value.authentication_denied is authentication_denied
    assert captured.value.failure_code == failure_code


def test_handling_mark_does_not_change_oa_snapshot_or_remove_item() -> None:
    original = _record()
    store = MemoryWorkObjectStore([original])
    client = _client(store, RecordingGateway())

    response = client.patch(
        f"/api/v1/work-objects/{original.work_object_id}/handling-mark",
        headers=TEST_CSRF_HEADERS,
        json={"mark": "pending_sync_confirmation"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["handling_mark"] == "pending_sync_confirmation"
    assert payload["source_status"] == original.source_status
    assert payload["source_title"] == original.source_title
    listed = client.get("/api/v1/work-objects").json()["items"]
    assert [item["work_object_id"] for item in listed] == [original.work_object_id]


def test_cross_user_detail_and_mark_are_both_not_found() -> None:
    original = _record(owner="user-a")
    store = MemoryWorkObjectStore([original])
    client = _client(store, RecordingGateway(), user_id="user-b")

    listed = client.get("/api/v1/work-objects")
    detail = client.get(f"/api/v1/work-objects/{original.work_object_id}")
    update = client.patch(
        f"/api/v1/work-objects/{original.work_object_id}/handling-mark",
        headers=TEST_CSRF_HEADERS,
        json={"mark": "handled_elsewhere"},
    )

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert detail.status_code == 404
    assert update.status_code == 404
    assert store.records[("user-a", original.source_ref)].handling_mark is None


def test_sync_failure_writes_nothing_and_stored_data_remains_readable() -> None:
    original = _record()
    store = MemoryWorkObjectStore([original])
    gateway = RecordingGateway(
        ExecutionResult(
            status="timeout",
            error_code="adapter_timeout",
            trace_id="trace-timeout",
        )
    )
    client = _client(store, gateway)

    failed = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    stored = client.get("/api/v1/work-objects")

    assert failed.status_code == 503
    assert failed.json()["detail"] == {
        "code": "work_object_sync_failed",
        "message": "Work Object synchronization failed; stored data is unchanged.",
    }
    assert store.upsert_calls == 0
    assert stored.status_code == 200
    assert original.source_fetched_at is not None
    assert stored.json()["items"][0]["source_fetched_at"] == (
        original.source_fetched_at.isoformat().replace("+00:00", "Z")
    )


def test_expired_oa_identity_returns_recognizable_reauthentication_action() -> None:
    store = MemoryWorkObjectStore([_record()])
    gateway = RecordingGateway(
        ExecutionResult(
            status="binding_required",
            error_code="identity_expired",
            trace_id="trace-expired",
        )
    )
    client = _client(store, gateway)

    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "oa_reauthentication_required",
        "message": "OA authentication is no longer usable; authenticate again.",
        "next_action": "reauthenticate",
    }
    assert store.upsert_calls == 0


def test_binding_scope_required_preserves_session_and_writes_nothing() -> None:
    store = MemoryWorkObjectStore([_record()])
    gateway = RecordingGateway(
        ExecutionResult(
            status="binding_required",
            error_code="needs_binding_scope",
            trace_id="trace-binding-scope",
        )
    )
    client = _client(store, gateway)

    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "oa_binding_scope_required",
        "message": "OA binding scope must be clarified before synchronization.",
        "next_action": "clarify_binding_scope",
    }
    assert store.upsert_calls == 0
    assert client.get("/api/v1/work-objects").status_code == 200


def test_invalid_gateway_payload_fails_before_any_write() -> None:
    store = MemoryWorkObjectStore()
    gateway = RecordingGateway(
        ExecutionResult(
            status="completed",
            trace_id="trace-invalid",
            data={
                "workflows": [{"todo_id": "oa-todo-1", "title": "partial"}],
                "returned_count": 1,
                "authoritative_count": 1,
                "is_complete": True,
            },
        )
    )
    client = _client(store, gateway)

    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "work_object_sync_invalid"
    assert store.upsert_calls == 0


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("todo_id", "   "),
        ("title", "<b>Pending approval</b>"),
    ],
)
def test_semantically_invalid_gateway_text_fails_before_any_write(
    field: str,
    invalid_value: str,
) -> None:
    workflow = {
        "todo_id": "oa-todo-1",
        "title": "Pending approval",
        "status": "OA_PENDING",
        "received_at": "2026-08-18",
        "created_at": "2026-08-17",
        "workflow_type_id": "workflow-1",
    }
    workflow[field] = invalid_value
    store = MemoryWorkObjectStore()
    gateway = RecordingGateway(
        ExecutionResult(
            status="completed",
            trace_id="trace-invalid-text",
            data={
                "workflows": [workflow],
                "returned_count": 1,
                "authoritative_count": 1,
                "is_complete": True,
            },
        )
    )
    client = _client(store, gateway)

    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "work_object_sync_invalid"
    assert store.upsert_calls == 0


def test_list_returns_one_bounded_batch_with_explicit_overflow() -> None:
    records = [
        _record(source_ref=f"oa-todo-{index}", index=index)
        for index in range(1, 202)
    ]
    client = _client(MemoryWorkObjectStore(records), RecordingGateway())

    response = client.get("/api/v1/work-objects")

    assert response.status_code == 200
    assert response.json()["limit"] == 200
    assert response.json()["limit_exceeded"] is True
    assert len(response.json()["items"]) == 200


@pytest.mark.parametrize(
    ("automation_level", "expected_action"),
    [("full", "ai_draft"), ("assisted", "self_serve")],
)
def test_unique_capability_mapping_projects_capability_action(
    automation_level: CapabilityAutomationLevel,
    expected_action: str,
) -> None:
    capability = _handling_capability(
        f"oa.handle.{automation_level}",
        automation_level=automation_level,
    )
    client = _client(
        MemoryWorkObjectStore([_record()]),
        RecordingGateway(),
        capabilities=(capability,),
    )

    payload = client.get("/api/v1/work-objects").json()["items"][0]

    assert payload["handling_action"] == expected_action
    assert payload["handling_capability_id"] == capability.capability_id


def test_handled_elsewhere_overrides_a_full_capability_mapping() -> None:
    capability = _handling_capability(
        "oa.handle.full",
        automation_level="full",
    )
    record = _record().model_copy(
        update={
            "handling_mark": "handled_elsewhere",
            "handling_marked_by_ai_user_id": "user-a",
            "handling_marked_at": NOW,
        }
    )
    client = _client(
        MemoryWorkObjectStore([record]),
        RecordingGateway(),
        capabilities=(capability,),
    )

    payload = client.get("/api/v1/work-objects").json()["items"][0]

    assert payload["handling_action"] == "view_only"
    assert payload["handling_capability_id"] is None


def test_resolver_is_exact_active_and_fail_closed_on_ambiguity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    record = _record()
    first = _handling_capability("oa.handle.first", automation_level="full")
    second = _handling_capability("oa.handle.second", automation_level="assisted")
    inactive = _handling_capability(
        "oa.handle.disabled",
        automation_level="full",
        status="disabled",
    )
    concrete = _handling_capability(
        "oa.handle.other-workflow",
        automation_level="full",
        source_workflow_type_id="workflow-other",
    )

    assert _resolve_handling_capability(record=record, capabilities=[]) is None
    assert _resolve_handling_capability(
        record=record,
        capabilities=[first, inactive, concrete],
    ) is first

    with caplog.at_level("WARNING"):
        ambiguous = _resolve_handling_capability(
            record=record,
            capabilities=[first, second],
        )
    assert ambiguous is None
    assert "oa.handle.first" in caplog.text
    assert "oa.handle.second" in caplog.text
    assert record.source_ref is not None
    assert record.source_ref not in caplog.text


def test_none_workflow_type_matches_only_none_selector() -> None:
    record = InternalWorkObjectRecord(
        work_object_id="work-internal-none",
        state_authority="internal",
        source_system="eternalai",
        source_kind="internal_task",
        source_ref=None,
        assignee_ai_user_id="user-a",
        assignee_display_name="Display user-a",
        due_at=None,
        source_title=None,
        source_status=None,
        source_received_at=None,
        source_created_at=None,
        source_workflow_type_id=None,
        source_fetched_at=None,
        handling_mark=None,
        handling_marked_by_ai_user_id=None,
        handling_marked_at=None,
        task_record_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    none_selector = _handling_capability(
        "internal.none",
        automation_level="full",
        source_system="eternalai",
        source_kind="internal_task",
        source_workflow_type_id=None,
    )
    concrete_selector = _handling_capability(
        "internal.concrete",
        automation_level="full",
        source_system="eternalai",
        source_kind="internal_task",
        source_workflow_type_id="specific",
    )

    assert _resolve_handling_capability(
        record=record,
        capabilities=[none_selector, concrete_selector],
    ) is none_selector


@pytest.mark.parametrize("handling_action", ["go_source_system", "view_only"])
def test_view_model_rejects_capability_id_for_non_capability_action(
    handling_action: str,
) -> None:
    view = _view_from_record(_record(), [])

    with pytest.raises(
        ValueError,
        match="handling_capability_id must be null",
    ):
        OAWorkObjectView.model_validate(
            {
                **view.model_dump(mode="python"),
                "handling_action": handling_action,
                "handling_capability_id": "oa.unexpected",
            }
        )


@pytest.mark.parametrize("handling_action", ["ai_draft", "self_serve"])
def test_view_model_requires_capability_id_for_capability_action(
    handling_action: str,
) -> None:
    view = _view_from_record(_record(), [])

    with pytest.raises(
        ValueError,
        match="handling_capability_id is required",
    ):
        OAWorkObjectView.model_validate(
            {
                **view.model_dump(mode="python"),
                "handling_action": handling_action,
                "handling_capability_id": None,
            }
        )


def test_work_object_routes_require_valid_authentication() -> None:
    client = TestClient(create_app())

    assert client.get("/api/v1/work-objects").status_code == 401
