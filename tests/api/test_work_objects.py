from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.v1.work_objects import (
    OAWorkObjectView,
    WorkObjectService,
    _lifecycle_view,
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
    OAObservation,
    OAPendingWorkSnapshot,
    OAPendingWorkSnapshotCollection,
    OASyncClockInvalid,
    OASyncFailureCode,
    OASyncOutcomeUnknown,
    OASyncStatus,
    OASyncStatusView,
    OASyncStream,
    OASyncSubject,
    OASyncTicket,
    OAView,
    OAWorkObjectRecord,
    WorkObjectHandlingMark,
    WorkObjectReadBatch,
    WorkObjectRecord,
)
from app.ports.work_object_lifecycle import (
    CompletionFilter,
    LifecycleEventRecord,
    LifecycleMutationResult,
    LifecycleStoreError,
    can_replay_lifecycle_event,
    lifecycle_etag,
    lifecycle_role_allowed,
    lifecycle_transition_allowed,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope, compute_visibility_scope
from app.ports.work_object_search import normalize_search_query, normalize_search_value
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    MemorySessionRevocations,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)
from tests.runtime.registry_fakes import StaticCapabilityRegistry, active_capability

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


class MemoryWorkObjectStore:
    def __init__(self, records: list[WorkObjectRecord] | None = None) -> None:
        self.records = {record.work_object_id: record for record in records or []}
        self.states: dict[tuple[str, str], OASyncStatus] = {}
        self.apply_calls = 0
        self.upsert_calls = 0
        self.list_calls: list[dict[str, object]] = []
        self.receipts: dict[tuple[str, str, UUID], DispatchReceipt] = {}
        self.lifecycle_events: list[LifecycleEventRecord] = []

    @staticmethod
    def _project(record: WorkObjectRecord, scope: AuthorizedWorkObjectScope) -> WorkObjectRecord:
        if scope.principal_tenant_id != "default" and record.state_authority == "external_snapshot":
            return record.model_copy(update={"oa_observation": OAObservation(
                pending_state="legacy_unverified", revision=0,
                last_seen_at=record.source_fetched_at, last_checked_at=None,
            )})
        return record

    async def get_oa_sync_status(
        self, subject: OASyncSubject, stream: OASyncStream
    ) -> OASyncStatus:
        return self.states.get((subject.ai_user_id, stream), OASyncStatus(
            subject=subject, stream=stream, issued_generation=0, applied_generation=0,
            last_attempt_status="never", last_attempt_started_at=None,
            last_attempt_finished_at=None, last_success_at=None, last_error_code=None,
        ))

    async def begin_oa_sync(
        self, subject: OASyncSubject, stream: OASyncStream, started_at: datetime,
    ) -> OASyncTicket:
        state = await self.get_oa_sync_status(subject, stream)
        ticket = OASyncTicket(subject=subject, stream=stream,
                              generation=state.issued_generation + 1, started_at=started_at)
        self.states[(subject.ai_user_id, stream)] = OASyncStatus.model_validate({
            **state.model_dump(), "issued_generation": ticket.generation,
            "last_attempt_status": "running", "last_attempt_started_at": started_at,
            "last_attempt_finished_at": None, "last_error_code": None,
        })
        return ticket

    async def apply_oa_pending_snapshot(
        self, ticket: OASyncTicket, *, assignee_display_name: str,
        collection: OAPendingWorkSnapshotCollection, fetched_at: datetime,
    ) -> str:
        self.apply_calls += 1
        collection = OAPendingWorkSnapshotCollection.model_validate(collection.model_dump())
        state = await self.get_oa_sync_status(ticket.subject, ticket.stream)
        if (
            ticket.generation != state.issued_generation
            or ticket.generation <= state.applied_generation
        ):
            return "superseded"
        owned = [r for r in self.records.values() if r.state_authority == "external_snapshot"
                 and r.assignee_ai_user_id == ticket.subject.ai_user_id]
        if fetched_at < ticket.started_at or any(fetched_at < r.source_fetched_at for r in owned):
            raise OASyncClockInvalid()
        await self.upsert_oa_pending_workflows(
            assignee_ai_user_id=ticket.subject.ai_user_id,
            assignee_display_name=assignee_display_name,
            snapshots=collection.workflows, fetched_at=fetched_at,
        )
        refs = {item.source_ref for item in collection.workflows}
        for key, record in list(self.records.items()):
            if (
                record.state_authority == "external_snapshot"
                and record.assignee_ai_user_id == ticket.subject.ai_user_id
            ):
                self.records[key] = record.model_copy(update={"oa_observation": OAObservation(
                    pending_state="current" if record.source_ref in refs else "unconfirmed",
                    revision=ticket.generation, last_seen_at=record.source_fetched_at,
                    last_checked_at=fetched_at,
                )})
        self.states[(ticket.subject.ai_user_id, ticket.stream)] = OASyncStatus.model_validate({
            **state.model_dump(), "applied_generation": ticket.generation,
            "last_attempt_status": "succeeded", "last_attempt_finished_at": fetched_at,
            "last_success_at": fetched_at, "last_error_code": None,
        })
        return "applied"

    async def finish_oa_sync_failure(
        self, ticket: OASyncTicket, *, failure_code: OASyncFailureCode, finished_at: datetime,
    ) -> None:
        state = await self.get_oa_sync_status(ticket.subject, ticket.stream)
        if (
            ticket.generation == state.issued_generation
            and ticket.generation > state.applied_generation
        ):
            self.states[(ticket.subject.ai_user_id, ticket.stream)] = OASyncStatus.model_validate(
                {
                    **state.model_dump(),
                    "last_attempt_status": "failed",
                    "last_attempt_finished_at": finished_at,
                    "last_error_code": "clock_invalid"
                    if finished_at < ticket.started_at
                    else failure_code,
                }
            )

    async def list_with_oa_sync_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        oa_view: OAView = "active",
        limit: int = 201,
        completion: CompletionFilter | None = None,
    ) -> WorkObjectReadBatch:
        records = await self.list_for_scope(
            scope, search_term=search_term, limit=limit, oa_view=oa_view, completion=completion
        )
        if scope.principal_tenant_id == "default":
            state = await self.get_oa_sync_status(
                OASyncSubject(
                    tenant_id="default",
                    ai_user_id=scope.principal_ai_user_id,
                ),
                "pending",
            )
            view = state.to_view()
        else:
            view = OASyncStatusView(
                status="unsupported_scope",
                revision=0,
                attempt_revision=0,
                last_attempt_at=None,
                last_success_at=None,
                failure_code=None,
            )
        return WorkObjectReadBatch(records=records, oa_sync=view)

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
                oa_observation=OAObservation(
                    pending_state="legacy_unverified", revision=0,
                    last_seen_at=fetched_at, last_checked_at=None,
                ),
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
        oa_view: OAView = "all",
        completion: CompletionFilter | None = None,
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
        records = [self._project(record, scope) for record in records]
        if oa_view == "active":
            records = [
                r
                for r in records
                if r.state_authority != "external_snapshot"
                or r.oa_observation.pending_state != "unconfirmed"
            ]
        elif oa_view == "unconfirmed":
            records = [
                r
                for r in records
                if r.state_authority == "external_snapshot"
                and r.oa_observation.pending_state == "unconfirmed"
            ]
        if completion == "active":
            records = [
                r
                for r in records
                if not isinstance(r, InternalWorkObjectRecord) or r.status != "completed"
            ]
        elif completion == "completed":
            now = datetime.now(UTC)
            records = [
                r
                for r in records
                if isinstance(r, InternalWorkObjectRecord)
                and r.status == "completed"
                and r.completed_at is not None
                and now - timedelta(days=30) <= r.completed_at <= now
            ]
        return records[:limit]

    async def get_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None:
        return next(
            (
                self._project(record, scope)
                for record in self.records.values()
                if record.work_object_id == work_object_id and self._visible(record, scope)
            ),
            None,
        )

    async def get_lifecycle_event_for_scope(
        self, work_object_id, scope, *, actor_ai_user_id, idempotency_key
    ):
        if actor_ai_user_id != scope.principal_ai_user_id:
            raise LifecycleStoreError("work_object_action_forbidden")
        record = await self.get_for_scope(work_object_id, scope)
        if record is None:
            return None
        return next(
            (
                event
                for event in self.lifecycle_events
                if event.work_object_id == work_object_id
                and event.tenant_id == scope.principal_tenant_id
                and event.actor_ai_user_id == actor_ai_user_id
                and event.idempotency_key == idempotency_key
            ),
            None,
        )

    async def list_lifecycle_events_for_scope(self, work_object_id, scope, *, after_version, limit):
        record = await self.get_for_scope(work_object_id, scope)
        if record is None:
            return None
        if (
            not isinstance(record, InternalWorkObjectRecord)
            or record.source_kind != "manual_dispatch"
        ):
            raise LifecycleStoreError("work_object_lifecycle_unsupported")
        return sorted(
            (
                event
                for event in self.lifecycle_events
                if event.work_object_id == work_object_id
                and event.tenant_id == scope.principal_tenant_id
                and event.result_version > after_version
            ),
            key=lambda event: event.result_version,
        )[:limit]

    async def apply_lifecycle_command_for_scope(
        self,
        work_object_id,
        scope,
        *,
        actor,
        command,
        idempotency_key,
        request_fingerprint,
        expected_etag,
        event_id,
    ):
        record = await self.get_for_scope(work_object_id, scope)
        if record is None:
            raise LifecycleStoreError("work_object_not_found")
        if (
            not isinstance(record, InternalWorkObjectRecord)
            or record.source_kind != "manual_dispatch"
        ):
            raise LifecycleStoreError("work_object_lifecycle_unsupported")
        if (
            actor.tenant_id != scope.principal_tenant_id
            or actor.ai_user_id != scope.principal_ai_user_id
            or actor.department_id != scope.principal_department_id
            or not lifecycle_role_allowed(record, actor, command.operation)
        ):
            raise LifecycleStoreError("work_object_action_forbidden")
        if time.monotonic() > actor.valid_until:
            raise LifecycleStoreError("organization_directory_stale")
        previous = await self.get_lifecycle_event_for_scope(
            work_object_id, scope, actor_ai_user_id=actor.ai_user_id,
            idempotency_key=idempotency_key
        )
        if previous is not None:
            if previous.request_fingerprint != request_fingerprint:
                raise LifecycleStoreError("idempotency_key_reused")
            if not can_replay_lifecycle_event(record, actor, previous):
                raise LifecycleStoreError("work_object_action_forbidden")
            return LifecycleMutationResult(previous, True)
        if not lifecycle_transition_allowed(record, command.operation):
            raise LifecycleStoreError("work_object_transition_invalid")
        if (
            lifecycle_etag(_lifecycle_view(record, actor, None).model_dump(mode="json"))
            != expected_etag
        ):
            raise LifecycleStoreError("work_object_version_conflict")
        now = max(datetime.now(UTC), record.updated_at)
        status = "completed" if command.operation == "complete" else "in_progress"
        event = LifecycleEventRecord(
            event_id=event_id,
            work_object_id=work_object_id,
            tenant_id=actor.tenant_id,
            actor_ai_user_id=actor.ai_user_id,
            operation=command.operation,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            from_status=record.status,
            to_status=status,
            result_version=record.version + 1,
            occurred_at=now,
            text=command.text,
        )
        changes = dict(status=status, version=event.result_version, updated_at=now)
        if command.operation == "accept":
            changes.update(accepted_by_ai_user_id=actor.ai_user_id, accepted_at=now)
        elif command.operation == "complete":
            changes.update(completed_by_ai_user_id=actor.ai_user_id, completed_at=now)
        self.records[work_object_id] = InternalWorkObjectRecord.model_validate(
            {**record.model_dump(), **changes}
        )
        self.lifecycle_events.append(event)
        return LifecycleMutationResult(event, False)

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


