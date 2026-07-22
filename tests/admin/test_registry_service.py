"""Unit tests for the Admin Lite Registry service."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.admin.registry import (
    AdminCapabilityCreate,
    AdminInvalidStatusTransitionError,
    AdminRegistryService,
    AdminRequestContext,
)
from app.composition import build_admin_registry_service
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.capability_registry import CapabilitySpec
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
        input_schema={"type": "object"},
        output_schema={"type": "object"},
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


def _payload(capability_id: str = "oa.leave.apply") -> AdminCapabilityCreate:
    source = _capability(capability_id)
    data = source.model_dump(exclude={"status"})
    return AdminCapabilityCreate.model_validate(data)


def _context(*roles: str) -> AdminRequestContext:
    return AdminRequestContext(
        trace_id="trace-admin",
        session_id="admin-lite",
        ai_user_id="unverified-admin-request",
        roles=roles,
    )


def _service(
    registry: RecordingRegistry,
    trace: RecordingTrace,
) -> AdminRegistryService:
    return AdminRegistryService(
        capability_registry=registry,
        policy_guard=MinimalPolicyGuard(
            admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS
        ),
        trace_port=trace,
    )


def test_management_builder_injects_the_closed_admin_action_allowlist() -> None:
    registry = RecordingRegistry([_capability()])
    trace = RecordingTrace()
    service = build_admin_registry_service(
        capability_registry=registry,
        trace_port=trace,
    )

    capabilities = asyncio.run(service.list_capabilities(_context("admin")))

    assert [capability.capability_id for capability in capabilities] == [
        "oa.leave.apply"
    ]
    assert registry.calls == [("list", None)]
    assert trace.events[0].attributes["policy_capability_id"] == "admin_registry_list"


def test_create_is_draft_and_enable_is_a_separate_action() -> None:
    registry = RecordingRegistry()
    trace = RecordingTrace()
    service = _service(registry, trace)

    async def exercise() -> tuple[CapabilitySpec, CapabilitySpec]:
        created = await service.create_capability(_payload(), _context("admin"))
        enabled = await service.enable_capability(
            created.capability_id,
            _context("admin"),
        )
        return created, enabled

    created, enabled = asyncio.run(exercise())

    assert created.status == "draft"
    assert enabled.status == "active"
    assert registry.calls == [
        ("create", "oa.leave.apply"),
        ("get", "oa.leave.apply"),
        ("update", "oa.leave.apply"),
    ]
    assert [event.attributes["action"] for event in trace.events] == [
        "create",
        "enable",
    ]
    assert trace.events[0].attributes["after_status"] == "draft"
    assert trace.events[1].attributes == {
        "action": "enable",
        "policy_capability_id": "admin_registry_enable",
        "authorization_decision": "allow",
        "role_claim_source": "request_header",
        "role_claim_authenticated": False,
        "before_status": "draft",
        "after_status": "active",
    }


@pytest.mark.parametrize(
    ("initial_status", "action", "expected_status", "mutation"),
    [
        ("active", "enable", "active", None),
        ("disabled", "enable", "active", "update"),
        ("draft", "disable", "disabled", "disable"),
        ("active", "disable", "disabled", "disable"),
        ("disabled", "disable", "disabled", None),
    ],
)
def test_enable_disable_state_transitions_are_stable(
    initial_status: str,
    action: str,
    expected_status: str,
    mutation: str | None,
) -> None:
    registry = RecordingRegistry([_capability(status=initial_status)])
    trace = RecordingTrace()
    service = _service(registry, trace)

    async def exercise() -> CapabilitySpec:
        if action == "enable":
            return await service.enable_capability(
                "oa.leave.apply",
                _context("admin"),
            )
        return await service.disable_capability(
            "oa.leave.apply",
            _context("admin"),
        )

    result = asyncio.run(exercise())

    assert result.status == expected_status
    mutation_calls = [call for call in registry.calls if call[0] != "get"]
    assert mutation_calls == ([] if mutation is None else [(mutation, "oa.leave.apply")])
    assert trace.events[-1].attributes["before_status"] == initial_status
    assert trace.events[-1].attributes["after_status"] == expected_status


@pytest.mark.parametrize("action", ["enable", "disable"])
def test_deprecated_transition_is_rejected_and_traced(action: str) -> None:
    registry = RecordingRegistry([_capability(status="deprecated")])
    trace = RecordingTrace()
    service = _service(registry, trace)

    async def exercise() -> None:
        if action == "enable":
            await service.enable_capability("oa.leave.apply", _context("admin"))
        else:
            await service.disable_capability("oa.leave.apply", _context("admin"))

    with pytest.raises(AdminInvalidStatusTransitionError):
        asyncio.run(exercise())

    assert registry.calls == [("get", "oa.leave.apply")]
    assert trace.events == [
        TraceEvent(
            trace_id="trace-admin",
            task_id="admin-request:trace-admin",
            session_id="admin-lite",
            event_type="admin_action",
            status="failed",
            capability_id="oa.leave.apply",
            attributes={
                "action": action,
                "policy_capability_id": f"admin_registry_{action}",
                "authorization_decision": "allow",
                "role_claim_source": "request_header",
                "role_claim_authenticated": False,
                "reason_code": "invalid_status_transition",
                "before_status": "deprecated",
                "after_status": "deprecated",
            },
        )
    ]
