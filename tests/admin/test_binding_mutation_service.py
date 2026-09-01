"""Focused tests for the isolated Admin binding mutation service."""

from __future__ import annotations

from typing import Literal, cast
from uuid import uuid4

import pytest

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.admin.registry import (
    AdminBindingMutationService,
    AdminBindingMutationUnavailableError,
    AdminBindingNotFoundError,
    AdminRequestContext,
    AdminRoleNotAllowedError,
)
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.auth import PrincipalOrgContext
from app.ports.identity_mapping import (
    IdentityCheckResult,
    IdentityMappingMutationError,
    IdentityMappingMutationResult,
    IdentityMappingPort,
)
from app.ports.trace import TraceEvent, TracePort

ADMIN_AI_USER_ID = "usr_v1_" + ("a" * 43)
TARGET_AI_USER_ID = "usr_v1_" + ("b" * 43)
BINDING_ID = f"oa-session-v1:{TARGET_AI_USER_ID}"


class RecordingMutationPort:
    def __init__(
        self,
        outcome: IdentityMappingMutationResult | IdentityMappingMutationError | None,
    ) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    async def revoke_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        self.calls.append(("revoke", binding_id))
        return self._result()

    async def reset_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None:
        self.calls.append(("reset", binding_id))
        return self._result()

    def _result(self) -> IdentityMappingMutationResult | None:
        if isinstance(self.outcome, IdentityMappingMutationError):
            raise self.outcome
        return self.outcome


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record_event(self, event: TraceEvent) -> None:
        self.events.append(event)


def _context(*roles: str) -> AdminRequestContext:
    return AdminRequestContext(
        trace_id="trace-binding-mutation",
        session_id="admin-session",
        ai_user_id=ADMIN_AI_USER_ID,
        roles=roles,
        org_ctx=PrincipalOrgContext(),
        principal_authenticated=True,
    )


def _mutation_result(
    *,
    previous_bind_status: Literal["active", "expired", "revoked"] = "active",
    changed: bool = True,
) -> IdentityMappingMutationResult:
    return IdentityMappingMutationResult(
        mapping=IdentityCheckResult(
            binding_id=BINDING_ID,
            target_system="oa",
            execution_identity="user_delegated",
            bind_status="revoked",
            reason_code="identity_revoked",
        ),
        previous_bind_status=previous_bind_status,
        changed=changed,
    )


