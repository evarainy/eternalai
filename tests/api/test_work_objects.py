from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.work_objects import (
    OAWorkObjectView,
    WorkObjectService,
    _resolve_handling_capability,
    _view_from_record,
)
from app.db.session import make_async_engine, make_async_session_factory
from app.event_loop import make_event_loop
from app.infra.persistence.work_object.postgresql import PostgreSQLWorkObjectStore
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
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryPort,
    OrganizationUserMembership,
)
from app.ports.request_context import RequestOrgContext
from app.ports.work_object import (
    DispatchReceipt,
    InternalWorkObjectRecord,
    OAPendingWorkSnapshot,
    OAWorkObjectRecord,
    WorkObjectHandlingMark,
    WorkObjectRecord,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope, compute_visibility_scope
from app.ports.work_object_search import normalize_search_query, normalize_search_value
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
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
        self.records = {record.work_object_id: record for record in records or []}
        self.upsert_calls = 0
        self.list_calls: list[dict[str, object]] = []
        self.receipts: dict[tuple[str, str, UUID], DispatchReceipt] = {}

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
            current = next(
                (
                    record
                    for record in self.records.values()
                    if record.state_authority == "external_snapshot"
                    and record.assignee_ai_user_id == assignee_ai_user_id
                    and record.source_ref == snapshot.source_ref
                ),
                None,
            )
            key = (
                current.work_object_id
                if current
                else f"work-{assignee_ai_user_id}-{snapshot.source_ref}"
            )
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

    async def list_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        limit: int = 201,
    ) -> list[WorkObjectRecord]:
        self.list_calls.append(
            {
                "assignee_ai_user_id": scope.principal_ai_user_id,
                "search_term": search_term,
                "limit": limit,
            }
        )
        normalized_search_term = normalize_search_query(search_term)
        records = [record for record in self.records.values() if self._visible(record, scope)]
        if normalized_search_term:
            records = [
                record
                for record in records
                if (
                    record.source_title is not None
                    and normalized_search_term in normalize_search_value(record.source_title)
                )
                or (
                    record.source_ref is not None
                    and normalize_search_value(record.source_ref) == normalized_search_term
                )
                or (
                    record.assignee_display_name is not None
                    and normalize_search_value(record.assignee_display_name)
                    == normalized_search_term
                )
                or (
                    isinstance(record, InternalWorkObjectRecord)
                    and record.title is not None
                    and normalized_search_term in normalize_search_value(record.title)
                )
            ]
        return records[:limit]

    async def get_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.work_object_id == work_object_id and self._visible(record, scope)
            ),
            None,
        )

    async def set_handling_mark_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None:
        record = await self.get_for_scope(work_object_id, scope)
        if (
            record is None
            or record.state_authority != "external_snapshot"
            or record.assignee_ai_user_id != scope.principal_ai_user_id
        ):
            return None
        marked = record.model_copy(
            update={
                "handling_mark": mark,
                "handling_marked_by_ai_user_id": scope.principal_ai_user_id,
                "handling_marked_at": marked_at,
                "updated_at": marked_at,
            }
        )
        self.records[record.work_object_id] = marked
        return marked

    @staticmethod
    def _visible(record: WorkObjectRecord, scope: AuthorizedWorkObjectScope) -> bool:
        if isinstance(record, InternalWorkObjectRecord) and record.source_kind == "manual_dispatch":
            return (
                record.tenant_id == scope.principal_tenant_id
                and record.version is not None
                and record.owner_department_id is not None
                and record.initiator_ai_user_id is not None
                and (
                    record.owner_department_id == scope.principal_department_id
                    or record.initiator_ai_user_id == scope.principal_ai_user_id
                )
            )
        return record.assignee_ai_user_id == scope.principal_ai_user_id

    async def get_dispatch_receipt(
        self,
        *,
        tenant_id: str,
        initiator_ai_user_id: str,
        idempotency_key: UUID,
    ) -> DispatchReceipt | None:
        return self.receipts.get((tenant_id, initiator_ai_user_id, idempotency_key))

    async def create_internal_dispatch(
        self,
        *,
        records: list[InternalWorkObjectRecord],
        receipt: DispatchReceipt,
    ) -> tuple[DispatchReceipt, bool]:
        key = (receipt.tenant_id, receipt.initiator_ai_user_id, receipt.idempotency_key)
        if key in self.receipts:
            return self.receipts[key], False
        self.records.update({record.work_object_id: record for record in records})
        self.receipts[key] = receipt
        return receipt, True


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
    assert first.json()["items"][0]["source_fetched_at"] == NOW.isoformat().replace("+00:00", "Z")
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
            "title": None,
            "requirement": None,
            "receipt_requirement": None,
            "owner_department_id": None,
            "initiator_ai_user_id": None,
            "kind": None,
            "target_kind": None,
            "status": None,
            "reminder_choices": None,
            "reminder_delivery": None,
            "version": None,
            "created_at": "2026-08-19T12:00:00.000000Z",
            "updated_at": "2026-08-19T12:00:00.000000Z",
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
    assert store.records[original.work_object_id].handling_mark is None


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
    records = [_record(source_ref=f"oa-todo-{index}", index=index) for index in range(1, 202)]
    client = _client(MemoryWorkObjectStore(records), RecordingGateway())

    response = client.get("/api/v1/work-objects")

    assert response.status_code == 200
    assert response.json()["limit"] == 200
    assert response.json()["limit_exceeded"] is True
    assert len(response.json()["items"]) == 200


