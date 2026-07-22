"""API acceptance tests for Admin Lite Registry management."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.admin.registry import AdminRegistryService
from app.infra.identity.mock_identity_mapping import MockIdentityMapping
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.main import create_app
from app.ports.capability_registry import CapabilitySpec
from app.ports.task_store import TaskEventRecord, TaskRecord
from app.ports.trace import TraceEvent


class RecordingRegistry:
    def __init__(self, items: list[CapabilitySpec] | None = None) -> None:
        self.items = {item.capability_id: item for item in items or []}
        self.calls: list[tuple[str, str | None]] = []

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        self.calls.append(("create", capability.capability_id))
        self.items[capability.capability_id] = capability
        return capability

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        self.calls.append(("get", capability_id))
        return self.items.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        self.calls.append(("list", None))
        return list(self.items.values())

    async def update(
        self,
        capability_id: str,
        patch: dict[str, Any],
    ) -> CapabilitySpec:
        self.calls.append(("update", capability_id))
        updated = self.items[capability_id].model_copy(update=patch)
        self.items[capability_id] = updated
        return updated

    async def disable(self, capability_id: str) -> CapabilitySpec:
        self.calls.append(("disable", capability_id))
        disabled = self.items[capability_id].model_copy(update={"status": "disabled"})
        self.items[capability_id] = disabled
        return disabled


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record_event(self, event: TraceEvent) -> None:
        self.events.append(event)


class EmptyTaskStore:
    async def create_task(self, record: TaskRecord) -> TaskRecord:
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        raise AssertionError("Registry routes must not update Tasks")

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None:
        raise AssertionError("Registry routes must not append Task events")

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


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


class RuntimeSentinel:
    def __init__(self, gateway: GatewaySentinel) -> None:
        self.gateway = gateway
        self.calls = 0

    async def handle_user_message(self, **_: Any) -> Any:
        self.calls += 1
        await self.gateway.execute_capability()
        raise AssertionError("Admin route reached the Runtime execution chain")


def _capability(
    capability_id: str = "oa.leave.apply",
    *,
    status: str = "draft",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name="Leave application",
        type="action",
        intent_tags=["leave"],
        input_schema={
            "type": "object",
            "properties": {"password": {"type": "string"}},
        },
        output_schema={"type": "object", "properties": {"cookie": {}}},
        input_schema_digest="sha256:input",
        output_schema_digest="sha256:output",
        risk_level="medium",
        owner="oa-team",
        version="1.0.0",
        status=status,
        short_description="Submit leave application",
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=True,
        policy_digest="sha256:policy",
    )


def _create_body(capability_id: str = "oa.leave.apply") -> dict[str, Any]:
    return _capability(capability_id).model_dump(mode="json", exclude={"status"})


def _client(
    registry: RecordingRegistry,
    trace: RecordingTrace,
    runtime: RuntimeSentinel | None = None,
) -> TestClient:
    service = AdminRegistryService(
        capability_registry=registry,
        task_store=EmptyTaskStore(),
        identity_mapping=MockIdentityMapping(rows=[]),
        policy_guard=MinimalPolicyGuard(
            admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS
        ),
        trace_port=trace,
    )
    return TestClient(
        create_app(
            runtime=runtime,
            admin_registry_service=service,
        )
    )


ADMIN_HEADERS = {"X-EternalAI-Roles": "viewer, admin"}
ROLE_DENIED_DETAIL = {
    "detail": {
        "code": "role_not_allowed",
        "message": "Management role is required.",
    }
}


def test_registry_list_and_get_return_credential_safe_metadata() -> None:
    registry = RecordingRegistry([_capability()])
    trace = RecordingTrace()
    client = _client(registry, trace)

    listed = client.get("/api/v1/admin/registry", headers=ADMIN_HEADERS)
    viewed = client.get(
        "/api/v1/admin/registry/oa.leave.apply",
        headers=ADMIN_HEADERS,
    )

    assert listed.status_code == 200
    assert viewed.status_code == 200
    assert listed.json()["items"] == [viewed.json()]
    returned_items = [*listed.json()["items"], viewed.json()]
    for item in returned_items:
        assert "input_schema" not in item
        assert "output_schema" not in item
        assert "policy_digest" not in item
        assert "password" not in str(item)
        assert "cookie" not in str(item)
    assert [event.attributes["action"] for event in trace.events] == ["list", "get"]


def test_create_is_draft_then_enable_and_disable_are_independent_actions() -> None:
    registry = RecordingRegistry()
    trace = RecordingTrace()
    client = _client(registry, trace)

    created = client.post(
        "/api/v1/admin/registry",
        json=_create_body(),
        headers=ADMIN_HEADERS,
    )
    enabled = client.post(
        "/api/v1/admin/registry/oa.leave.apply/enable",
        headers=ADMIN_HEADERS,
    )
    disabled = client.post(
        "/api/v1/admin/registry/oa.leave.apply/disable",
        headers=ADMIN_HEADERS,
    )

    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert [event.attributes["action"] for event in trace.events] == [
        "create",
        "enable",
        "disable",
    ]


def test_create_rejects_client_supplied_status() -> None:
    registry = RecordingRegistry()
    trace = RecordingTrace()
    client = _client(registry, trace)
    body = _create_body()
    body["status"] = "active"

    response = client.post(
        "/api/v1/admin/registry",
        json=body,
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert registry.calls == []
    assert trace.events == []


@pytest.mark.parametrize(
    "capability_id",
    ["oa.leave.apply", "missing.capability"],
)
def test_read_denial_is_identical_before_registry_access(
    capability_id: str,
) -> None:
    registry = RecordingRegistry([_capability()])
    trace = RecordingTrace()
    client = _client(registry, trace)

    response = client.get(f"/api/v1/admin/registry/{capability_id}")

    assert response.status_code == 403
    assert response.json() == ROLE_DENIED_DETAIL
    assert registry.calls == []
    assert len(trace.events) == 1
    assert trace.events[0].status == "blocked"
    assert trace.events[0].attributes["reason_code"] == "role_not_allowed"


def test_write_denial_prevents_registry_mutation_and_is_traced() -> None:
    registry = RecordingRegistry()
    trace = RecordingTrace()
    client = _client(registry, trace)

    response = client.post(
        "/api/v1/admin/registry",
        json=_create_body(),
        headers={"X-EternalAI-Roles": "viewer"},
    )

    assert response.status_code == 403
    assert response.json() == ROLE_DENIED_DETAIL
    assert registry.calls == []
    assert len(trace.events) == 1
    assert trace.events[0].status == "blocked"
    assert trace.events[0].attributes["action"] == "create"


def test_deprecated_transition_returns_stable_conflict_shape() -> None:
    registry = RecordingRegistry([_capability(status="deprecated")])
    trace = RecordingTrace()
    client = _client(registry, trace)

    response = client.post(
        "/api/v1/admin/registry/oa.leave.apply/enable",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "invalid_status_transition",
            "message": "Capability status transition is not allowed.",
        }
    }
    assert registry.calls == [("get", "oa.leave.apply")]
    assert trace.events[-1].status == "failed"


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [(ADMIN_HEADERS, 200), ({}, 403)],
)
def test_admin_routes_never_reach_runtime_gateway_or_adapter(
    headers: dict[str, str],
    expected_status: int,
) -> None:
    adapter = AdapterSentinel()
    gateway = GatewaySentinel(adapter)
    runtime = RuntimeSentinel(gateway)
    registry = RecordingRegistry([_capability()])
    client = _client(registry, RecordingTrace(), runtime)

    response = client.get("/api/v1/admin/registry", headers=headers)

    assert response.status_code == expected_status
    assert runtime.calls == 0
    assert gateway.calls == 0
    assert adapter.calls == 0