def _service(
    port: RecordingMutationPort,
    trace: RecordingTrace,
) -> AdminBindingMutationService:
    return AdminBindingMutationService(
        identity_mapping=cast(IdentityMappingPort, port),
        policy_guard=MinimalPolicyGuard(admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS),
        trace_port=cast(TracePort, trace),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "expected_next_action"),
    [("revoke", "none"), ("reset", "reauthenticate")],
)
async def test_admin_cross_user_mutations_use_distinct_port_methods_and_safe_audit(
    operation: Literal["revoke", "reset"],
    expected_next_action: Literal["none", "reauthenticate"],
) -> None:
    port = RecordingMutationPort(_mutation_result())
    trace = RecordingTrace()
    service = _service(port, trace)

    if operation == "revoke":
        result = await service.revoke_binding(BINDING_ID, _context("admin"))
    else:
        result = await service.reset_binding(BINDING_ID, _context("admin"))

    assert result.model_dump(mode="json") == {
        "action": operation,
        "binding": {
            "binding_id": BINDING_ID,
            "target_system": "oa",
            "execution_identity": "user_delegated",
            "bind_status": "revoked",
            "binding_scope": None,
            "account_set_id": None,
            "device_domain_id": None,
            "reason_code": "identity_revoked",
        },
        "changed": True,
        "next_action": expected_next_action,
    }
    assert port.calls == [(operation, BINDING_ID)]
    assert trace.events == [
        TraceEvent(
            trace_id="trace-binding-mutation",
            task_id="admin-request:trace-binding-mutation",
            session_id="admin-session",
            tenant_id="default",
            ai_user_id=ADMIN_AI_USER_ID,
            event_type="admin_action",
            status="ok",
            attributes={
                "action": f"bindings_{operation}",
                "policy_capability_id": f"admin_bindings_{operation}",
                "authorization_decision": "allow",
                "role_claim_source": "authenticated_principal",
                "role_claim_authenticated": True,
                "binding_id": BINDING_ID,
                "previous_bind_status": "active",
                "after_bind_status": "revoked",
                "changed": True,
                "next_action": expected_next_action,
            },
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["revoke", "reset"])
async def test_non_admin_is_denied_before_any_port_call(
    operation: Literal["revoke", "reset"],
) -> None:
    port = RecordingMutationPort(_mutation_result())
    trace = RecordingTrace()
    service = _service(port, trace)

    with pytest.raises(AdminRoleNotAllowedError):
        if operation == "revoke":
            await service.revoke_binding(BINDING_ID, _context("employee"))
        else:
            await service.reset_binding(BINDING_ID, _context("employee"))

    assert port.calls == []
    assert trace.events[0].status == "blocked"
    assert trace.events[0].attributes == {
        "action": f"bindings_{operation}",
        "policy_capability_id": f"admin_bindings_{operation}",
        "authorization_decision": "deny",
        "role_claim_source": "authenticated_principal",
        "role_claim_authenticated": True,
        "binding_id": BINDING_ID,
        "next_action": "none" if operation == "revoke" else "reauthenticate",
        "reason_code": "role_not_allowed",
    }


@pytest.mark.anyio
async def test_missing_binding_is_distinct_from_mutation_unavailable() -> None:
    port = RecordingMutationPort(None)
    trace = RecordingTrace()

    with pytest.raises(AdminBindingNotFoundError) as exc_info:
        await _service(port, trace).revoke_binding(BINDING_ID, _context("admin"))

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert port.calls == [("revoke", BINDING_ID)]
    assert trace.events[0].attributes["reason_code"] == "binding_not_found"


@pytest.mark.anyio
async def test_storage_failure_is_rethrown_without_exception_chain_or_sensitive_data() -> None:
    sensitive_marker = "synthetic-" + uuid4().hex
    port = RecordingMutationPort(IdentityMappingMutationError(f"credential={sensitive_marker}"))
    trace = RecordingTrace()

    with pytest.raises(AdminBindingMutationUnavailableError) as exc_info:
        await _service(port, trace).reset_binding(BINDING_ID, _context("admin"))

    rendered = str(exc_info.value) + repr(exc_info.value) + repr(trace.events)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert sensitive_marker not in rendered
    assert port.calls == [("reset", BINDING_ID)]
    assert trace.events[0].attributes["reason_code"] == "binding_mutation_unavailable"


@pytest.mark.anyio
async def test_invalid_binding_input_is_not_copied_into_trace_or_error() -> None:
    sensitive_marker = "synthetic-" + uuid4().hex
    unsafe_binding_id = f"oa-session-v1:credential={sensitive_marker}"
    port = RecordingMutationPort(None)
    trace = RecordingTrace()

    with pytest.raises(AdminBindingNotFoundError) as exc_info:
        await _service(port, trace).revoke_binding(
            unsafe_binding_id,
            _context("admin"),
        )

    rendered = str(exc_info.value) + repr(exc_info.value) + repr(trace.events)
    assert sensitive_marker not in rendered
    assert "binding_id" not in trace.events[0].attributes


@pytest.mark.anyio
async def test_idempotent_revoke_preserves_revoked_state_and_reports_unchanged() -> None:
    port = RecordingMutationPort(_mutation_result(previous_bind_status="revoked", changed=False))
    trace = RecordingTrace()

    result = await _service(port, trace).revoke_binding(BINDING_ID, _context("admin"))

    assert result.changed is False
    assert result.binding.bind_status == "revoked"
    assert trace.events[0].attributes["previous_bind_status"] == "revoked"
    assert trace.events[0].attributes["changed"] is False


@pytest.mark.anyio
async def test_revoke_expired_binding_audits_expired_to_revoked_transition() -> None:
    port = RecordingMutationPort(_mutation_result(previous_bind_status="expired", changed=True))
    trace = RecordingTrace()

    result = await _service(port, trace).revoke_binding(BINDING_ID, _context("admin"))

    assert result.binding.bind_status == "revoked"
    assert result.changed is True
    assert port.calls == [("revoke", BINDING_ID)]
    assert trace.events[0].attributes["previous_bind_status"] == "expired"
    assert trace.events[0].attributes["after_bind_status"] == "revoked"
    assert trace.events[0].attributes["changed"] is True