def test_list_search_normalizes_query_before_store_call() -> None:
    records = [_record(index=1), _record(index=2)]
    store = MemoryWorkObjectStore(records)
    client = _client(store, RecordingGateway())

    response = client.get("/api/v1/work-objects", params={"q": "\u3000 APPROVAL\u00a0  \t2\u0085"})

    assert response.status_code == 200
    assert [item["work_object_id"] for item in response.json()["items"]] == ["work-user-a-2"]
    assert store.list_calls == [
        {
            "assignee_ai_user_id": "user-a",
            "search_term": "approval 2",
            "limit": 201,
        }
    ]


@pytest.mark.parametrize("query", [None, "", "   ", "\u3000", "\u00a0", "\t", "\u0085", "\ufeff"])
def test_list_whitespace_query_preserves_the_existing_list_behavior(query: str | None) -> None:
    store = MemoryWorkObjectStore([_record()])
    client = _client(store, RecordingGateway())

    response = client.get("/api/v1/work-objects", params={} if query is None else {"q": query})

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert store.list_calls[0]["search_term"] is None


def test_list_search_reports_overflow_after_filtering() -> None:
    records = [_record(source_ref=f"oa-todo-{index}", index=index) for index in range(1, 202)]
    client = _client(MemoryWorkObjectStore(records), RecordingGateway())

    response = client.get("/api/v1/work-objects", params={"q": "pending approval"})

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
    assert (
        _resolve_handling_capability(
            record=record,
            capabilities=[first, inactive, concrete],
        )
        is first
    )

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

    assert (
        _resolve_handling_capability(
            record=record,
            capabilities=[none_selector, concrete_selector],
        )
        is none_selector
    )


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


