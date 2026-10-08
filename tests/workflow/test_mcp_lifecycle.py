"""Owner-bound terminal repair with the real Runtime and Workflow engine."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1.mcp import McpApiService, ResumeRequest
from app.mcp.models import McpFailure
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from app.ports.capability_gateway import ExecutionResult
from app.ports.human_gate import VersionBindingMismatchError
from app.ports.mcp import McpValidatedOutcome
from app.ports.workflow_engine import GovernedFinalizationError
from app.ports.workflow_store import WorkflowOperation
from tests.infra.mcp.test_transport import Peer, serving
from tests.mcp.test_operations import fixture
from tests.runtime.test_runtime_user_action import (
    _START_MESSAGE,
    ControllableVersionGate,
    _build_harness,
    _dispatch,
    _outcome,
    _tenant_pair,
)


async def governed_chat(*, gate=None):
    chat = await _build_harness(gate=gate)
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


@pytest.mark.parametrize("state", ["WAITING_LOCAL_CONFIRM", "READY"])
@pytest.mark.parametrize("fault", ["none", "task", "evaluation_after_write"])
def test_presend_exception_retirement_preserves_internal_error_and_replay(state, fault):
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op.model_copy(update={"state": state})
        original_update = chat.runtime._task_store.update_status
        original_once = chat.trace.record_event_once
        failures = 0

        async def update(task_id, *args, **kwargs):
            nonlocal failures
            if fault == "task" and task_id == pending.task_id and not failures:
                failures += 1
                raise RuntimeError("synthetic retirement task failure")
            return await original_update(task_id, *args, **kwargs)

        async def once(event, key):
            nonlocal failures
            await original_once(event, key)
            if (
                fault == "evaluation_after_write" and event.event_type == "evaluation_recorded"
                and not failures
            ):
                failures += 1
                raise RuntimeError("synthetic retirement trace failure")

        chat.runtime._task_store.update_status = update
        chat.trace.record_event_once = once
        token = authenticated_session.set(session)
        try:
            async def retire():
                return await chat.runtime._retire_pending_confirmation(
                    pending_key=next(iter(chat.runtime._pending_workflows)), pending=pending,
                    status="confirmation_invalidated", reason="exception",
                    error_code="internal_error",
                )

            if fault == "none":
                response = await retire()
                assert _outcome(response) == "confirmation_invalidated"
            else:
                with pytest.raises(GovernedFinalizationError) as interrupted:
                    await retire()
                assert interrupted.value.result.error_code == "internal_error"
                claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
                assert not claim.cleanup_complete and claim.pending is pending
            assert h.store.op.state == "FAILED" and not h.store.op.send_started
            # Repair uses only the persisted operation after a reload, not the exception object.
            h.store.op = WorkflowOperation.model_validate_json(h.store.op.model_dump_json())
            assert h.operations.result(h.store.op, pending.trace_id).error_code == "internal_error"
            response = await _dispatch(chat)
            task = chat.runtime._task_store.records[pending.task_id]
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert task.status == claim.state == _outcome(response) == "confirmation_invalidated"
            assert task.error_code == claim.error_code == "internal_error"
            assert claim.cleanup_complete and claim.pending is None
            assert not chat.runtime._pending_workflows and chat.engine.resume_calls == 0
            assert not chat.runtime._session_memory.recall(pending.owner)
            assert h.store.transitions == ["FAILED"] and failures == (fault != "none")
            terminal = sorted((event.event_type, event.error_code)
                              for event in chat.trace.once.values())
            assert terminal == [
                ("evaluation_recorded", "internal_error"),
                ("task_confirmation_invalidated", "internal_error"),
            ]
            repaired = await chat.engine.finalize_governed_task(task_id=pending.task_id)
            assert repaired.error_code == "internal_error" and len(chat.trace.once) == 2
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["gate_binding", "workflow_contract"])
@pytest.mark.parametrize("fault", ["none", "task", "evaluation_after_write"])
def test_version_rejection_finishes_governed_confirmation_without_replay(boundary, fault):
    async def run():
        gate = ControllableVersionGate()
        chat, h, pending, session = await governed_chat(gate=gate)
        h.store.op = h.op.model_copy(update={"state": "WAITING_LOCAL_CONFIRM"})
        calls_before = len(chat.gateway.calls)
        original_update = chat.runtime._task_store.update_status
        original_once = chat.trace.record_event_once
        failures = 0
        injected = RuntimeError("synthetic version retirement interruption")

        async def owned(op):
            h.operations.lifecycle_owner(op)

        async def reject_contract(capability):
            assert capability.capability_id == pending.capability_id
            raise VersionBindingMismatchError("synthetic changed workflow contract")

        async def update(task_id, *args, **kwargs):
            nonlocal failures
            if fault == "task" and task_id == pending.task_id and not failures:
                failures += 1
                raise injected
            return await original_update(task_id, *args, **kwargs)

        async def once(event, key):
            nonlocal failures
            await original_once(event, key)
            if (
                fault == "evaluation_after_write"
                and event.event_type == "evaluation_recorded"
                and not failures
            ):
                failures += 1
                raise injected

        h.operations.owned = owned
        chat.runtime._task_store.update_status = update
        chat.trace.record_event_once = once
        if boundary == "gate_binding":
            gate.fail_bindings = True
        else:
            chat.engine.configure_governed_validation(reject_contract)
        token = authenticated_session.set(session)
        try:
            if fault == "none":
                assert _outcome(await _dispatch(chat)) == "action_version_conflict"
                assert chat.runtime._task_store.records[pending.task_id].status == "cancelled"
            else:
                with pytest.raises(GovernedFinalizationError) as interrupted:
                    await _dispatch(chat)
                assert interrupted.value.__cause__ is injected
                claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
                assert not claim.cleanup_complete and claim.pending is pending
            assert h.store.op.state == "CANCELLED" and not h.store.op.send_started
            assert h.store.op.confirmation_error_code is None
            # Restore only the durable operation; finalization must survive a reload.
            h.store.op = WorkflowOperation.model_validate_json(h.store.op.model_dump_json())
            response = await _dispatch(chat)
            task = chat.runtime._task_store.records[pending.task_id]
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert task.status == claim.state == _outcome(response) == "cancelled"
            assert task.error_code is claim.error_code is None
            assert claim.cleanup_complete and claim.pending is None
            assert h.store.transitions == ["CANCELLED"] and failures == (fault != "none")
            assert len(chat.gateway.calls) == calls_before
            assert chat.engine.resume_calls == (boundary == "workflow_contract")
            assert not chat.runtime._pending_workflows
            assert not chat.runtime._session_memory.recall(pending.owner)
            terminal = sorted((event.event_type, event.error_code)
                              for event in chat.trace.once.values())
            assert terminal == [
                ("evaluation_recorded", None), ("task_cancelled", None),
            ]
            assert _outcome(await _dispatch(chat)) == "cancelled"
            assert len(chat.trace.once) == 2 and h.store.transitions == ["CANCELLED"]
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize("entry", ["readable_view", "operation"])
@pytest.mark.parametrize("fault", ["none", "task", "evaluation_after_write"])
def test_first_expired_api_read_finishes_original_task_without_replay(entry, fault, monkeypatch):
    async def run():
        chat, h, pending, session = await governed_chat()
        gate = await chat.gate.get_request(pending.gate_request_id)
        h.store.op = h.op.model_copy(
            update={"state": "WAITING_LOCAL_CONFIRM", "expires_at": gate.expires_at}
        )

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return gate.expires_at + timedelta(seconds=1)

        async def owned(op):
            h.operations.lifecycle_owner(op)

        async def mapping(*args):
            return SimpleNamespace()

        async def build(**kwargs):
            return h.store.op.context

        h.operations.owned = owned
        h.operations.contexts = SimpleNamespace(store=SimpleNamespace(mapping=mapping), build=build)
        profile = SimpleNamespace(enabled=True, tenant_id=h.op.context.tenant_id,
                                  service_config_version=h.op.context.service_config_version,
                                  display_name="Synthetic")
        service = McpApiService(SimpleNamespace(configs={h.op.context.service_config_id: profile}),
                                h.operations, chat.engine)
        monkeypatch.setattr("app.mcp.operations.datetime", Clock)
        original_update = chat.runtime._task_store.update_status
        original_once = chat.trace.record_event_once
        failures = 0
        injected = RuntimeError("synthetic expiry read interruption")

        async def read():
            if entry == "readable_view":
                return await service.readable_view(h.store.op)
            return await service.operation(h.op.operation_id, session)

        foreign = replace(
            session, principal=session.principal.model_copy(update={"ai_user_id": "other"})
        )
        foreign_token = authenticated_session.set(foreign)
        try:
            with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                await read()
            assert h.store.transitions == [] and chat.trace.once == {}
            assert chat.runtime._task_store.records[pending.task_id].status == "waiting_user"
        finally:
            authenticated_session.reset(foreign_token)

        async def update(task_id, *args, **kwargs):
            nonlocal failures
            if fault == "task" and task_id == pending.task_id and not failures:
                failures += 1
                raise injected
            return await original_update(task_id, *args, **kwargs)

        async def once(event, key):
            nonlocal failures
            await original_once(event, key)
            if (
                fault == "evaluation_after_write"
                and event.event_type == "evaluation_recorded"
                and not failures
            ):
                failures += 1
                raise injected

        chat.runtime._task_store.update_status = update
        chat.trace.record_event_once = once
        token = authenticated_session.set(session)
        try:
            if fault == "none":
                with pytest.raises(McpFailure, match="mcp_confirmation_expired"):
                    await read()
                assert (
                    chat.runtime._task_store.records[pending.task_id].status
                    == "confirmation_invalidated"
                )
            else:
                with pytest.raises(GovernedFinalizationError) as interrupted:
                    await read()
                assert interrupted.value.__cause__ is injected
            assert h.store.op.state == "EXPIRED" and not h.store.op.send_started
            h.store.op = WorkflowOperation.model_validate_json(h.store.op.model_dump_json())
            await read()
            response = await _dispatch(chat)
            task = chat.runtime._task_store.records[pending.task_id]
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert task.status == claim.state == _outcome(response) == "confirmation_invalidated"
            assert task.error_code == claim.error_code == "confirm_required"
            assert claim.cleanup_complete and claim.pending is None
            assert h.store.transitions == ["EXPIRED"] and failures == (fault != "none")
            assert chat.engine.resume_calls == 0 and len(chat.gateway.calls) == 1
            assert not chat.runtime._pending_workflows
            terminal = sorted((event.event_type, event.error_code)
                              for event in chat.trace.once.values())
            assert terminal == [
                ("evaluation_recorded", "confirm_required"),
                ("task_confirmation_invalidated", "confirm_required"),
            ]
            await read()
            assert len(chat.trace.once) == 2 and h.store.transitions == ["EXPIRED"]
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize("mode,status,error_code", [
    ("throw", "failed", "internal_error"),
    ("return", "failed", "internal_error"),
    ("return", "failed", "mcp_contract_unconfirmed"),
    ("return", "denied", "policy_denied"),
    ("return", "binding_required", "identity_revoked"),
    ("return", "waiting_user", "confirm_required"),
    ("return", "timeout", "adapter_timeout"),
    ("return", "no_capability_found", "capability_not_found"),
    ("return", "failed", None),
    ("return", "completed", None),
])
@pytest.mark.parametrize("fault", ["none", "task", "guard_exit"])
def test_runtime_gateway_presend_failure_retires_ready_without_replay_send(
    mode, status, error_code, fault,
):
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op.model_copy(update={"state": "WAITING_LOCAL_CONFIRM"})
        calls = 0
        failures = 0
        expected_error = error_code or "internal_error"
        original_update = chat.runtime._task_store.update_status
        original_guard = h.store.execution_guard
        injected = RuntimeError("synthetic failure finalizer interruption")

        async def owned(op):
            h.operations.lifecycle_owner(op)

        async def execute(*args, **kwargs):
            nonlocal calls
            calls += 1
            assert h.store.op.state == "READY" and not h.store.op.send_started
            if mode == "throw":
                raise RuntimeError("synthetic gateway registry failure before send")
            return ExecutionResult(status=status, error_code=error_code, trace_id=pending.trace_id)

        async def update(task_id, *args, **kwargs):
            nonlocal failures
            if fault == "task" and task_id == pending.task_id and not failures:
                failures += 1
                raise injected
            return await original_update(task_id, *args, **kwargs)

        @asynccontextmanager
        async def guard(op):
            nonlocal failures
            async with original_guard(op):
                yield
            if fault == "guard_exit" and h.store.op.state == "FAILED" and not failures:
                failures += 1
                raise injected

        h.operations.owned = owned
        h.operations.gateway = SimpleNamespace(execute_capability=execute)
        chat.runtime._task_store.update_status = update
        h.store.execution_guard = guard
        token = authenticated_session.set(session)
        try:
            if fault != "none":
                expected_exception = (
                    RuntimeError if mode == "throw" and fault == "guard_exit"
                    else GovernedFinalizationError
                )
                with pytest.raises(expected_exception) as interrupted:
                    await _dispatch(chat)
                assert type(interrupted.value) is expected_exception
                assert (
                    interrupted.value is injected if expected_exception is RuntimeError
                    else interrupted.value.__cause__ is injected
                )
                assert failures == 1
            response = await _dispatch(chat)
            assert calls == chat.engine.resume_calls == 1
            assert _outcome(response) == "confirmation_invalidated"
            assert h.store.op.state == "FAILED" and not h.store.op.send_started
            task = chat.runtime._task_store.records[pending.task_id]
            assert task.status == "confirmation_invalidated" and task.error_code == expected_error
            h.store.op = WorkflowOperation.model_validate_json(h.store.op.model_dump_json())
            assert h.operations.result(h.store.op, pending.trace_id).error_code == expected_error
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert claim.state == "confirmation_invalidated" and claim.error_code == expected_error
            terminal = sorted((event.event_type, event.error_code)
                              for event in chat.trace.once.values())
            expected_events = [
                ("evaluation_recorded", expected_error),
                ("task_confirmation_invalidated", expected_error),
            ]
            if mode == "return" and fault != "guard_exit":
                expected_events.append(("response_envelope_created", None))
            assert terminal == sorted(expected_events)
            replay = await _dispatch(chat)
            assert _outcome(replay) == "confirmation_invalidated"
            assert calls == chat.engine.resume_calls == 1
            assert not chat.runtime._pending_workflows
            assert not chat.runtime._session_memory.recall(pending.owner)
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize("state", ["VERIFIED_SUCCESS", "FAILED", "CANCELLED", "EXPIRED"])
@pytest.mark.parametrize("fault", [
    "first_read", "response_trace", "response_after_write", "task", "envelope", "cancel_wait",
    "guard_exit", "guard_cancel",
])
def test_durable_terminal_survives_entire_runtime_completion(state, fault):
    async def run():
        chat, h, pending, session = await governed_chat()
        h.store.op = h.op = h.op.model_copy(update={"state": "WAITING_LOCAL_CONFIRM"})
        sends = failures = 0
        resumed = False
        original_resume = h.operations.resume
        original_guard = h.store.execution_guard
        original_read = h.store.by_task
        original_step = chat.trace.record_step
        original_once = chat.trace.record_event_once
        original_update = chat.runtime._task_store.update_status
        original_envelope = chat.runtime._build_envelope

        async def resume(*args, **kwargs):
            nonlocal resumed
            result = await original_resume(*args, **kwargs)
            resumed = True
            return result

        @asynccontextmanager
        async def guard(op):
            nonlocal failures
            async with original_guard(op):
                yield
            if (
                fault in {"guard_exit", "guard_cancel"}
                and not failures and h.store.op.state == state
            ):
                failures += 1
                if fault == "guard_cancel":
                    raise asyncio.CancelledError("synthetic guard exit cancellation")
                raise RuntimeError("synthetic guard exit failure")

        async def owned(op):
            h.operations.lifecycle_owner(op)
            if state in {"FAILED", "CANCELLED"}:
                h.store.op = h.store.op.model_copy(update={"state": state})
            if state == "EXPIRED":
                h.store.op = h.store.op.model_copy(
                    update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )

        async def execute(*args, **kwargs):
            nonlocal sends
            sends += 1
            await h.store.transition(h.store.op, state="SENDING")
            return ExecutionResult(
                status="completed", trace_id=pending.trace_id,
                mcp_outcome=McpValidatedOutcome(state="VERIFIED_SUCCESS", persistence={}),
            )

        def fail_once():
            nonlocal failures
            if resumed and not failures and h.store.op.state == state:
                failures += 1
                if fault == "cancel_wait":
                    raise asyncio.CancelledError("synthetic completion cancellation")
                raise RuntimeError("synthetic completion I/O failure")

        async def read(task_id):
            if fault in {"first_read", "cancel_wait"}:
                fail_once()
            return await original_read(task_id)

        async def step(trace_id, task_id, *args, **kwargs):
            if fault == "response_trace" and task_id == pending.task_id and kwargs.get(
                "event_type"
            ) == "response_envelope_created":
                fail_once()
            return await original_step(trace_id, task_id, *args, **kwargs)

        async def update(task_id, *args, **kwargs):
            if fault == "task" and task_id == pending.task_id:
                fail_once()
            return await original_update(task_id, *args, **kwargs)

        async def once(event, key):
            await original_once(event, key)
            if fault == "response_after_write" and event.event_type == "response_envelope_created":
                fail_once()

        def envelope(*args, **kwargs):
            if fault == "envelope":
                fail_once()
            return original_envelope(*args, **kwargs)

        h.operations.owned = owned
        h.operations.gateway = SimpleNamespace(execute_capability=execute)
        h.operations.resume = resume
        h.store.execution_guard = guard
        h.store.by_task = read
        chat.trace.record_step = step
        chat.trace.record_event_once = once
        chat.runtime._task_store.update_status = update
        chat.runtime._build_envelope = envelope
        expected = {
            "VERIFIED_SUCCESS": "completed", "FAILED": "completed", "CANCELLED": "cancelled",
            "EXPIRED": "confirmation_invalidated",
        }[state]
        token = authenticated_session.set(session)
        try:
            with pytest.raises(
                asyncio.CancelledError if fault in {"cancel_wait", "guard_cancel"}
                else GovernedFinalizationError
            ):
                await _dispatch(chat)
            assert failures == 1 and h.store.op.state == state
            claim = next(iter(chat.runtime._claimed_pending_confirmations.values()))
            assert claim.state == expected and not claim.cleanup_complete
            assert claim.pending is pending
            assert list(chat.runtime._pending_workflows.values()) == [pending]
            assert len(chat.runtime._session_memory.recall(pending.owner)) == (
                1 if state == "VERIFIED_SUCCESS" else 0
            )
            response = await _dispatch(chat)
            assert _outcome(response) == (
                "action_already_claimed" if expected == "completed" else expected
            )
            if state == "VERIFIED_SUCCESS":
                assert "nothing was executed" not in response.model_dump_json()
                assert "本次未执行" not in response.model_dump_json()
            record = chat.runtime._task_store.records[pending.task_id]
            assert record.status == ("failed" if state == "FAILED" else expected)
            if state == "FAILED":
                assert record.error_code == "policy_denied"
            assert claim.cleanup_complete and claim.pending is None
            assert not chat.runtime._pending_workflows
            assert sends == (1 if state == "VERIFIED_SUCCESS" else 0)
            assert chat.engine.resume_calls == 1
            assert len(chat.runtime._session_memory.recall(pending.owner)) == (
                1 if state == "VERIFIED_SUCCESS" else 0
            )
            events = [
                item["event_type"] for item in chat.trace.steps
                if item["task_id"] == pending.task_id
            ]
            assert events.count("task_completed" if state == "VERIFIED_SUCCESS" else (
                "task_failed" if state == "FAILED" else
                "task_cancelled" if state == "CANCELLED" else "task_confirmation_invalidated"
            )) == 1
            assert events.count("evaluation_recorded") == 1
            # One initial preview response and exactly one terminal response trace.
            assert events.count("response_envelope_created") == (
                1 if fault in {"envelope", "guard_exit", "guard_cancel"} else 2
            )
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


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
            assert sorted(event.event_type for event in chat.trace.once.values()) == [
                "evaluation_recorded", "response_envelope_created", "task_completed",
            ]
            assert not chat.runtime._pending_workflows
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
