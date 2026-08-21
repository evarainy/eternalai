from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGateRequest,
    VersionBinding,
    VersionBindingMismatchError,
    build_task_version_binding_manifest,
)


def _binding(*, version: str = "1.0.0") -> VersionBinding:
    return VersionBinding(
        resource_type="tool",
        resource_id="oa.approve",
        version=version,
        digest=("a" if version == "1.0.0" else "b") * 64,
    )


def test_human_gate_locks_manifest_actor_request_and_decision() -> None:
    async def exercise() -> None:
        gate = InMemoryHumanGate()
        now = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
        manifest = build_task_version_binding_manifest(
            task_id="task-1",
            bindings=(_binding(),),
            locked_at=now,
        )
        assert await gate.bind_task(manifest) == manifest
        assert await gate.get_task_binding("task-1") == manifest
        await gate.assert_task_bindings("task-1", (_binding(),), exact=True)
        with pytest.raises(VersionBindingMismatchError):
            await gate.assert_task_bindings(
                "task-1",
                (_binding(version="2.0.0"),),
            )

        request = HumanGateRequest(
            request_id="request-1",
            task_id="task-1",
            requested_for_ai_user_id="user-1",
            requested_session_id="session-1",
            requested_tenant_id="tenant-1",
            action_digest="d" * 64,
            request_digest="c" * 64,
            binding_manifest_digest=manifest.manifest_digest,
            requested_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        assert await gate.create_request(request) == request

        wrong_actor = HumanGateDecisionRecord(
            request_id=request.request_id,
            task_id=request.task_id,
            decided_by_ai_user_id="user-2",
            decided_session_id="session-1",
            decided_tenant_id="tenant-1",
            decision="confirmed",
            request_digest=request.request_digest,
            binding_manifest_digest=request.binding_manifest_digest,
            decided_at=now + timedelta(seconds=1),
        )
        with pytest.raises(HumanGateConflictError):
            await gate.record_decision(wrong_actor)

        decision = wrong_actor.model_copy(
            update={"decided_by_ai_user_id": "user-1"}
        )
        wrong_tenant = decision.model_copy(
            update={"decided_tenant_id": "tenant-2"}
        )
        with pytest.raises(HumanGateConflictError):
            await gate.record_decision(wrong_tenant)

        assert await gate.record_decision(decision) == decision
        assert await gate.get_decision(request.request_id) == decision

        changed = decision.model_copy(
            update={"decision": "rejected", "decided_at": now + timedelta(seconds=2)}
        )
        with pytest.raises(HumanGateConflictError):
            await gate.record_decision(changed)

        expired_request = request.model_copy(update={"request_id": "request-2"})
        await gate.create_request(expired_request)
        expired_decision = decision.model_copy(
            update={
                "request_id": "request-2",
                "decided_at": now + timedelta(minutes=6),
            }
        )
        with pytest.raises(HumanGateConflictError):
            await gate.record_decision(expired_decision)

        rejected_request = request.model_copy(update={"request_id": "request-3"})
        await gate.create_request(rejected_request)
        rejection = decision.model_copy(
            update={"request_id": "request-3", "decision": "rejected"}
        )
        assert await gate.record_decision(rejection) == rejection

    asyncio.run(exercise())