def test_visibility_reads_current_directory_on_each_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memberships = [
        OrganizationUserMembership(
            user_id="synthetic-directory-user",
            department_id="synthetic-first",
            job_title="75",
        )
    ]
    lookups: list[str] = []
    scopes: list[AuthorizedWorkObjectScope] = []

    class Directory:
        async def list_user_memberships(self, user_id: str) -> list[OrganizationUserMembership]:
            lookups.append(user_id)
            return list(memberships)

        async def get_department(self, department_id: str) -> OrganizationDepartment:
            return OrganizationDepartment(department_id=department_id, display_name="Synthetic")

    def capture_scope(**kwargs: Any) -> AuthorizedWorkObjectScope:
        scope = compute_visibility_scope(**kwargs)
        scopes.append(scope)
        return scope

    monkeypatch.setattr("app.api.v1.work_objects.compute_visibility_scope", capture_scope)
    service = WorkObjectService(
        store=MemoryWorkObjectStore([_record()]),
        gateway=RecordingGateway(_success_result()),
        capability_registry=cast(CapabilityRegistryPort, StaticCapabilityRegistry()),
        organization_directory=cast(OrganizationDirectoryPort, Directory()),
    )
    principal = Principal(
        ai_user_id="user-a",
        display_name="Synthetic",
        roles=("admin",),
        org_ctx=PrincipalOrgContext(
            directory_user_id="synthetic-directory-user",
            department_id="stale-token-department",
        ),
    )

    async def exercise() -> None:
        first = await service.list_for_principal(principal)
        assert len(first.items) == 1
        memberships[0] = memberships[0].model_copy(
            update={
                "department_id": "synthetic-second",
                "job_title": None,
            }
        )
        assert await service.get_for_principal("work-user-a-1", principal) is not None
        memberships.clear()
        assert len((await service.list_for_principal(principal)).items) == 1

    asyncio.run(exercise())
    assert lookups == ["synthetic-directory-user"] * 3
    assert [scope.principal_department_id for scope in scopes] == [
        "synthetic-first",
        "synthetic-second",
        None,
    ]
    assert all(scope.principal_ai_user_id == "user-a" for scope in scopes)


def test_list_and_detail_apply_computed_visibility_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def inject_scope(**kwargs: Any) -> AuthorizedWorkObjectScope:
        calls.append(kwargs)
        return AuthorizedWorkObjectScope(
            principal_tenant_id=kwargs["principal_tenant_id"],
            principal_ai_user_id="user-b",
            principal_department_id=None,
        )

    monkeypatch.setattr("app.api.v1.work_objects.compute_visibility_scope", inject_scope)
    service = WorkObjectService(
        store=MemoryWorkObjectStore([_record(), _record(owner="user-b")]),
        gateway=RecordingGateway(_success_result()),
        capability_registry=cast(CapabilityRegistryPort, StaticCapabilityRegistry()),
    )
    principal = Principal(
        ai_user_id="user-a",
        display_name="Synthetic",
        roles=("user",),
        org_ctx=PrincipalOrgContext(),
    )

    async def exercise() -> None:
        result = await service.list_for_principal(principal)
        assert [item.work_object_id for item in result.items] == ["work-user-b-1"]
        assert await service.get_for_principal("work-user-a-1", principal) is None
        assert await service.get_for_principal("work-user-b-1", principal) is not None

    asyncio.run(exercise())
    assert (
        calls
        == [
            {
                "principal_tenant_id": "default",
                "principal_ai_user_id": "user-a",
                "principal_department_id": None,
            }
        ]
        * 3
    )


