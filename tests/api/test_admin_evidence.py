"""API acceptance tests for bounded Admin Lite Task and Binding evidence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.admin.registry import AdminRegistryService
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.main import create_app
from app.ports.capability_gateway import RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityCheckResult,
    TargetSystem,
)
from app.ports.task_store import (
    TASK_STORE_QUERY_LIMIT,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
)
from app.ports.trace import TRACE_QUERY_LIMIT, TraceEvent, TracePersistedEvent


class RegistrySentinel:
    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        raise AssertionError("Task and Binding routes must not access Registry")

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        raise AssertionError("Task and Binding routes must not access Registry")

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        raise AssertionError("Task and Binding routes must not access Registry")

    async def update(
        self,
        capability_id: str,
        patch: dict[str, Any],
    ) -> CapabilitySpec:
        raise AssertionError("Task and Binding routes must not access Registry")

    async def disable(self, capability_id: str) -> CapabilitySpec:
        raise AssertionError("Task and Binding routes must not access Registry")


class RecordingTaskStore:
    def __init__(
        self,
        tasks: list[TaskRecord] | None = None,
        events: list[TaskEventRecord] | None = None,
    ) -> None:
        self.tasks = tasks or []
        self.events = events or []
        self.calls: list[tuple[str, object]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        raise AssertionError("Admin evidence routes are read-only")

    async def get_task(self, task_id: str) -> TaskRecord | None:
        self.calls.append(("get_task", task_id))
        return next((task for task in self.tasks if task.task_id == task_id), None)

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_code: str | None = None,
    ) -> TaskRecord:
        raise AssertionError("Admin evidence routes are read-only")

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None:
        raise AssertionError("Admin evidence routes are read-only")

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        self.calls.append(("list_tasks", (session_id, ai_user_id)))
        return [
            task
            for task in self.tasks
            if (session_id is None or task.session_id == session_id)
            and (ai_user_id is None or task.ai_user_id == ai_user_id)
        ]

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        self.calls.append(("list_events", task_id))
        return [event for event in self.events if event.task_id == task_id]


class RecordingIdentityMapping:
    def __init__(self, mappings: list[IdentityCheckResult] | None = None) -> None:
        self.mappings = mappings or []
        self.calls: list[tuple[object, ...]] = []

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        raise AssertionError("Admin evidence routes only list mappings")

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> IdentityCheckResult | None:
        raise AssertionError("Admin evidence routes only list mappings")

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> list[IdentityCheckResult]:
        self.calls.append(
            (
                ai_user_id,
                target_system,
                binding_scope,
                account_set_id,
                device_domain_id,
            )
        )
        return self.mappings


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record_event(self, event: TraceEvent) -> None:
        self.events.append(event)


class RecordingTraceQuery:
    def __init__(self, events: list[TracePersistedEvent] | None = None) -> None:
        self.events = events or []
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def list_events_by_trace(
        self,
        trace_id: str,
        **filters: object,
    ) -> list[TracePersistedEvent]:
        self.calls.append(("trace", trace_id, filters))
        return self.events

    async def list_events_by_task(
        self,
        task_id: str,
        **filters: object,
    ) -> list[TracePersistedEvent]:
        self.calls.append(("task", task_id, filters))
        return self.events

    async def list_events_by_session(
        self,
        session_id: str,
        **filters: object,
    ) -> list[TracePersistedEvent]:
        self.calls.append(("session", session_id, filters))
        return self.events


class AdapterSentinel:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self) -> None:
        self.calls += 1


class GatewaySentinel:
    def __init__(self, adapter: AdapterSentinel) -> None:
        self.adapter = adapter
        self.calls = 0

    async def execute_capability(self) -> None:
        self.calls += 1
        await self.adapter.execute()


class WorkflowSentinel:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self) -> None:
        self.calls += 1


class RuntimeSentinel:
    def __init__(self, gateway: GatewaySentinel, workflow: WorkflowSentinel) -> None:
        self.gateway = gateway
        self.workflow = workflow
        self.calls = 0

    async def handle_user_message(self, **_: Any) -> Any:
        self.calls += 1
        await self.gateway.execute_capability()
        await self.workflow.run()
        raise AssertionError("Admin route reached the Runtime execution chain")


ADMIN_HEADERS = {"X-EternalAI-Roles": "admin"}
ROLE_DENIED_DETAIL = {
    "detail": {
        "code": "role_not_allowed",
        "message": "Management role is required.",
    }
}


def _task(index: int, *, ai_user_id: str = "user-1") -> TaskRecord:
    return TaskRecord(
        task_id=f"task-{index:03d}",
        session_id="session-1",
        ai_user_id=ai_user_id,
        status="failed" if index % 2 else "completed",
        trace_id=f"private-trace-{index}",
        capability_id="oa.leave.apply",
        error_code="adapter_timeout" if index % 2 else None,
    )


def _event(index: int) -> TaskEventRecord:
    payload: dict[str, Any] = {
        "capability_id": "oa.leave.apply",
        "selection_rule": "exact_id",
    }
    if index == 0:
        payload.update(
            {
                "password": "must-not-leak",
                "access_token": "must-not-leak",
                "future_payload": {"secret": "must-not-leak"},
            }
        )
    return TaskEventRecord(
        event_id=f"event-{index:03d}",
        task_id="task-000",
        event_type="capability_selected",
        timestamp=datetime(2026, 7, 23, tzinfo=timezone.utc)
        + timedelta(seconds=index),
        payload=payload,
    )


def _binding(index: int) -> IdentityCheckResult:
    return IdentityCheckResult(
        binding_id=f"binding-{index:03d}",
        target_system="oa",
        execution_identity="user_delegated",
        bind_status="active",
        binding_scope="self",
        account_set_id=f"account-{index:03d}",
        reason_code=None,
    )


def _trace_event(index: int) -> TracePersistedEvent:
    return TracePersistedEvent(
        event_id=f"trace-event-{index:03d}",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        event_type="adapter_error",
        status="failed",
        capability_id="oa.leave.apply",
        error_code="adapter_timeout",
        attributes={
            "safe": "visible",
            "authorization": "Bearer MUST-NOT-LEAK-ADMIN-TRACE",
            "token": "MUST-NOT-LEAK-DIRECT-TOKEN",
            "nested": {"access_token": "MUST-NOT-LEAK-ADMIN-TRACE"},
            "dsn": "postgresql+psycopg://alice:MUST-NOT-LEAK-URI@db/app",
            "headers": {
                "X-Api-Key": "MUST-NOT-LEAK-API-KEY",
                "X-CSRF-Token": "MUST-NOT-LEAK-CSRF-TOKEN",
            },
        },
        created_at=datetime(2026, 7, 23, tzinfo=timezone.utc)
        + timedelta(seconds=index),
    )


def _client(
    task_store: RecordingTaskStore,
    identity_mapping: RecordingIdentityMapping,
    trace: RecordingTrace,
    runtime: RuntimeSentinel | None = None,
    trace_query: RecordingTraceQuery | None = None,
) -> TestClient:
    service = AdminRegistryService(
        capability_registry=RegistrySentinel(),
        task_store=task_store,
        identity_mapping=identity_mapping,
        policy_guard=MinimalPolicyGuard(
            admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS
        ),
        trace_port=trace,
        trace_query=trace_query or RecordingTraceQuery(),
    )
    return TestClient(create_app(runtime=runtime, admin_registry_service=service))


@pytest.mark.parametrize("requested_limit", ["-1", "999999999"])
def test_task_list_is_whitelisted_and_fixed_at_100_regardless_of_limit(
    requested_limit: str,
) -> None:
    task_store = RecordingTaskStore([_task(index) for index in range(101)])
    trace = RecordingTrace()
    client = _client(task_store, RecordingIdentityMapping(), trace)

    response = client.get(
        f"/api/v1/admin/tasks?ai_user_id=user-1&limit={requested_limit}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == TASK_STORE_QUERY_LIMIT == 100
    assert set(items[0]) == {
        "task_id",
        "session_id",
        "ai_user_id",
        "status",
        "capability_id",
        "error_code",
    }
    assert "private-trace" not in str(items)
    assert task_store.calls == [("list_tasks", (None, "user-1"))]
    assert trace.events[-1].attributes["action"] == "tasks_list"
    assert trace.events[-1].attributes["result_count"] == 100


def test_task_events_drop_unknown_sensitive_payload_and_are_fixed_at_100() -> None:
    task_store = RecordingTaskStore(
        tasks=[_task(0)],
        events=[_event(index) for index in range(101)],
    )
    trace = RecordingTrace()
    client = _client(task_store, RecordingIdentityMapping(), trace)

    response = client.get(
        "/api/v1/admin/tasks/task-000/events?limit=999999999",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == TASK_STORE_QUERY_LIMIT == 100
    assert set(items[0]) == {"event_id", "task_id", "event_type", "timestamp", "evidence"}
    assert items[0]["evidence"] == {
        "capability_id": "oa.leave.apply",
        "selection_rule": "exact_id",
    }
    serialized = str(response.json())
    assert "password" not in serialized
    assert "access_token" not in serialized
    assert "future_payload" not in serialized
    assert "must-not-leak" not in serialized
    assert task_store.calls == [
        ("get_task", "task-000"),
        ("list_events", "task-000"),
    ]
    assert trace.events[-1].attributes["action"] == "task_events_list"


def test_bindings_require_a_user_forward_filters_and_are_fixed_at_100() -> None:
    identity_mapping = RecordingIdentityMapping(
        [_binding(index) for index in range(101)]
    )
    trace = RecordingTrace()
    client = _client(RecordingTaskStore(), identity_mapping, trace)

    response = client.get(
        "/api/v1/admin/bindings"
        "?ai_user_id=user-1&target_system=oa&binding_scope=self"
        "&account_set_id=account-set&device_domain_id=device-domain"
        "&limit=-1",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_user_id"] == "user-1"
    assert len(body["items"]) == TASK_STORE_QUERY_LIMIT == 100
    assert set(body["items"][0]) == {
        "binding_id",
        "target_system",
        "execution_identity",
        "bind_status",
        "binding_scope",
        "account_set_id",
        "device_domain_id",
        "reason_code",
    }
    assert identity_mapping.calls == [
        ("user-1", "oa", "self", "account-set", "device-domain")
    ]
    assert trace.events[-1].attributes["action"] == "bindings_list"


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/admin/tasks?ai_user_id=user-1",
        "/api/v1/admin/tasks?ai_user_id=missing-user",
        "/api/v1/admin/tasks/task-000/events",
        "/api/v1/admin/tasks/missing-task/events",
        "/api/v1/admin/bindings?ai_user_id=user-1",
        "/api/v1/admin/bindings?ai_user_id=missing-user",
        "/api/v1/admin/bindings",
        "/api/v1/admin/bindings?ai_user_id=",
        "/api/v1/admin/bindings?ai_user_id=user-1&target_system=unsupported",
    ],
)
def test_each_evidence_action_denies_before_resource_access(url: str) -> None:
    task_store = RecordingTaskStore([_task(0)], [_event(0)])
    identity_mapping = RecordingIdentityMapping([_binding(0)])
    trace = RecordingTrace()
    client = _client(task_store, identity_mapping, trace)

    response = client.get(url)

    assert response.status_code == 403
    assert response.json() == ROLE_DENIED_DETAIL
    assert task_store.calls == []
    assert identity_mapping.calls == []
    assert len(trace.events) == 1
    assert trace.events[0].status == "blocked"
    assert trace.events[0].attributes["role_claim_authenticated"] is False


@pytest.mark.parametrize(
    ("query", "reason_code"),
    [
        ("", "ai_user_id_required"),
        ("?ai_user_id=", "ai_user_id_required"),
        ("?ai_user_id=user-1&target_system=unsupported", "target_system_invalid"),
    ],
)
def test_authorized_invalid_binding_query_is_checked_after_role_guard(
    query: str,
    reason_code: str,
) -> None:
    identity_mapping = RecordingIdentityMapping([_binding(0)])
    trace = RecordingTrace()
    client = _client(RecordingTaskStore(), identity_mapping, trace)

    response = client.get(
        f"/api/v1/admin/bindings{query}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "binding_query_invalid",
            "message": "Binding query parameters are invalid.",
        }
    }
    assert identity_mapping.calls == []
    assert len(trace.events) == 1
    assert trace.events[0].status == "failed"
    assert trace.events[0].attributes["action"] == "bindings_list"
    assert trace.events[0].attributes["reason_code"] == reason_code


def test_authorized_task_events_report_not_found_without_listing_events() -> None:
    task_store = RecordingTaskStore()
    trace = RecordingTrace()
    client = _client(task_store, RecordingIdentityMapping(), trace)

    response = client.get(
        "/api/v1/admin/tasks/missing-task/events",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "task_not_found", "message": "Task was not found."}
    }
    assert task_store.calls == [("get_task", "missing-task")]
    assert trace.events[-1].status == "failed"


def test_authorized_task_list_requires_a_bounded_filter() -> None:
    task_store = RecordingTaskStore([_task(0)])
    trace = RecordingTrace()
    client = _client(task_store, RecordingIdentityMapping(), trace)

    response = client.get("/api/v1/admin/tasks", headers=ADMIN_HEADERS)

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "task_filter_required",
            "message": "session_id or ai_user_id is required.",
        }
    }
    assert task_store.calls == []
    assert trace.events[-1].status == "failed"


def test_trace_list_is_bounded_whitelisted_and_redacted_on_read() -> None:
    query = RecordingTraceQuery(
        [_trace_event(index) for index in range(TRACE_QUERY_LIMIT + 1)]
    )
    trace = RecordingTrace()
    client = _client(
        RecordingTaskStore(),
        RecordingIdentityMapping(),
        trace,
        trace_query=query,
    )

    response = client.get(
        "/api/v1/admin/traces"
        "?trace_id=trace-1&task_id=task-1&session_id=session-1&limit=999999",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == TRACE_QUERY_LIMIT == 100
    assert set(items[0]) == {
        "event_id",
        "trace_id",
        "task_id",
        "session_id",
        "event_type",
        "status",
        "capability_id",
        "error_code",
        "attributes",
        "created_at",
    }
    assert items[0]["attributes"] == {
        "safe": "visible",
        "authorization": "[REDACTED]",
        "token": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]"},
        "dsn": "[REDACTED]",
        "headers": {
            "X-Api-Key": "[REDACTED]",
            "X-CSRF-Token": "[REDACTED]",
        },
    }
    assert "MUST-NOT-LEAK-ADMIN-TRACE" not in response.text
    assert "MUST-NOT-LEAK-URI" not in response.text
    assert "MUST-NOT-LEAK-API-KEY" not in response.text
    assert "MUST-NOT-LEAK-DIRECT-TOKEN" not in response.text
    assert "MUST-NOT-LEAK-CSRF-TOKEN" not in response.text
    assert query.calls == [
        (
            "trace",
            "trace-1",
            {"task_id": "task-1", "session_id": "session-1"},
        )
    ]
    assert trace.events[-1].attributes["action"] == "traces_list"
    assert trace.events[-1].attributes["result_count"] == 100


def test_trace_list_denies_before_query_and_records_blocked_action() -> None:
    query = RecordingTraceQuery([_trace_event(0)])
    trace = RecordingTrace()
    client = _client(
        RecordingTaskStore(),
        RecordingIdentityMapping(),
        trace,
        trace_query=query,
    )

    response = client.get("/api/v1/admin/traces?trace_id=trace-1")

    assert response.status_code == 403
    assert response.json() == ROLE_DENIED_DETAIL
    assert query.calls == []
    assert len(trace.events) == 1
    assert trace.events[0].status == "blocked"
    assert trace.events[0].attributes["action"] == "traces_list"
    assert trace.events[0].attributes["authorization_decision"] == "deny"
    assert trace.events[0].attributes["role_claim_authenticated"] is False


@pytest.mark.parametrize(
    "query_string",
    ["", "?trace_id=", "?task_id=%20%20&session_id="],
)
def test_authorized_trace_list_requires_a_non_blank_filter(
    query_string: str,
) -> None:
    query = RecordingTraceQuery([_trace_event(0)])
    trace = RecordingTrace()
    client = _client(
        RecordingTaskStore(),
        RecordingIdentityMapping(),
        trace,
        trace_query=query,
    )

    response = client.get(
        f"/api/v1/admin/traces{query_string}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "trace_filter_required",
            "message": "trace_id, task_id, or session_id is required.",
        }
    }
    assert query.calls == []
    assert trace.events[-1].status == "failed"
    assert trace.events[-1].attributes["reason_code"] == "trace_filter_required"


def test_trace_list_returns_503_when_admin_service_is_unconfigured() -> None:
    response = TestClient(create_app()).get(
        "/api/v1/admin/traces?trace_id=trace-1",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "admin_registry_unavailable",
            "message": "Admin Registry provider is not configured.",
        }
    }


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/admin/tasks?ai_user_id=user-1",
        "/api/v1/admin/tasks/task-000/events",
        "/api/v1/admin/bindings?ai_user_id=user-1",
        "/api/v1/admin/traces?trace_id=trace-1",
    ],
)
def test_new_admin_routes_never_reach_any_execution_surface(url: str) -> None:
    adapter = AdapterSentinel()
    gateway = GatewaySentinel(adapter)
    workflow = WorkflowSentinel()
    runtime = RuntimeSentinel(gateway, workflow)
    client = _client(
        RecordingTaskStore([_task(0)], [_event(0)]),
        RecordingIdentityMapping([_binding(0)]),
        RecordingTrace(),
        runtime,
    )

    response = client.get(url, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert runtime.calls == 0
    assert gateway.calls == 0
    assert adapter.calls == 0
    assert workflow.calls == 0
