"""Composition checks for the additive Admin binding mutation service."""

from __future__ import annotations

from typing import cast, get_type_hints

import pytest

from app.admin.registry import (
    AdminRegistryService,
    AdminRegistryServiceWithBindingMutations,
    AdminRequestContext,
)
from app.composition import ProductionComponents, build_admin_registry_service
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.identity_mapping import (
    IdentityCheckResult,
    IdentityMappingMutationResult,
    IdentityMappingPort,
)
from app.ports.task_store import TaskStorePort
from app.ports.trace import TraceEvent, TracePort, TraceQueryPort

TARGET_AI_USER_ID = "usr_v1_" + ("d" * 43)
BINDING_ID = f"oa-session-v1:{TARGET_AI_USER_ID}"


class MutationPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def revoke_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult:
        self.calls.append(("revoke", binding_id))
        return self._result()

    async def reset_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult:
        self.calls.append(("reset", binding_id))
        return self._result()

    @staticmethod
    def _result() -> IdentityMappingMutationResult:
        return IdentityMappingMutationResult(
            mapping=IdentityCheckResult(
                binding_id=BINDING_ID,
                target_system="oa",
                execution_identity="user_delegated",
                bind_status="revoked",
                reason_code="identity_revoked",
            ),
            previous_bind_status="active",
            changed=True,
        )


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record_event(self, event: TraceEvent) -> None:
        self.events.append(event)


@pytest.mark.anyio
async def test_builder_returns_the_additive_subclass_with_working_mutations() -> None:
    mapping = MutationPort()
    trace = RecordingTrace()

    service = build_admin_registry_service(
        capability_registry=cast(CapabilityRegistryPort, object()),
        task_store=cast(TaskStorePort, object()),
        identity_mapping=cast(IdentityMappingPort, mapping),
        trace_port=cast(TracePort, trace),
        trace_query=cast(TraceQueryPort, object()),
    )

    assert isinstance(service, AdminRegistryService)
    assert isinstance(service, AdminRegistryServiceWithBindingMutations)
    result = await service.revoke_binding(
        BINDING_ID,
        AdminRequestContext(
            trace_id="trace-composition",
            session_id="admin-session",
            ai_user_id="usr_v1_synthetic",
            roles=("admin",),
            principal_authenticated=True,
        ),
    )
    assert result.binding.binding_id == BINDING_ID
    assert mapping.calls == [("revoke", BINDING_ID)]
    assert trace.events[0].attributes["policy_capability_id"] == (
        "admin_bindings_revoke"
    )


def test_production_components_keep_the_base_admin_service_annotation() -> None:
    annotations = get_type_hints(ProductionComponents)

    assert annotations["admin_registry_service"] is AdminRegistryService