def test_sync_keeps_unfiltered_completion_contract(dispatch_db, monkeypatch):
    from datetime import UTC, datetime

    db = dispatch_db
    ids = []
    for _ in range(3):
        response = db.post()
        assert response.status_code == 201
        ids.append(response.json()["items"][0]["work_object_id"])
    for object_id, days in zip(ids[1:], (1, 31), strict=True):
        db.execute(
            "UPDATE work_objects SET status='completed',version=3,"
            "created_at=CURRENT_TIMESTAMP-interval '40 days',"
            "accepted_by_ai_user_id='synthetic',accepted_at=CURRENT_TIMESTAMP-interval '39 days',"
            "completed_by_ai_user_id='synthetic',completed_at=CURRENT_TIMESTAMP-make_interval(days=>:days),"
            "updated_at=CURRENT_TIMESTAMP-make_interval(days=>:days) WHERE work_object_id=:id",
            id=object_id,
            days=days,
        )
    monkeypatch.setattr(db.service, "_clock", lambda: datetime.now(UTC))
    db.service._gateway.result = _success_result(title="Synthetic OA success")
    calls = []
    original = db.store.list_with_oa_sync_for_scope

    async def capture(*args, **kwargs):
        calls.append(kwargs.get("completion"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(db.store, "list_with_oa_sync_for_scope", capture)
    synced = db.client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert synced.status_code == 200
    assert calls[-1] is None
    assert {
        item["work_object_id"]
        for item in synced.json()["items"]
        if item["state_authority"] == "internal"
    } == set(ids)
    assert [
        item["source_title"]
        for item in synced.json()["items"]
        if item["state_authority"] == "external_snapshot"
    ] == ["Synthetic OA success"]
    active = db.client.get("/api/v1/work-objects?completion=active")
    assert active.status_code == 200 and calls[-1] == "active"
    assert {
        item["work_object_id"]
        for item in active.json()["items"]
        if item["state_authority"] == "internal"
    } == {ids[0]}
    completed = db.client.get("/api/v1/work-objects?completion=completed")
    assert completed.status_code == 200 and calls[-1] == "completed"
    assert [item["work_object_id"] for item in completed.json()["items"]] == [ids[1]]
    # A view expiring during projection must preserve the filter on its self-scope retry.
    monkeypatch.setattr(db.service, "_read_expired", lambda _fresh: True)
    for completion, expected in (("active", {ids[0]}), ("completed", {ids[1]})):
        calls.clear()
        fallback = db.client.get("/api/v1/work-objects", params={"completion": completion})
        assert fallback.status_code == 200
        assert calls == [completion, completion]
        assert {item["work_object_id"] for item in fallback.json()["items"]
                if item["state_authority"] == "internal"} == expected


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
        oa_observation=OAObservation(
            pending_state="legacy_unverified", revision=0,
            last_seen_at=NOW - timedelta(minutes=index), last_checked_at=None,
        ),
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
            session_revocations=MemorySessionRevocations(),
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
            "accepted_at": None,
            "completed_at": None,
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


def _sentinel_list(count: int = 201) -> tuple[list[str], list[WorkObjectRecord]]:
    ids = [f"id-{count - 1 - index:03d}" for index in range(count)]
    records = [
        _record(source_ref=f"oa-todo-{index}", index=index).model_copy(
            update={"work_object_id": item_id}
        )
        for index, item_id in enumerate(ids, start=1)
    ]
    return ids, records


def test_list_returns_one_bounded_batch_with_explicit_overflow() -> None:
    sentinel_ids, records = _sentinel_list()
    store = MemoryWorkObjectStore(records)
    client = _client(store, RecordingGateway())

    response = client.get("/api/v1/work-objects")

    assert response.status_code == 200
    assert response.json()["limit"] == 200
    assert response.json()["limit_exceeded"] is True
    assert [item["work_object_id"] for item in response.json()["items"]] == sentinel_ids[:200]
    assert store.list_calls == [
        {
            "assignee_ai_user_id": "user-a",
            "search_term": None,
            "limit": 201,
        }
    ]


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
    sentinel_ids, records = _sentinel_list()
    store = MemoryWorkObjectStore(records)
    client = _client(store, RecordingGateway())

    response = client.get("/api/v1/work-objects", params={"q": "pending approval"})

    assert response.status_code == 200
    assert response.json()["limit"] == 200
    assert response.json()["limit_exceeded"] is True
    assert [item["work_object_id"] for item in response.json()["items"]] == sentinel_ids[:200]
    assert store.list_calls == [
        {
            "assignee_ai_user_id": "user-a",
            "search_term": "pending approval",
            "limit": 201,
        }
    ]


def test_list_q_and_sync_preserve_ordered_store_prefix() -> None:
    sentinel_ids, records = _sentinel_list()
    list_store = MemoryWorkObjectStore(records)
    list_client = _client(list_store, RecordingGateway())
    listed = list_client.get("/api/v1/work-objects")
    assert list_store.list_calls == [
        {"assignee_ai_user_id": "user-a", "search_term": None, "limit": 201}
    ]
    assert [item["work_object_id"] for item in listed.json()["items"]] == sentinel_ids[:200]
    assert listed.json()["limit_exceeded"] is True

    search_store = MemoryWorkObjectStore(records)
    search_client = _client(search_store, RecordingGateway())
    searched = search_client.get("/api/v1/work-objects", params={"q": "pending approval"})
    assert search_store.list_calls == [
        {
            "assignee_ai_user_id": "user-a",
            "search_term": "pending approval",
            "limit": 201,
        }
    ]
    assert [item["work_object_id"] for item in searched.json()["items"]] == sentinel_ids[:200]
    assert searched.json()["limit_exceeded"] is True

    existing = [
        _record(source_ref=f"existing-{index}", index=index).model_copy(
            update={"work_object_id": f"keep-{index}"}
        )
        for index in (1, 2, 3)
    ]
    sync_store = MemoryWorkObjectStore(existing)
    sync_client = _client(sync_store, RecordingGateway(_success_result(title="Synced title")))
    synced = sync_client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    expected_sync = ["keep-1", "keep-2", "keep-3", "work-user-a-oa-todo-1"]
    assert sync_store.list_calls[-1] == {
        "assignee_ai_user_id": "user-a",
        "search_term": None,
        "limit": 201,
    }
    assert [item["work_object_id"] for item in synced.json()["items"]] == expected_sync
    assert synced.json()["limit_exceeded"] is False
    assert synced.json()["items"][-1]["source_title"] == "Synced title"


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
        async def read_view(self):
            from app.ports.organization_directory import OrganizationDirectoryReadView
            now = datetime.now(UTC)
            lookups.append("synthetic-directory-user")
            return OrganizationDirectoryReadView(
                snapshot_version=len(lookups),
                source_fetched_at=now,
                last_success_at=now,
                observed_at=now,
                departments=tuple(
                    OrganizationDepartment(
                        department_id=member.department_id, display_name="Synthetic"
                    )
                    for member in memberships
                ),
                memberships=tuple(memberships),
            )

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
                    principal_tenant_id="default",
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
        async def read_view(self):
            raise RuntimeError("synthetic-private-join-key")

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


def test_department_and_initiator_scope_reaches_real_store(dispatch_db, monkeypatch) -> None:
    from app.ports import work_object_scope as policy

    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    from tests.api.test_work_object_dispatch import (
        assert_created,
        cross_department_body,
        insert_synthetic_row,
        manual_row,
        request_body,
    )

    db = dispatch_db
    sent = assert_created(db, db.post(cross_department_body()))
    db.actor("other-head", "office-c", "75")
    local_response = db.post(
        request_body(targets=[{"kind": "department", "department_id": "office-a"}])
    )
    assert local_response.status_code == 201
    local_id = local_response.json()["items"][0]["work_object_id"]
    unrelated = assert_created(db, db.post(cross_department_body()))
    insert_synthetic_row(db, manual_row(
        db, work_object_id="synthetic-other-tenant", tenant_id="synthetic-other",
        owner_department_id="office-a", initiator_ai_user_id="ai-sender",
    ))
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset())
    db.actor("sender", "office-a", "75")
    own = assert_created(db, db.post())
    denied = db.post(cross_department_body())
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "cross_department_dispatch_denied"
    db.actor("sender", "office-a", None)
    visible = db.client.get("/api/v1/work-objects")
    assert visible.status_code == 200
    assert {item["work_object_id"] for item in visible.json()["items"]} == {
        sent["work_object_id"],
        local_id,
        own["work_object_id"],
    }
    for item_id in (sent["work_object_id"], local_id):
        assert db.client.get("/api/v1/work-objects/" + item_id).status_code == 200
    assert db.client.get("/api/v1/work-objects/" + unrelated["work_object_id"]).status_code == 404
    assert db.client.get("/api/v1/work-objects/synthetic-other-tenant").status_code == 404


@pytest.mark.parametrize("missing", [False, True])
def test_missing_and_stale_directory_preserve_self_reads(dispatch_db, monkeypatch, missing):
    from app.ports import work_object_scope as policy

    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    from tests.api.test_work_object_dispatch import assert_created, expire_directory, request_body
    db = dispatch_db
    own = assert_created(db, db.post())["work_object_id"]
    db.actor("synthetic-other-sender", "office-c", "75")
    result = db.post(request_body(targets=[{"kind": "department", "department_id": "office-a"}]))
    assert result.status_code == 201
    departmental = result.json()["items"][0]["work_object_id"]
    db.actor("sender", "office-a", None)
    assert {
        item["work_object_id"] for item in db.client.get("/api/v1/work-objects").json()["items"]
    } == {own, departmental}
    if missing:
        db.execute(
            "UPDATE organization_directory_sync_state SET snapshot_version=0,"
            "source_fetched_at=NULL,"
            "last_success_at=NULL,last_attempt_started_at=NULL,last_attempt_finished_at=NULL,"
            "last_attempt_status='never',last_error_code=NULL"
        )
    else:
        expire_directory(db)
    for suffix in ("", "?q=Synthetic"):
        response = db.client.get("/api/v1/work-objects"+suffix)
        assert response.status_code == 200
        assert [item["work_object_id"] for item in response.json()["items"]] == [own]
    assert db.client.get("/api/v1/work-objects/"+own).status_code == 200
    assert db.client.get("/api/v1/work-objects/"+departmental).status_code == 404
    from tests.auth_fakes import TEST_CSRF_HEADERS
    db.service._gateway = RecordingGateway(_success_result())
    synced = db.client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert synced.status_code == 200
    assert any(item["source_ref"] == "oa-todo-1" for item in synced.json()["items"])
    assert own in {item["work_object_id"] for item in synced.json()["items"]}
    assert departmental not in {item["work_object_id"] for item in synced.json()["items"]}


def test_expiry_before_read_response_requeries_self_scope(dispatch_db, monkeypatch):
    from app.ports import work_object_scope as policy

    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    from tests.api.test_work_object_dispatch import assert_created, request_body
    db = dispatch_db
    own = assert_created(db, db.post())["work_object_id"]
    db.actor("synthetic-other-sender", "office-c", "75")
    result = db.post(request_body(targets=[{"kind": "department", "department_id": "office-a"}]))
    assert result.status_code == 201
    db.actor("sender", "office-a", None)
    ticks = [0.0]
    db.service._monotonic = lambda: ticks[0]
    calls = []
    original = db.store.list_with_oa_sync_for_scope
    async def delayed(scope, **kwargs):
        calls.append((scope, kwargs))
        records = await original(scope, **kwargs)
        ticks[0] = 172801.0
        return records
    monkeypatch.setattr(db.store, "list_with_oa_sync_for_scope", delayed)
    response = db.client.get("/api/v1/work-objects?q=Synthetic")
    assert response.status_code == 200
    assert [item["work_object_id"] for item in response.json()["items"]] == [own]
    assert [scope.principal_department_id for scope, _ in calls] == ["office-a", None]
    assert calls[0][1] == calls[1][1]
    original_get = db.store.get_for_scope
    detail_calls = []
    async def delayed_get(identity, scope):
        detail_calls.append(scope.principal_department_id)
        record = await original_get(identity, scope)
        ticks[0] = 172801.0
        return record
    monkeypatch.setattr(db.store, "get_for_scope", delayed_get)
    ticks[0] = 0.0
    assert db.client.get("/api/v1/work-objects/"+own).status_code == 200
    assert detail_calls == ["office-a", None]
    ticks[0] = 0.0
    detail_calls.clear()
    departmental = result.json()["items"][0]["work_object_id"]
    assert db.client.get("/api/v1/work-objects/"+departmental).status_code == 404
    assert detail_calls == ["office-a", None]


def test_memory_store_keeps_two_personal_dispatches_and_oa_upserts_distinct(
    dispatch_db, monkeypatch
) -> None:
    from app.ports import work_object_scope as policy

    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
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
        principal_tenant_id="default",
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
            tenant_id="default",
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
        principal_tenant_id="default",
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

    async def failed():
        raise RuntimeError("SYNTHETIC-DIRECTORY-FAILURE")

    monkeypatch.setattr(db.directory, "read_view", failed)
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
    apply_failures = []
    if failure == "local-db":
        db.service._gateway = RecordingGateway(_success_result())

        async def fail(ticket, **_kwargs):
            apply_failures.append(ticket)
            raise RuntimeError("synthetic database failure")

        monkeypatch.setattr(db.store, "apply_oa_pending_snapshot", fail)
    else:
        db.service._gateway = RecordingGateway(
            ExecutionResult(status="failed", trace_id="synthetic-trace", error_code=failure)
        )
    with pytest.raises(BackgroundWorkObjectSyncError) as error:
        run(db.service.sync_for_background(db.tokens.principal))
    assert error.value.authentication_denied is expected_auth
    assert error.value.failure_code == expected_code
    assert db.counts() == (0, 0)
    with db.sql.connect() as connection:
        assert connection.execute(text("SELECT * FROM oa_work_pending_observations")).all() == []
        state = connection.execute(text("SELECT * FROM oa_work_sync_state")).mappings().all()
    if failure == "local-db":
        assert len(apply_failures) == 1
        assert len(state) == 1
        assert state[0]["last_attempt_status"] == "failed"
        assert state[0]["last_error_code"] == "storage_unavailable"
        assert state[0]["applied_generation"] == 0


def _default_client(store, gateway) -> TestClient:
    tokens = StaticSessionTokens(roles=("user",))
    tokens.principal = Principal(ai_user_id="user-a", display_name="User A", roles=("user",),
                                 org_ctx=PrincipalOrgContext(tenant_id="default"))
    service = WorkObjectService(store=store, gateway=gateway,
                                capability_registry=StaticCapabilityRegistry(), clock=lambda: NOW)
    client = TestClient(
        create_app(
            work_object_service=service,
            session_revocations=MemorySessionRevocations(),
            session_tokens=tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
        backend_options={"loop_factory": make_event_loop},
    )
    client.cookies.update(auth_cookies())
    return client


def _empty_success_result() -> ExecutionResult:
    return ExecutionResult(status="completed", trace_id="trace-empty", data={
        "workflows": [], "returned_count": 0, "authoritative_count": 0, "is_complete": True,
    })


def test_complete_empty_snapshot_history_search_default_and_reappearance() -> None:
    original = _record()
    other = _record(owner="user-b")
    store = MemoryWorkObjectStore([original, other])
    gateway = RecordingGateway(_empty_success_result())
    client = _default_client(store, gateway)
    result = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert result.status_code == 200
    assert result.json()["items"] == []
    assert result.json()["oa_sync"]["revision"] == 1
    assert result.json()["oa_sync"]["last_success_at"] == NOW.isoformat().replace("+00:00", "Z")
    history = client.get("/api/v1/work-objects", params={"oa_view": "unconfirmed"})
    assert [row["work_object_id"] for row in history.json()["items"]] == [original.work_object_id]
    assert history.json()["items"][0]["oa_observation"]["pending_state"] == "unconfirmed"
    assert history.json()["items"][0]["handling_action"] == "view_only"
    assert history.json()["items"][0]["handling_capability_id"] is None
    assert store.records[other.work_object_id] == other
    default_search = client.get("/api/v1/work-objects", params={"q": original.source_ref})
    all_search = client.get(
        "/api/v1/work-objects", params={"q": original.source_ref, "oa_view": "all"}
    )
    assert default_search.json()["items"] == []
    assert [row["work_object_id"] for row in all_search.json()["items"]] == [
        original.work_object_id
    ]
    detail = client.get(f"/api/v1/work-objects/{original.work_object_id}")
    assert detail.json()["source_fetched_at"] == original.source_fetched_at.isoformat().replace(
        "+00:00", "Z"
    )
    marked = client.patch(f"/api/v1/work-objects/{original.work_object_id}/handling-mark",
                          json={"mark": "handled_elsewhere"}, headers=TEST_CSRF_HEADERS)
    assert marked.status_code == 200
    assert marked.json()["handling_action"] == "view_only"
    assert marked.json()["oa_observation"]["pending_state"] == "unconfirmed"
    gateway.result = _success_result()
    returned = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert returned.status_code == 200
    assert returned.json()["items"][0]["work_object_id"] == original.work_object_id
    assert returned.json()["items"][0]["handling_mark"] == "handled_elsewhere"
    assert returned.json()["items"][0]["oa_observation"]["pending_state"] == "current"
    assert store.apply_calls == 2
    assert "cache-control" not in result.headers
    assert "cache-control" not in detail.headers


@pytest.mark.parametrize("changes", [
    {"is_complete": False}, {"returned_count": 2}, {"authoritative_count": 2},
    {"workflows": [{"todo_id": "bad"}]},
])
def test_partial_and_malformed_batches_preserve_facts_and_report_diagnostic(changes) -> None:
    original = _record()
    store = MemoryWorkObjectStore([original])
    good = _success_result()
    gateway = RecordingGateway(good.model_copy(update={"data": {**good.data, **changes}}))
    client = _default_client(store, gateway)
    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "work_object_sync_invalid"
    assert store.records == {original.work_object_id: original}
    assert store.apply_calls == 0
    sync = client.get("/api/v1/work-objects").json()["oa_sync"]
    assert sync == {"status": "failed", "revision": 0, "attempt_revision": 1,
                    "last_attempt_at": NOW.isoformat().replace("+00:00", "Z"),
                    "last_success_at": None, "failure_code": "invalid_response"}


def test_commit_ack_loss_reports_unknown_without_reapplying_or_recording_failure() -> None:
    class LostAckStore(MemoryWorkObjectStore):
        async def apply_oa_pending_snapshot(self, *args, **kwargs):
            await super().apply_oa_pending_snapshot(*args, **kwargs)
            raise OASyncOutcomeUnknown()

        async def finish_oa_sync_failure(self, *args, **kwargs):
            raise AssertionError("unknown outcome must not record failure")

    store = LostAckStore([_record()])
    client = _default_client(store, RecordingGateway(_empty_success_result()))
    result = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "oa_sync_outcome_unknown"
    persisted = client.get("/api/v1/work-objects").json()
    assert persisted["oa_sync"]["status"] == "succeeded"
    assert persisted["items"] == []
    assert store.apply_calls == 1


def test_begin_failure_prevents_gateway_and_local_errors_are_non_counted() -> None:
    class BeginFailure(MemoryWorkObjectStore):
        async def begin_oa_sync(self, *args, **kwargs):
            raise RuntimeError("synthetic begin failure")

    gateway = RecordingGateway()
    store = BeginFailure()
    client = _default_client(store, gateway)
    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "work_object_sync_failed"
    assert gateway.calls == []
    assert store.records == {}


def test_background_default_reconciliation_does_not_read_directory_or_list() -> None:
    class NoReads(MemoryWorkObjectStore):
        async def list_with_oa_sync_for_scope(self, *args, **kwargs):
            raise AssertionError("background must not list")

    store = NoReads([_record()])
    service = WorkObjectService(store=store, gateway=RecordingGateway(_empty_success_result()),
                                capability_registry=StaticCapabilityRegistry(), clock=lambda: NOW)
    principal = Principal(ai_user_id="user-a", display_name="A", roles=("user",),
                           org_ctx=PrincipalOrgContext(tenant_id="default"))
    asyncio.run(service.sync_for_background(principal))
    assert store.records[_record().work_object_id].oa_observation.pending_state == "unconfirmed"
    assert store.states[("user-a", "pending")].last_attempt_status == "succeeded"
    assert store.list_calls == []


def test_online_sync_reaches_real_reconciliation_store(dispatch_db):
    db = dispatch_db
    # Use the real router and PostgreSQL store; only upstream OA is synthetic.
    gateway = RecordingGateway()
    client = _default_client(db.store, gateway)
    first = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert first.status_code == 200
    work_id = first.json()["items"][0]["work_object_id"]
    gateway.result = _empty_success_result()
    second = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert second.status_code == 200
    assert second.json()["items"] == []
    assert second.json()["oa_sync"]["revision"] == 2
    history = client.get("/api/v1/work-objects", params={"oa_view": "unconfirmed"})
    assert [r["work_object_id"] for r in history.json()["items"]] == [work_id]
    with db.sql.connect() as connection:
        rows = connection.execute(
            text("SELECT pending_state, revision FROM oa_work_pending_observations")
        ).all()
    assert rows == [("unconfirmed", 2)]
    assert client.get(f"/api/v1/work-objects/{work_id}").json()["handling_action"] == "view_only"


@pytest.mark.parametrize("error_code", ["identity_expired", "adapter_timeout"])
def test_online_diagnostic_write_failure_remains_fixed_503(error_code):
    attempts = []

    class DiagnosticFailure(MemoryWorkObjectStore):
        async def finish_oa_sync_failure(self, ticket, **kwargs):
            attempts.append((ticket, kwargs))
            raise RuntimeError("synthetic diagnostic failure")

    original = _record()
    store = DiagnosticFailure([original])
    gateway = RecordingGateway(ExecutionResult(
        status="failed", trace_id="synthetic-trace", error_code=error_code,
    ))
    client = _default_client(store, gateway)
    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "work_object_sync_failed",
        "message": "Work Object synchronization failed; stored data is unchanged.",
    }
    assert len(gateway.calls) == len(attempts) == 1
    assert attempts[0][1]["failure_code"] == (
        "reauthentication_required" if error_code == "identity_expired" else "upstream_unavailable"
    )
    assert store.records == {original.work_object_id: original}
    assert store.apply_calls == 0


def test_http_real_commit_ack_loss_reloads_published_empty_snapshot(dispatch_db, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    db = dispatch_db
    gateway = RecordingGateway()
    client = _default_client(db.store, gateway)
    first = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert first.status_code == 200
    work_id = first.json()["items"][0]["work_object_id"]
    commits = []
    diagnostic_calls = []

    class LostApplyAcknowledgement(AsyncSession):
        async def commit(self):
            await super().commit()
            commits.append(True)
            if len(commits) == 2:
                raise RuntimeError("synthetic lost apply acknowledgement")

    original_finish = db.store.finish_oa_sync_failure

    async def record_finish(*args, **kwargs):
        diagnostic_calls.append(True)
        return await original_finish(*args, **kwargs)

    monkeypatch.setattr(db.store, "finish_oa_sync_failure", record_finish)
    monkeypatch.setattr(db.store, "_session_factory", async_sessionmaker(
        db.engine, class_=LostApplyAcknowledgement, expire_on_commit=False,
    ))
    gateway.result = _empty_success_result()
    response = client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "oa_sync_outcome_unknown"
    assert len(commits) == 2  # begin then apply; no retry or diagnostic commit
    assert len(gateway.calls) == 2  # seed then empty batch
    assert diagnostic_calls == []
    persisted = client.get("/api/v1/work-objects")
    assert persisted.status_code == 200
    assert persisted.json()["items"] == []
    assert persisted.json()["oa_sync"]["status"] == "succeeded"
    assert persisted.json()["oa_sync"]["revision"] == 2
    assert persisted.json()["oa_sync"]["failure_code"] is None
    history = client.get("/api/v1/work-objects", params={"oa_view": "unconfirmed"})
    assert history.status_code == 200
    assert [r["work_object_id"] for r in history.json()["items"]] == [work_id]
    assert history.json()["items"][0]["oa_observation"]["revision"] == 2
    with db.sql.connect() as connection:
        assert connection.execute(text(
            "SELECT last_attempt_status FROM oa_work_sync_state"
        )).scalar_one() == "succeeded"
        assert connection.execute(text(
            "SELECT pending_state FROM oa_work_pending_observations"
        )).scalar_one() == "unconfirmed"


def test_real_commit_ack_loss_preserves_published_state_and_late_failure(dispatch_db):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tests.api.test_work_object_dispatch import run

    db = dispatch_db

    class LostAcknowledgement(AsyncSession):
        async def commit(self):
            await super().commit()
            raise RuntimeError("synthetic lost acknowledgement")

    async def exercise():
        subject = OASyncSubject(tenant_id="default", ai_user_id="user-a")
        ticket = await db.store.begin_oa_sync(subject, "pending", NOW)
        uncertain = PostgreSQLWorkObjectStore(
            async_sessionmaker(db.engine, class_=LostAcknowledgement)
        )
        with pytest.raises(OASyncOutcomeUnknown):
            await uncertain.apply_oa_pending_snapshot(ticket, assignee_display_name="Synthetic",
                collection=OAPendingWorkSnapshotCollection(workflows=[], returned_count=0,
                    authoritative_count=0, is_complete=True), fetched_at=NOW)
        await db.store.finish_oa_sync_failure(
            ticket, failure_code="storage_unavailable", finished_at=NOW
        )
        state = await db.store.get_oa_sync_status(subject, "pending")
        assert state.last_attempt_status == "succeeded"
        assert state.last_error_code is None
        assert state.applied_generation == 1

    run(exercise())


def test_nondefault_legacy_sync_never_touches_default_observation_state(dispatch_db):
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    principal = Principal(ai_user_id="user-a", display_name="Synthetic", roles=(),
                          org_ctx=PrincipalOrgContext(tenant_id="default"))
    service = WorkObjectService(store=db.store, gateway=RecordingGateway(),
                                capability_registry=StaticCapabilityRegistry(), clock=lambda: NOW)

    def side_tables():
        with db.sql.connect() as connection:
            return [list(connection.execute(text("SELECT * FROM " + table)).mappings())
                    for table in ("oa_work_sync_state", "oa_work_pending_observations")]

    run(service.sync_for_background(principal))
    before = side_tables()
    other = principal.model_copy(update={"org_ctx": PrincipalOrgContext(tenant_id="other")})
    response = run(service.sync_for_principal(other))
    assert response.oa_sync.status == "unsupported_scope"
    assert response.oa_sync.revision == 0
    assert response.items[0].oa_observation.pending_state == "legacy_unverified"
    assert side_tables() == before