def test_admin_cannot_read_others_work_object_by_id(migrated_database_url: str) -> None:
    # Real persistence predicate: weakening production SQL must expose the seeded row.
    async def exercise() -> None:
        engine = make_async_engine(migrated_database_url)
        factory = make_async_session_factory(engine)
        store = PostgreSQLWorkObjectStore(factory)
        owner = "synthetic-scope001-owner"
        try:
            await store.upsert_oa_pending_workflows(
                assignee_ai_user_id=owner,
                assignee_display_name="Synthetic owner",
                snapshots=[
                    OAPendingWorkSnapshot(
                        source_ref="synthetic-scope001-work",
                        title="Synthetic private memo",
                        status="OA_PENDING",
                        received_at="2026-09-09",
                        created_at="2026-09-09",
                        workflow_type_id="synthetic-workflow",
                    )
                ],
                fetched_at=NOW,
            )
            records = await store.list_for_scope(
                AuthorizedWorkObjectScope(
                    principal_tenant_id="tenant-dispatch-a",
                    principal_ai_user_id=owner,
                    principal_department_id=None,
                )
            )
            assert len(records) == 1
            service = WorkObjectService(
                store=store,
                gateway=RecordingGateway(_success_result()),
                capability_registry=cast(CapabilityRegistryPort, StaticCapabilityRegistry()),
            )
            admin = Principal(
                ai_user_id="synthetic-scope001-admin",
                display_name="Synthetic admin",
                roles=("admin",),
                org_ctx=PrincipalOrgContext(),
            )
            record_id = records[0].work_object_id
            assert await service.get_for_principal(record_id, admin) is None
            assert (await service.list_for_principal(admin)).items == []
            owner_principal = admin.model_copy(update={"ai_user_id": owner, "roles": ("user",)})
            visible = await service.get_for_principal(record_id, owner_principal)
            assert visible is not None and visible.work_object_id == record_id
        finally:
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM work_objects WHERE assignee_ai_user_id = :owner"),
                    {"owner": owner},
                )
                await session.commit()
            await engine.dispose()

    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        runner.run(exercise())


def test_directory_failure_does_not_expose_join_key_or_return_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fastapi import HTTPException

    class FailedDirectory:
        async def list_user_memberships(self, user_id: str) -> list[OrganizationUserMembership]:
            raise RuntimeError(f"synthetic driver query parameter: {user_id}")

    store = MemoryWorkObjectStore([_record()])
    service = WorkObjectService(
        store=store,
        gateway=RecordingGateway(_success_result()),
        capability_registry=cast(CapabilityRegistryPort, StaticCapabilityRegistry()),
        organization_directory=cast(OrganizationDirectoryPort, FailedDirectory()),
    )
    principal = Principal(
        ai_user_id="user-a",
        display_name="Synthetic",
        roles=("user",),
        org_ctx=PrincipalOrgContext(directory_user_id="synthetic-private-join-key"),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.list_for_principal(principal))
    assert error.value.status_code == 503
    assert error.value.detail == {
        "code": "organization_directory_unavailable",
        "message": "Organization directory is unavailable.",
    }
    assert error.value.__suppress_context__ is True
    assert "synthetic-private-join-key" not in str(error.value) + caplog.text
    assert store.list_calls == []


