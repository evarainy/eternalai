"""Owner-bound terminal repair with the real Runtime and Workflow engine."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1.mcp import McpApiService, ResumeRequest
from app.mcp.models import McpFailure
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from app.ports.capability_gateway import ExecutionResult
from app.ports.mcp import McpValidatedOutcome
from app.ports.workflow_engine import GovernedFinalizationError
from tests.infra.mcp.test_transport import Peer, serving
from tests.mcp.test_operations import fixture
from tests.runtime.test_runtime_user_action import (
    _START_MESSAGE,
    _build_harness,
    _dispatch,
    _outcome,
    _tenant_pair,
)


async def governed_chat():
    chat = await _build_harness()
    pending = next(iter(chat.runtime._pending_workflows.values()))
    h = await fixture()
    context = h.op.context.model_copy(
        update={
            "task_id": pending.task_id,
            "user_id": pending.owner.ai_user_id,
            "tenant_id": pending.owner.tenant_id,
            "chat_session_id": pending.owner.session_id,
        }
    )
    h.op = h.op.model_copy(
        update={
            "context": context,
            "state": "VERIFIED_SUCCESS",
            "outer_capability_id": pending.capability_id,
            "outer_version": pending.projection_snapshot.capability_version,
            "gate_request_id": pending.gate_request_id,
            "action_digest": pending.action_digest,
        }
    )
    h.store.op = h.op
    h.operations.gates = h.store.gates = chat.gate
    chat.engine._governed = h.operations
    session = AuthenticatedSessionContext(
        principal=chat.principal,
        fingerprint=b"fresh-login",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return chat, h, pending, session


@pytest.mark.parametrize("fault", ["task", "terminal", "evaluation"])
def test_real_chat_confirmation_finalizer_failure_preserves_business_terminal(fault):
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op = h.op.model_copy(update={"state": "WAITING_LOCAL_CONFIRM"})
        sends = 0

        async def owned(op):
            h.operations.lifecycle_owner(op)

        async def execute(*args, **kwargs):
            nonlocal sends
            assert h.store.op.state == "READY"
            sends += 1
            await h.store.transition(h.store.op, state="SENDING")
            return ExecutionResult(
                status="completed",
                trace_id=pending.trace_id,
                mcp_outcome=McpValidatedOutcome(state="VERIFIED_SUCCESS", persistence={}),
            )

        h.operations.owned = owned
        h.operations.gateway = SimpleNamespace(execute_capability=execute)
        original_update = chat.runtime._task_store.update_status
        original_once = chat.trace.record_event_once

        async def broken_update(task_id, *args, **kwargs):
            if task_id == pending.task_id:
                raise RuntimeError("synthetic finalizer write failure")
            await original_update(task_id, *args, **kwargs)

        async def broken_trace(event, key):
            if event.event_type == (
                "task_completed" if fault == "terminal" else "evaluation_recorded"
            ):
                raise RuntimeError("synthetic finalizer write failure")
            await original_once(event, key)

        token = authenticated_session.set(session)
        try:
            if fault == "task":
                chat.runtime._task_store.update_status = broken_update
            else:
                chat.trace.record_event_once = broken_trace
            with pytest.raises(RuntimeError, match="finalizer"):
                await _dispatch(chat)
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert h.store.op.state == "VERIFIED_SUCCESS" and sends == 1
            remembered = chat.runtime._session_memory.recall(pending.owner)
            assert remembered and remembered[-1].terminal_status == "completed"
            assert (
                claim.state == "completed"
                and not claim.cleanup_complete
                and claim.pending is pending
            )
            chat.runtime._task_store.update_status = original_update
            chat.trace.record_event_once = original_once
            response = await _dispatch(chat)
            assert _outcome(response) == "action_already_claimed"
            assert "nothing was executed" not in response.model_dump_json()
            assert "本次未执行" not in response.model_dump_json()
            assert chat.runtime._task_store.records[pending.task_id].status == "completed"
            assert sends == chat.engine.resume_calls == 1
            assert len(chat.runtime._session_memory.recall(pending.owner)) == 1
            assert len(chat.trace.once) == 2 and not chat.runtime._pending_workflows
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


def test_expired_cleanup_then_apps_new_generation_does_not_block_old_owner_chat():
    async def run(profile):
        chat, h, captured, session = await governed_chat()
        old_gate = await chat.gate.get_request(captured.gate_request_id)
        deadline = datetime.now(UTC) - timedelta(minutes=10)
        old_gate = old_gate.model_copy(
            update={"requested_at": deadline - timedelta(minutes=10), "expires_at": deadline}
        )
        chat.gate._requests[old_gate.request_id] = old_gate
        pending = replace(captured, expires_at=deadline, monotonic_deadline=0)
        key = (pending.owner.tenant_id, pending.owner.session_id, pending.owner.ai_user_id)
        chat.runtime._pending_workflows[key] = pending
        h.store.op = h.op = h.op.model_copy(
            update={"state": "WAITING_LOCAL_CONFIRM", "expires_at": deadline}
        )
        original_once = chat.trace.record_event_once

        async def failure(event, key):
            if event.event_type == "evaluation_recorded":
                raise RuntimeError("synthetic cleanup interruption")
            await original_once(event, key)

        async def owned(op):
            h.operations.lifecycle_owner(op)

        async def mapping(*args):
            return SimpleNamespace()

        async def connection(**kwargs):
            return SimpleNamespace(identity_evidence={"subject": "synthetic"})

        async def build(**kwargs):
            return h.op.context.model_copy(
                update={"login_session_fingerprint": session.fingerprint.hex()}
            )

        h.operations.owned = owned
        h.operations.contexts = SimpleNamespace(
            store=SimpleNamespace(mapping=mapping, connection=connection), build=build
        )
        h.operations.recovery_policies[(h.op.context.service_config_id, h.op.remote_tool)] = (
            SimpleNamespace(approved=True, version="synthetic", same_subject=lambda *args: True)
        )
        token = authenticated_session.set(session)
        try:
            chat.trace.record_event_once = failure
            with pytest.raises(GovernedFinalizationError) as interrupted:
                await chat.runtime._expire_pending_confirmations(pending.owner)
            assert str(interrupted.value.__cause__) == "synthetic cleanup interruption"
            assert h.store.op.state == "EXPIRED"
            chat.trace.record_event_once = original_once
            service = McpApiService(
                SimpleNamespace(configs={profile.service_config_id: profile}),
                h.operations,
                chat.engine,
            )
            op = h.store.op
            renewed = await service.resume(
                op,
                ResumeRequest(
                    action="takeover",
                    expected_revision=op.revision,
                    preview_digest=service.view(op).preview_digest,
                ),
            )
            new_op = h.store.op
            assert (
                renewed.state == "WAITING_LOCAL_CONFIRM"
                and new_op.action_digest != pending.action_digest
            )
            task_before = chat.runtime._task_store.records[pending.task_id]
            ordinary = await chat.runtime.handle_user_message(
                channel="web",
                principal=chat.principal,
                session_id=pending.owner.session_id,
                message=_START_MESSAGE,
                client_capabilities={},
            )
            assert ordinary.status == "waiting_user" and ordinary.task_id != pending.task_id
            assert (
                h.store.op == new_op
                and await chat.gate.get_request(new_op.gate_request_id) is not None
            )
            assert all(
                item.task_id != pending.task_id for item in chat.runtime._pending_workflows.values()
            )
            assert chat.runtime._task_store.records[pending.task_id] == task_before
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert claim.cleanup_complete
            assert len(chat.trace.once) == 2 and chat.engine.resume_calls == 0
        finally:
            authenticated_session.reset(token)

    with serving(Peer("2025-11-25")) as profile:
        asyncio.run(run(profile))


def test_failed_error_code_agrees_between_operation_task_terminal_and_evaluation():
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op.model_copy(update={"state": "FAILED"})
        token = authenticated_session.set(session)
        try:
            result = await chat.engine.finalize_governed_task(task_id=pending.task_id)
            expected = h.operations.result(h.store.op, pending.trace_id).error_code
            assert expected == "policy_denied"
            assert (
                result.error_code
                == chat.runtime._task_store.records[pending.task_id].error_code
                == expected
            )
            assert {event.error_code for event in chat.trace.once.values()} == {expected}
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


def test_apps_confirm_endpoint_repairs_original_chat_before_later_confirmation_or_expiry():
    async def run(profile):
        chat, h, pending, session = await governed_chat()
        gate = await chat.gate.get_request(pending.gate_request_id)
        h.store.op = h.op = h.op.model_copy(
            update={
                "state": "WAITING_LOCAL_CONFIRM",
                "expires_at": gate.expires_at,
            }
        )
        sends = 0

        async def resume(**kwargs):
            nonlocal sends
            assert kwargs["expected_action_digest"] == pending.action_digest
            sends += 1
            await h.store.transition(h.store.op, state="VERIFIED_SUCCESS")

        service = McpApiService(
            SimpleNamespace(configs={profile.service_config_id: profile}),
            h.operations,
            SimpleNamespace(
                resume=resume, finalize_governed_task=chat.engine.finalize_governed_task
            ),
        )
        token = authenticated_session.set(session)
        try:
            body = ResumeRequest(
                action="confirm",
                expected_revision=h.op.revision,
                preview_digest=service.view(h.op).preview_digest,
            )
            assert (await service.resume(h.op, body)).state == "VERIFIED_SUCCESS"
            assert chat.runtime._task_store.records[pending.task_id].status == "completed"
            chat.runtime._monotonic_clock = lambda: 10000
            await chat.runtime._expire_pending_confirmations(pending.owner)
            assert _outcome(await _dispatch(chat)) == "action_already_claimed"
            assert sends == 1 and chat.engine.resume_calls == 0
            assert len(chat.trace.once) == 2 and not chat.runtime._pending_workflows
        finally:
            authenticated_session.reset(token)

    with serving(Peer("2025-11-25")) as profile:
        asyncio.run(run(profile))


@pytest.mark.parametrize("fault", ["task", "terminal", "evaluation", "none"])
def test_apps_terminal_repair_preserves_chat_task_trace_and_never_resumes(fault):
    async def run():
        chat, h, pending, session = await governed_chat()
        token = authenticated_session.set(session)
        original_update = chat.runtime._task_store.update_status
        original_once = chat.trace.record_event_once

        async def fail_update(*args, **kwargs):
            raise RuntimeError("synthetic task interruption")

        async def fail_trace(event, key):
            if event.event_type == (
                "task_completed" if fault == "terminal" else "evaluation_recorded"
            ):
                raise RuntimeError("synthetic trace interruption")
            await original_once(event, key)

        try:
            if fault == "task":
                chat.runtime._task_store.update_status = fail_update
            elif fault != "none":
                chat.trace.record_event_once = fail_trace
            if fault != "none":
                with pytest.raises(GovernedFinalizationError) as interrupted:
                    await chat.engine.finalize_governed_task(task_id=pending.task_id)
                assert "synthetic" in str(interrupted.value.__cause__)
            chat.runtime._task_store.update_status = original_update
            chat.trace.record_event_once = original_once
            await asyncio.gather(
                *(chat.engine.finalize_governed_task(task_id=pending.task_id) for _ in range(3))
            )
            chat.runtime._monotonic_clock = lambda: 10000
            await chat.runtime._expire_pending_confirmations(pending.owner)
            response = await _dispatch(chat)
            assert _outcome(response) == "action_already_claimed"
            assert chat.runtime._task_store.records[pending.task_id].status == "completed"
            assert chat.engine.resume_calls == 0
            assert not chat.runtime._pending_workflows
            terminal = [step for step in chat.trace.steps if step["event_type"] == "task_completed"]
            evaluations = [
                step
                for step in chat.trace.steps
                if step["event_type"] == "evaluation_recorded"
                and step["task_id"] == pending.task_id
            ]
            assert len(terminal) == len(evaluations) == 1
            assert terminal[0]["trace_id"] == pending.trace_id
            assert terminal[0]["trace_id"] != h.op.operation_id
            assert (
                evaluations[0]["attributes"]["business_verification"]["result"] == "not_evaluated"
            )
            assert h.store.transitions == []
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize("state", ["UNKNOWN", "WAITING_EXTERNAL_CONFIRM", "SENDING"])
def test_uncertain_operation_is_never_finalized_as_failure(state):
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op.model_copy(update={"state": state})
        token = authenticated_session.set(session)
        try:
            assert await chat.engine.finalize_governed_task(task_id=pending.task_id) is None
            assert chat.runtime._task_store.records[pending.task_id].status == "waiting_user"
            assert chat.trace.once == {}
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


def test_other_owner_expiry_does_not_touch_pending_or_task():
    async def run():
        chat, other, a, b = await _tenant_pair()
        chat.runtime._monotonic_clock = lambda: 10000
        calls_before = list(chat.runtime._task_store.status_updates)
        # The second owner's request must not even attempt the first owner's cleanup.
        await chat.runtime._expire_pending_confirmations(b.owner)
        assert chat.runtime._task_store.records[a.task_id].status == "waiting_user"
        assert (
            a.owner.tenant_id,
            a.owner.session_id,
            a.owner.ai_user_id,
        ) in chat.runtime._pending_workflows
        assert len(chat.runtime._task_store.status_updates) == len(calls_before) + 1
        assert chat.engine.resume_calls == 0
        assert chat.gate.record_decision_calls == 0

    asyncio.run(run())


def test_lifecycle_owner_rejects_other_user_before_task_or_trace_writes():
    async def run():
        chat, h, pending, session = await governed_chat()
        foreign = AuthenticatedSessionContext(
            principal=session.principal.model_copy(update={"ai_user_id": "other"}),
            fingerprint=b"other",
            expires_at=session.expires_at,
        )
        token = authenticated_session.set(foreign)
        try:
            with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                await chat.engine.finalize_governed_task(task_id=pending.task_id)
            assert chat.runtime._task_store.records[pending.task_id].status == "waiting_user"
            assert chat.trace.once == {}
            assert h.store.transitions == []
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize(
    "state,status,error,event_type",
    [
        ("FAILED", "failed", "policy_denied", "task_failed"),
        (
            "EXPIRED",
            "confirmation_invalidated",
            "confirm_required",
            "task_confirmation_invalidated",
        ),
        ("CANCELLED", "cancelled", None, "task_cancelled"),
    ],
)
def test_terminal_repair_preserves_failure_and_cancellation_semantics(
    state, status, error, event_type
):
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op.model_copy(update={"state": state})
        token = authenticated_session.set(session)
        try:
            await chat.engine.finalize_governed_task(task_id=pending.task_id)
            await chat.engine.finalize_governed_task(task_id=pending.task_id)
            task = chat.runtime._task_store.records[pending.task_id]
            assert task.status == status and task.error_code == error
            terminal = [step for step in chat.trace.steps if step["event_type"] == event_type]
            assert len(terminal) == 1 and terminal[0]["error_code"] == error
            assert len(chat.trace.once) == 2 and chat.engine.resume_calls == 0
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())
