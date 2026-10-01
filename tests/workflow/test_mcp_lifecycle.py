"""Owner-bound terminal repair with the real Runtime and Workflow engine."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1.mcp import McpApiService, ResumeRequest
from app.mcp.models import McpFailure
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from tests.infra.mcp.test_transport import Peer, serving
from tests.mcp.test_operations import fixture
from tests.runtime.test_runtime_user_action import _build_harness, _dispatch, _outcome, _tenant_pair


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
                with pytest.raises(RuntimeError, match="synthetic .* interruption"):
                    await chat.engine.finalize_governed_task(task_id=pending.task_id)
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
        ("FAILED", "failed", "mcp_outcome_unknown", "task_failed"),
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