def test_department_and_initiator_scope_reaches_real_store(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import assert_created, request_body

    db = dispatch_db
    sent = assert_created(db, db.post())
    db.actor("other-head", "office-c", "75")
    local_response = db.post(
        request_body(targets=[{"kind": "department", "department_id": "office-a"}])
    )
    assert local_response.status_code == 201
    local_id = local_response.json()["items"][0]["work_object_id"]
    unrelated = assert_created(db, db.post())
    db.actor("sender", "office-a", None)
    visible = db.client.get("/api/v1/work-objects")
    assert visible.status_code == 200
    assert {item["work_object_id"] for item in visible.json()["items"]} == {
        sent["work_object_id"],
        local_id,
    }
    for item_id in (sent["work_object_id"], local_id):
        assert db.client.get("/api/v1/work-objects/" + item_id).status_code == 200
    assert db.client.get("/api/v1/work-objects/" + unrelated["work_object_id"]).status_code == 404


def test_memory_store_keeps_two_personal_dispatches_and_oa_upserts_distinct(dispatch_db) -> None:
    from uuid import uuid4

    from tests.api.test_work_object_dispatch import request_body, run

    db = dispatch_db
    db.membership("recipient-two", "office-b")
    key = uuid4()
    response = db.post(
        request_body(
            targets=[
                {"kind": "user", "directory_user_id": user, "department_id": "office-b"}
                for user in ("recipient", "recipient-two")
            ]
        ),
        key=str(key),
    )
    assert response.status_code == 201
    scope = AuthorizedWorkObjectScope(
        principal_tenant_id="tenant-dispatch-a",
        principal_ai_user_id="ai-sender",
        principal_department_id="office-a",
    )
    records = run(db.store.list_for_scope(scope))
    assert len(records) == 2
    assert all(
        record.assignee_ai_user_id is None and record.source_ref is None for record in records
    )
    receipt = run(
        db.store.get_dispatch_receipt(
            tenant_id="tenant-dispatch-a",
            initiator_ai_user_id="ai-sender",
            idempotency_key=key,
        )
    )
    assert receipt is not None
    memory = MemoryWorkObjectStore()
    assert run(memory.create_internal_dispatch(records=records, receipt=receipt)) == (receipt, True)
    assert run(memory.create_internal_dispatch(records=records, receipt=receipt)) == (
        receipt,
        False,
    )
    ids = {item["work_object_id"] for item in response.json()["items"]}
    assert set(memory.records) == ids
    for record in records:
        assert run(memory.get_for_scope(record.work_object_id, scope)) == record
        assert (
            run(
                memory.set_handling_mark_for_scope(
                    record.work_object_id,
                    scope,
                    "handled_elsewhere",
                    marked_at=NOW,
                )
            )
            is None
        )
    snapshot = OAPendingWorkSnapshot(
        source_ref="synthetic-oa-distinct",
        title="Synthetic OA",
        status="pending",
        received_at="synthetic received",
        created_at="synthetic created",
        workflow_type_id="synthetic type",
    )
    for _ in range(2):
        run(
            memory.upsert_oa_pending_workflows(
                assignee_ai_user_id="ai-sender",
                assignee_display_name="Synthetic sender",
                snapshots=[snapshot],
                fetched_at=NOW,
            )
        )
    assert len(memory.records) == 3
    oa = next(
        record
        for record in memory.records.values()
        if record.state_authority == "external_snapshot"
    )
    marked = run(
        memory.set_handling_mark_for_scope(
            oa.work_object_id,
            scope,
            "handled_elsewhere",
            marked_at=NOW,
        )
    )
    assert marked is not None and marked.handling_marked_by_ai_user_id == "ai-sender"
    assert {key: memory.records[key] for key in ids} == {
        record.work_object_id: record for record in records
    }


def test_handling_mark_uses_scope_actor_and_preserves_oa_only_write(
    dispatch_db, monkeypatch
) -> None:
    from tests.api.test_work_object_dispatch import assert_created, run

    db = dispatch_db
    for user in ("ai-sender", "ai-other"):
        run(
            db.store.upsert_oa_pending_workflows(
                assignee_ai_user_id=user,
                assignee_display_name="Synthetic owner",
                snapshots=[
                    OAPendingWorkSnapshot(
                        source_ref="mark-synthetic",
                        title="Synthetic OA",
                        status="pending",
                        received_at="2026-09-10",
                        created_at="2026-09-10",
                        workflow_type_id="synthetic",
                    )
                ],
                fetched_at=NOW,
            )
        )
    ids = {row["assignee_ai_user_id"]: row["work_object_id"] for row in db.rows("work_objects")}
    visible_internal = assert_created(db, db.post())
    path = "/api/v1/work-objects/"
    # Real HTTP principal: another owner's OA and visible internal objects remain unwritable.
    for item_id in (ids["ai-other"], visible_internal["work_object_id"]):
        response = db.client.patch(
            path + item_id + "/handling-mark",
            json={"mark": "handled_elsewhere"},
            headers=TEST_CSRF_HEADERS,
        )
        assert response.status_code == 404
    before = {row["work_object_id"]: row for row in db.rows("work_objects")}
    assert all(row["handling_mark"] is None for row in before.values())
    scope = AuthorizedWorkObjectScope(
        principal_tenant_id="tenant-dispatch-a",
        principal_ai_user_id="ai-other",
        principal_department_id=None,
    )
    # This seam intentionally makes scope.actor different from principal.actor.
    monkeypatch.setattr(db.service, "_handling_scope", lambda _principal: scope)
    changed = run(
        db.service.set_handling_mark_for_principal(
            ids["ai-other"], db.tokens.principal, "handled_elsewhere"
        )
    )
    assert changed is not None and changed.work_object_id == ids["ai-other"]
    after = {row["work_object_id"]: row for row in db.rows("work_objects")}
    assert after[ids["ai-other"]]["handling_marked_by_ai_user_id"] == "ai-other"
    assert after[ids["ai-other"]]["handling_mark"] == "handled_elsewhere"
    assert after[ids["ai-sender"]] == before[ids["ai-sender"]]
    assert after[visible_internal["work_object_id"]] == before[visible_internal["work_object_id"]]


@pytest.mark.parametrize(
    "path,method", [("", "get"), ("/synthetic-missing", "get"), ("/dispatch", "post")]
)
def test_directory_failure_has_consistent_error_contract(
    dispatch_db, monkeypatch, caplog, path, method
) -> None:
    from tests.api.test_work_object_dispatch import assert_error

    db = dispatch_db

    async def failed(_key):
        raise RuntimeError("SYNTHETIC-DIRECTORY-FAILURE")

    monkeypatch.setattr(db.directory, "list_user_memberships", failed)
    response = db.post() if method == "post" else db.client.get("/api/v1/work-objects" + path)
    assert_error(response, 503, "organization_directory_unavailable")
    assert "error_code" not in response.text
    assert "SYNTHETIC-DIRECTORY-FAILURE" not in response.text + caplog.text
    assert db.counts() == (0, 0)
    operation = db.client.app.openapi()["paths"][
        "/api/v1/work-objects" + ("/{work_object_id}" if path == "/synthetic-missing" else path)
    ][method]
    assert "503" in operation["responses"]


def test_background_sync_stops_after_upsert_without_directory_or_list(
    dispatch_db, monkeypatch
) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    db.service._gateway = RecordingGateway(_success_result())
    calls = []

    async def canary(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("synthetic read must not follow background upsert")

    monkeypatch.setattr(db.directory, "list_user_memberships", canary)
    monkeypatch.setattr(db.service._capability_registry, "list", canary)
    monkeypatch.setattr(db.store, "list_for_scope", canary)
    monkeypatch.setattr(db.store, "get_for_scope", canary)
    assert run(db.service.sync_for_background(db.tokens.principal)) is None
    rows = db.rows("work_objects")
    assert len(rows) == 1
    assert rows[0]["source_ref"] == "oa-todo-1" and rows[0]["assignee_ai_user_id"] == "ai-sender"
    assert calls == []


@pytest.mark.parametrize(
    "failure,expected_auth,expected_code",
    [
        ("identity_unbound", True, None),
        ("identity_expired", True, None),
        ("identity_revoked", True, None),
        ("adapter_timeout", False, "timeout"),
        ("adapter_http_500", False, "upstream_5xx"),
        ("adapter_payload_invalid", False, "invalid_response"),
        ("local-db", False, None),
    ],
)
def test_background_sync_keeps_auth_upstream_and_local_failure_classes(
    dispatch_db, monkeypatch, failure, expected_auth, expected_code
) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    if failure == "local-db":
        db.service._gateway = RecordingGateway(_success_result())

        async def fail(**_kwargs):
            raise RuntimeError("synthetic database failure")

        monkeypatch.setattr(db.store, "upsert_oa_pending_workflows", fail)
    else:
        db.service._gateway = RecordingGateway(
            ExecutionResult(status="failed", trace_id="synthetic-trace", error_code=failure)
        )
    with pytest.raises(BackgroundWorkObjectSyncError) as error:
        run(db.service.sync_for_background(db.tokens.principal))
    assert error.value.authentication_denied is expected_auth
    assert error.value.failure_code == expected_code
    assert db.counts() == (0, 0)
