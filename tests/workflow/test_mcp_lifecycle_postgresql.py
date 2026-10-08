"""Real PostgreSQL regressions for MCP confirmation and durable completion."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.mcp import McpApiService, ResumeRequest
from app.db.session import make_async_engine
from app.event_loop import make_event_loop
from app.infra.adapters.business_mcp.catalog import workflow_definitions
from app.infra.human_gate.postgresql import PostgreSQLHumanGate
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.persistence.mcp import schema
from app.infra.persistence.mcp.repository import PostgreSQLMcpStore
from app.infra.persistence.mcp.workflow_repository import PostgreSQLWorkflowStore
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.workflow.engine_adapter import WorkflowEngineAdapter
from app.mcp.connections import McpExecutionContextFactory
from app.mcp.contracts import OutputContract
from app.mcp.models import McpFailure, ServiceConfig
from app.mcp.operations import GovernedOperations
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.capability_gateway import RequestOrgContext
from app.ports.human_gate import HumanGateDecisionRecord, build_task_version_binding_manifest
from app.ports.task_store import TaskRecord
from app.ports.workflow_engine import GovernedFinalizationError
from app.workflow.engine import WorkflowEngine
from tests.infra.mcp.test_transport import serving
from tests.infra.persistence.test_mcp_store import Revocations
from tests.workflow.test_mcp_recovery import BusinessPeer, harness


def _service(h, profile):
    return McpApiService(
        SimpleNamespace(configs={profile.service_config_id: profile}),
        h["operations"],
        WorkflowEngineAdapter(h["workflow"]),
    )


def _body(service, op):
    return ResumeRequest(
        action="confirm",
        expected_revision=op.revision,
        preview_digest=service.view(op).preview_digest,
    )


async def _terminal_rows(h):
    async with h["store"].sessions() as session:
        return (
            await session.execute(
                sa.text(
                    "SELECT event_id, event_type, created_at FROM trace_events "
                    "WHERE task_id = :task AND event_type IN "
                    "('task_completed', 'task_confirmation_invalidated', 'evaluation_recorded') "
                    "ORDER BY event_id"
                ),
                {"task": h["task"]},
            )
        ).all()


@pytest.mark.parametrize("mode", ["decision_commit_crash", "concurrent_insert"])
def test_api_decision_replay_uses_committed_record(migrated_database_url, monkeypatch, mode):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url,
                profile,
                "talk_preparation_save",
                durable=True,
                trace_writer=True,
            ) as h:
                service = _service(h, profile)
                op, body = h["op"], _body(service, h["op"])
                gate = await h["workflows"].confirmation(op)
                resume = h["workflow"].resume
                calls = 0

                async def counted_resume(**kwargs):
                    nonlocal calls
                    calls += 1
                    if mode == "decision_commit_crash" and calls == 1:
                        raise RuntimeError("injected_after_decision_commit")
                    return await resume(**kwargs)

                monkeypatch.setattr(h["workflow"], "resume", counted_resume)
                if mode == "decision_commit_crash":
                    with pytest.raises(RuntimeError, match="^injected_after_decision_commit$"):
                        await service.resume(op, body)
                    first = await h["gates"].get_decision(gate.request_id)
                    assert first is not None and first.decision == "confirmed"
                    assert peer.effects == 0
                    assert (await service.resume(op, body)).state == "VERIFIED_SUCCESS"
                    assert await h["gates"].get_decision(gate.request_id) == first
                else:
                    get_decision = h["gates"].get_decision
                    arrived = 0
                    release = asyncio.Event()

                    async def initial_read(request_id):
                        nonlocal arrived
                        decision = await get_decision(request_id)
                        if request_id == gate.request_id and decision is None and arrived < 2:
                            arrived += 1
                            if arrived == 2:
                                release.set()
                            await asyncio.wait_for(release.wait(), 5)
                        return decision

                    monkeypatch.setattr(h["gates"], "get_decision", initial_read)
                    results = await asyncio.gather(
                        service.resume(op, body), service.resume(op, body), return_exceptions=True
                    )
                    assert arrived == 2
                    assert any(
                        getattr(value, "state", None) == "VERIFIED_SUCCESS" for value in results
                    )
                    assert all(
                        not isinstance(value, BaseException)
                        or isinstance(value, McpFailure)
                        and value.code in {"mcp_operation_busy", "mcp_operation_conflict"}
                        for value in results
                    )
                    first = await get_decision(gate.request_id)
                    assert first is not None and first.decision == "confirmed"
                    assert calls == 2
                    async with h["store"].sessions() as session:
                        count = (
                            await session.execute(
                                sa.text(
                                    "SELECT count(*) FROM human_gate_requests "
                                    "WHERE request_id=:request AND decided_at=:decided"
                                ),
                                {"request": gate.request_id, "decided": first.decided_at},
                            )
                        ).scalar_one()
                    assert count == 1
                stored = await h["workflows"].by_task(h["task"])
                assert stored.state == "VERIFIED_SUCCESS"
                assert stored.revision == op.revision + 3
                assert peer.effects == 1
                assert (await h["tasks"].get_task(h["task"])).status == "completed"
                with pytest.raises(McpFailure, match="^mcp_operation_conflict$"):
                    await service.resume(stored, body)
                assert peer.effects == 1

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize(
    "fault",
    [
        "task_after_commit",
        "terminal_after_commit",
        "evaluation_before_write",
        "evaluation_after_commit",
    ],
)
def test_real_trace_completion_retries_without_repeating_business(
    migrated_database_url,
    monkeypatch,
    fault,
):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url,
                profile,
                "talk_preparation_save",
                durable=True,
                trace_writer=True,
            ) as h:
                service = _service(h, profile)
                update, record = h["tasks"].update_status, h["trace"].record_event_once
                failures = 0

                async def failed_update(*args, **kwargs):
                    nonlocal failures
                    result = await update(*args, **kwargs)
                    if fault == "task_after_commit" and failures == 0:
                        failures += 1
                        raise RuntimeError("injected_task_commit_ack")
                    return result

                async def failed_record(event, key):
                    nonlocal failures
                    match = (
                        fault == "terminal_after_commit"
                        and event.event_type == "task_completed"
                        or fault.startswith("evaluation_")
                        and event.event_type == "evaluation_recorded"
                    )
                    if match and failures == 0 and fault == "evaluation_before_write":
                        failures += 1
                        raise RuntimeError("injected_trace_before_commit")
                    await record(event, key)
                    if match and failures == 0:
                        failures += 1
                        raise RuntimeError("injected_trace_commit_ack")

                monkeypatch.setattr(h["tasks"], "update_status", failed_update)
                monkeypatch.setattr(h["trace"], "record_event_once", failed_record)
                with pytest.raises(GovernedFinalizationError) as error:
                    await service.resume(h["op"], _body(service, h["op"]))
                assert error.value.result.output["state"] == "VERIFIED_SUCCESS"
                assert failures == 1 and peer.effects == 1
                assert (await h["workflows"].by_task(h["task"])).state == "VERIFIED_SUCCESS"
                committed = {row.event_id: row.created_at for row in await _terminal_rows(h)}
                for _ in range(2):
                    repaired = await service.operation(
                        h["op"].operation_id, authenticated_session.get()
                    )
                    assert repaired.state == "VERIFIED_SUCCESS"
                rows = await _terminal_rows(h)
                assert [row.event_type for row in rows].count("task_completed") == 1
                assert [row.event_type for row in rows].count("evaluation_recorded") == 1
                assert all(
                    dict((row.event_id, row.created_at) for row in rows)[key] == value
                    for key, value in committed.items()
                )
                assert (await h["tasks"].get_task(h["task"])).status == "completed"
                assert peer.effects == 1

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("expired", [False, True])
def test_gate_survives_checkpoint_rollback_with_original_deadline(
    migrated_database_url,
    monkeypatch,
    expired,
):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url, profile, "talk_preparation_save", durable=True
            ) as h:
                session = authenticated_session.get()
                token = authenticated_session.set(
                    replace(session, expires_at=datetime.now(UTC) + timedelta(hours=1))
                )
                try:
                    original_op = h["op"]
                    created = []
                    create = h["gates"].create_request

                    async def capture(request):
                        value = await create(request)
                        created.append(value)
                        return value

                    monkeypatch.setattr(h["gates"], "create_request", capture)
                    execute = AsyncSession.execute
                    failures = 0

                    async def fail_checkpoint(db_session, statement, *args, **kwargs):
                        nonlocal failures
                        if (
                            isinstance(statement, sa.sql.dml.Update)
                            and statement.table.name == schema.workflow_runs.name
                            and failures == 0
                        ):
                            failures += 1
                            raise RuntimeError("injected_checkpoint_write")
                        return await execute(db_session, statement, *args, **kwargs)

                    monkeypatch.setattr(AsyncSession, "execute", fail_checkpoint)
                    with pytest.raises(RuntimeError, match="^injected_checkpoint_write$"):
                        await h["operations"]._renew_confirmation(
                            original_op, original_op.context, "pg-test-policy"
                        )
                    monkeypatch.setattr(AsyncSession, "execute", execute)
                    assert failures == 1 and len(created) == 1
                    gate = created[0]
                    assert await h["gates"].get_request(gate.request_id) == gate
                    assert await h["workflows"].by_task(h["task"]) == original_op
                    if expired:
                        future = gate.expires_at + timedelta(seconds=1)

                        class Clock(datetime):
                            @classmethod
                            def now(cls, tz=None):
                                return future

                        monkeypatch.setattr("app.mcp.operations.datetime", Clock)
                        with pytest.raises(McpFailure, match="^mcp_confirmation_expired$"):
                            await h["operations"]._renew_confirmation(
                                original_op, original_op.context, "pg-test-policy"
                            )
                        assert (await h["workflows"].by_task(h["task"])).state == "EXPIRED"
                    else:
                        renewed = await h["operations"]._renew_confirmation(
                            original_op, original_op.context, "pg-test-policy"
                        )
                        assert renewed.gate_request_id == gate.request_id
                        assert renewed.expires_at == gate.expires_at
                        assert await h["workflows"].by_task(h["task"]) == renewed
                    assert await h["gates"].get_request(gate.request_id) == gate
                    assert len(created) == 1 and peer.effects == 0
                finally:
                    authenticated_session.reset(token)

        asyncio.run(run(), loop_factory=make_event_loop)


def test_real_operation_cas_has_one_winner(migrated_database_url):
    with serving(BusinessPeer("2025-11-25")) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url, profile, "talk_preparation_save", durable=True
            ) as h:
                op = h["op"]
                attempts = [uuid4().hex, uuid4().hex]
                results = await asyncio.gather(
                    *[
                        h["workflows"].transition(
                            op,
                            state="READY",
                            gate_request_id=op.gate_request_id,
                            attempt_id=attempt,
                        )
                        for attempt in attempts
                    ],
                    return_exceptions=True,
                )
                winners = [value for value in results if not isinstance(value, BaseException)]
                losers = [value for value in results if isinstance(value, BaseException)]
                assert len(winners) == len(losers) == 1
                assert (
                    isinstance(losers[0], McpFailure) and losers[0].code == "mcp_operation_conflict"
                )
                stored = await h["workflows"].by_task(h["task"])
                assert stored == winners[0]
                assert stored.state == "READY" and stored.revision == op.revision + 1
                assert stored.attempt_id in attempts and not stored.send_started

        asyncio.run(run(), loop_factory=make_event_loop)


def _public_contract():
    return OutputContract(
        "synthetic-pg",
        {
            "type": "object",
            "properties": {
                "artifactId": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                "payloadHash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "internal": {"type": "integer"},
            },
            "required": ["artifactId", "payloadHash"],
            "additionalProperties": False,
        },
        ("artifactId", "payloadHash"),
        ("artifactId", "payloadHash"),
        ("artifactId", "payloadHash", "internal"),
        "isolated-test-only",
        postcondition=lambda value: value.get("internal") == 7,
    )


def read_result_in_fresh_process(payload):
    async def run():
        engine = make_async_engine(os.environ["DATABASE_URL"])
        assert (engine.url.host, engine.url.port, engine.url.database) == (
            "127.0.0.1",
            15432,
            "eternalai_test",
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgreSQLMcpStore(sessions, b"s" * 32, Revocations())
        workflows = PostgreSQLWorkflowStore(store)
        gates = PostgreSQLHumanGate(sessions)
        registry = PostgreSQLCapabilityRegistry(sessions)
        profile = ServiceConfig.model_validate(payload["profile"])
        op = await workflows.load(
            payload["operation_id"], tenant_id="default", user_id="synthetic-user"
        )
        assert op is not None
        token = authenticated_session.set(
            AuthenticatedSessionContext(
                principal=Principal(
                    ai_user_id="synthetic-user",
                    display_name="Synthetic",
                    roles=(),
                    org_ctx=PrincipalOrgContext(tenant_id="default"),
                ),
                fingerprint=bytes.fromhex(op.context.login_session_fingerprint),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )

        class NoOutbound:
            async def execute_capability(self, *args, **kwargs):
                raise AssertionError("read_probe_must_not_execute")

        try:
            definitions = workflow_definitions(profile)
            manifests = {key: value.policy for key, value in definitions.items()}
            policy = MinimalPolicyGuard(
                governed_outer_ids=manifests,
                governed_leaf_ids=[item.leaf_capability_id for item in manifests.values()],
            )
            contexts = McpExecutionContextFactory(store, workflows, gates)
            operations = GovernedOperations(
                workflows, contexts, gates, NoOutbound(), policy, manifests, registry=registry
            )
            workflow = WorkflowEngine(
                definitions=definitions,
                capability_registry=registry,
                gateway=NoOutbound(),
                task_store=PostgreSQLTaskStore(sessions),
                trace_port=PostgreSQLTraceWriter(sessions),
                human_gate_port=gates,
                governed_operations=operations,
            )
            service = McpApiService(
                SimpleNamespace(configs={profile.service_config_id: profile}),
                operations,
                WorkflowEngineAdapter(workflow),
            )
            view = await service.readable_view(op)
            print(
                json.dumps(
                    {
                        "api": view.result,
                        "workflow": operations.result(op, "synthetic-read").output["result"],
                    }
                )
            )
        finally:
            authenticated_session.reset(token)
            await engine.dispose()

    asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("legacy", [False, True])
def test_safe_result_survives_process_restart_and_legacy_records(migrated_database_url, legacy):
    data = {"artifactId": "a" * 32, "payloadHash": "b" * 64, "internal": 7}

    class PreviewPeer(BusinessPeer):
        def reply(self, request):
            value = super().reply(request)
            if (
                request["method"] == "tools/call"
                and request["params"]["name"] == "clothing_plan_preview"
            ):
                value["result"] = {
                    "structuredContent": data,
                    "content": [{"type": "text", "text": json.dumps(data)}],
                }
            return value

    peer = PreviewPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url,
                profile,
                "clothing_plan_preview",
                durable=True,
                trace_writer=True,
                output_contracts={"clothing_plan_preview": _public_contract()},
            ) as h:
                service = _service(h, profile)
                view = await service.resume(h["op"], _body(service, h["op"]))
                expected = {"artifactId": data["artifactId"], "payloadHash": data["payloadHash"]}
                assert view.result == expected and peer.effects == 1
                op = await h["workflows"].by_task(h["task"])
                assert op.safe_output == data and op.public_result == expected
                if legacy:
                    payload = op.model_dump(mode="json")
                    payload.pop("public_result")
                    async with h["store"].sessions.begin() as session:
                        await session.execute(
                            sa.update(schema.operations)
                            .where(
                                schema.operations.c.operation_id == op.operation_id,
                                schema.operations.c.tenant_id == "default",
                            )
                            .values(
                                encrypted_payload=h["store"].encrypt(
                                    payload, h["workflows"]._aad(op)
                                )
                            )
                        )
                    expected = None
                code = (
                    "import json,sys; from tests.workflow.test_mcp_lifecycle_postgresql import "
                    "read_result_in_fresh_process; "
                    "read_result_in_fresh_process(json.loads(sys.stdin.read()))"
                )
                child = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-B", "-c", code],
                    input=json.dumps(
                        {
                            "operation_id": op.operation_id,
                            "profile": profile.model_dump(mode="json"),
                        }
                    ),
                    text=True,
                    capture_output=True,
                    timeout=30,
                    cwd=str(Path(__file__).resolve().parents[2]),
                )
                assert child.returncode == 0
                reloaded = json.loads(child.stdout)
                assert reloaded == {"api": expected, "workflow": expected}
                assert peer.effects == 1
                if not legacy:
                    task, trace = uuid4().hex, uuid4().hex
                    submit_id = f"business.{profile.service_config_id}.clothing_plan_submit"
                    spec = await h["registry"].get(submit_id)
                    assert spec is not None
                    await h["tasks"].create_task(
                        TaskRecord(
                            task_id=task,
                            session_id="synthetic-chat",
                            ai_user_id="synthetic-user",
                            tenant_id="default",
                            status="running",
                            trace_id=trace,
                        )
                    )
                    bindings = await h["workflow"].version_bindings(workflow_capability=spec)
                    await h["gates"].bind_task(
                        build_task_version_binding_manifest(
                            task_id=task, bindings=bindings.bindings, locked_at=datetime.now(UTC)
                        )
                    )
                    waiting = await h["workflow"].execute(
                        workflow_id=submit_id,
                        expected_version=spec.version,
                        workflow_capability=spec,
                        task_id=task,
                        session_id="synthetic-chat",
                        ai_user_id="synthetic-user",
                        initial_input=reloaded["api"],
                        request_context=RequestOrgContext(request_id=trace, tenant_id="default"),
                    )
                    assert waiting.status == "waiting_confirm" and peer.effects == 1
                    submit = await h["workflows"].by_task(task)
                    assert (await service.resume(submit, _body(service, submit))).state == (
                        "VERIFIED_SUCCESS"
                    )
                    result_arguments = {"artifactId": reloaded["api"]["artifactId"]}
                    result = await h["gateway"].execute_capability(
                        task,
                        "synthetic-chat",
                        "synthetic-user",
                        f"business.{profile.service_config_id}.clothing_result_get",
                        result_arguments,
                        RequestOrgContext(request_id=trace, tenant_id="default"),
                    )
                    assert result.status == "completed" and peer.effects == 3
                    calls = [
                        body["params"]
                        for _, _, body in peer.calls
                        if body["method"] == "tools/call"
                    ]
                    assert [(call["name"], call["arguments"]) for call in calls[-2:]] == [
                        ("clothing_plan_submit", reloaded["api"]),
                        ("clothing_result_get", result_arguments),
                    ]

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("change", ["none", "disconnect", "logout", "rebind"])
def test_real_revocation_during_final_hook_prevents_dispatch(
    migrated_database_url,
    change,
):
    from hashlib import sha256

    from app.infra.auth.session_revocations import PostgreSQLSessionRevocationStore
    from app.infra.mcp.driver import McpDriver
    from app.mcp.contracts import input_digest, safety_digest
    from tests.infra.persistence.test_mcp_store import bind

    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url, profile, "talk_preparation_save", durable=True
            ) as h:
                fingerprint = sha256(uuid4().bytes).digest()
                await bind(h["store"], profile, fingerprint=fingerprint)
                session = authenticated_session.get()
                marker = authenticated_session.set(replace(session, fingerprint=fingerprint))
                revocations = PostgreSQLSessionRevocationStore(h["store"].sessions)
                h["store"]._revocations = revocations
                try:
                    mapping = next(
                        item for item in h["mappings"] if item.remote_tool == "business_context_get"
                    )
                    context = await h["contexts"].build(
                        task_id=h["task"],
                        chat_session_id="synthetic-chat",
                        user_id="synthetic-user",
                        tenant_id="default",
                        mapping=mapping,
                    )
                    entered, release = asyncio.Event(), asyncio.Event()

                    async def before_send():
                        if any(call[2]["method"] == "tools/list" for call in peer.calls):
                            entered.set()
                            await release.wait()

                    attempt = asyncio.create_task(
                        McpDriver({profile.service_config_id: profile}, h["store"]).call(
                            context,
                            "business_context_get",
                            {},
                            input_digest=input_digest("business_context_get"),
                            safety_digest=safety_digest("business_context_get"),
                            write=False,
                            before_send=before_send,
                        )
                    )
                    try:
                        await asyncio.wait_for(entered.wait(), 5)
                        if change == "disconnect":
                            assert await h["store"].disconnect(
                                tenant_id="default",
                                user_id="synthetic-user",
                                connection_id=context.connection_id,
                            )
                        elif change == "logout":
                            await revocations.revoke(fingerprint, expires_at=session.expires_at)
                            assert await revocations.is_revoked(fingerprint)
                        elif change == "rebind":
                            await bind(
                                h["store"], profile, fingerprint=sha256(uuid4().bytes).digest()
                            )
                        release.set()
                        if change == "none":
                            await attempt
                            assert peer.effects == 1
                        else:
                            with pytest.raises(McpFailure, match="^mcp_authorization_invalid$"):
                                await attempt
                            assert peer.effects == 0
                        calls = [call for call in peer.calls if call[2]["method"] == "tools/call"]
                        assert len(calls) == (1 if change == "none" else 0)
                    finally:
                        release.set()
                        if not attempt.done():
                            attempt.cancel()
                            await asyncio.gather(attempt, return_exceptions=True)
                finally:
                    authenticated_session.reset(marker)
                    async with h["store"].sessions.begin() as db_session:
                        await db_session.execute(
                            sa.text(
                                "DELETE FROM auth_session_revocations "
                                "WHERE token_fingerprint=:fingerprint"
                            ),
                            {"fingerprint": fingerprint},
                        )
                    assert not await revocations.is_revoked(fingerprint)

        asyncio.run(run(), loop_factory=make_event_loop)


def test_runtime_response_trace_commit_retry_is_idempotent(migrated_database_url, monkeypatch):
    from app.infra.observability.postgresql_trace import TraceSanitizationError
    from app.memory.session_memory import SessionMemoryKey
    from tests.runtime.test_runtime_user_action import _build_harness

    with serving(BusinessPeer("2025-11-25")) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url,
                profile,
                "talk_preparation_save",
                durable=True,
                trace_writer=True,
            ) as h:
                chat = await _build_harness()
                template = next(iter(chat.runtime._pending_workflows.values()))
                pending = replace(
                    template,
                    task_id=h["task"],
                    trace_id=h["trace_id"],
                    owner=SessionMemoryKey("default", "synthetic-chat", "synthetic-user"),
                    action_digest=h["op"].action_digest,
                    gate_request_id=h["op"].gate_request_id,
                )
                chat.runtime._trace_port = h["trace"]
                record = h["trace"].record_event_once
                captured = []

                async def lost_ack(event, key):
                    await record(event, key)
                    captured.append((event, key))
                    if len(captured) == 1:
                        raise RuntimeError("injected_response_commit_ack")

                monkeypatch.setattr(h["trace"], "record_event_once", lost_ack)
                with pytest.raises(RuntimeError, match="^injected_response_commit_ack$"):
                    await chat.runtime._record_governed_response(pending)
                async with h["store"].sessions() as session:
                    first = (
                        await session.execute(
                            sa.text(
                                "SELECT event_id, created_at FROM trace_events WHERE task_id=:task "
                                "AND event_type='response_envelope_created'"
                            ),
                            {"task": h["task"]},
                        )
                    ).one()
                await asyncio.gather(
                    chat.runtime._record_governed_response(pending),
                    chat.runtime._record_governed_response(pending),
                )
                event, key = captured[0]
                with pytest.raises(TraceSanitizationError, match="^trace idempotency conflict$"):
                    await record(event.model_copy(update={"ai_user_id": "other-user"}), key)
                async with h["store"].sessions() as session:
                    rows = (
                        await session.execute(
                            sa.text(
                                "SELECT event_id, created_at FROM trace_events WHERE task_id=:task "
                                "AND event_type='response_envelope_created'"
                            ),
                            {"task": h["task"]},
                        )
                    ).all()
                assert rows == [first]
                assert len(captured) == 3

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("foreign_field", ["ai_user_id", "tenant_id"])
def test_owner_cleanup_and_old_generation_preserve_new_success(
    migrated_database_url,
    monkeypatch,
    foreign_field,
):
    from app.memory.session_memory import SessionMemoryKey
    from tests.runtime.test_runtime_user_action import _build_harness

    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url,
                profile,
                "talk_preparation_save",
                durable=True,
                trace_writer=True,
            ) as h:
                old = h["op"]
                session = authenticated_session.get()
                chat = await _build_harness()
                template = next(iter(chat.runtime._pending_workflows.values()))
                owner = SessionMemoryKey("default", "synthetic-chat", "synthetic-user")
                other = replace(owner, **{foreign_field: "other"})
                pending = replace(
                    template,
                    task_id=h["task"],
                    trace_id=h["trace_id"],
                    owner=owner,
                    action_digest=old.action_digest,
                    gate_request_id=old.gate_request_id,
                    monotonic_deadline=0,
                )
                chat.runtime._pending_workflows = {
                    ("default", "synthetic-chat", "synthetic-user"): pending
                }
                chat.runtime._claimed_pending_confirmations = {}
                chat.runtime._workflow_engine = WorkflowEngineAdapter(h["workflow"])
                chat.runtime._task_store = h["tasks"]
                chat.runtime._trace_port = h["trace"]
                execute = AsyncSession.execute
                queries = 0

                async def observe(db_session, *args, **kwargs):
                    nonlocal queries
                    queries += 1
                    return await execute(db_session, *args, **kwargs)

                monkeypatch.setattr(AsyncSession, "execute", observe)
                foreign_principal = session.principal.model_copy(
                    update={
                        "ai_user_id": other.ai_user_id,
                        "org_ctx": session.principal.org_ctx.model_copy(
                            update={"tenant_id": other.tenant_id}
                        ),
                    }
                )
                foreign_marker = authenticated_session.set(
                    replace(session, principal=foreign_principal)
                )
                try:
                    await chat.runtime._expire_pending_confirmations(other)
                finally:
                    authenticated_session.reset(foreign_marker)
                monkeypatch.setattr(AsyncSession, "execute", execute)
                assert queries == 0 and list(chat.runtime._pending_workflows.values()) == [pending]
                future = old.expires_at + timedelta(seconds=1)

                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return future

                monkeypatch.setattr("app.mcp.operations.datetime", Clock)
                marker = authenticated_session.set(
                    replace(session, expires_at=future + timedelta(hours=1))
                )
                try:
                    original_record = h["trace"].record_event_once
                    faults = 0

                    async def lost_terminal_ack(event, key):
                        nonlocal faults
                        await original_record(event, key)
                        if event.event_type == "task_confirmation_invalidated" and faults == 0:
                            faults += 1
                            raise RuntimeError("injected_expiry_commit_ack")

                    monkeypatch.setattr(h["trace"], "record_event_once", lost_terminal_ack)
                    with pytest.raises(GovernedFinalizationError):
                        await h["workflow"].retire_owned_confirmation(
                            h["task"], old.action_digest, old.gate_request_id, "expired"
                        )
                    expired = await h["workflows"].by_task(h["task"])
                    assert expired.state == "EXPIRED" and faults == 1
                    renewed = await h["operations"]._renew_confirmation(
                        expired, expired.context, "pg-new-generation"
                    )
                    assert renewed.action_digest != old.action_digest
                    assert renewed.gate_request_id != old.gate_request_id
                    with pytest.raises(McpFailure, match="^mcp_workflow_authorization_invalid$"):
                        await h["workflow"].resume(
                            task_id=h["task"],
                            confirmed=True,
                            expected_action_digest=old.action_digest,
                        )
                    assert peer.effects == 0
                    gate = await h["gates"].get_request(renewed.gate_request_id)
                    await h["gates"].record_decision(
                        HumanGateDecisionRecord(
                            request_id=gate.request_id,
                            task_id=h["task"],
                            decided_by_ai_user_id="synthetic-user",
                            decided_session_id="synthetic-chat",
                            decided_tenant_id="default",
                            decision="confirmed",
                            request_digest=gate.request_digest,
                            binding_manifest_digest=gate.binding_manifest_digest,
                            decided_at=gate.requested_at,
                        )
                    )
                    result = await h["workflow"].resume(
                        task_id=h["task"],
                        confirmed=True,
                        expected_action_digest=renewed.action_digest,
                    )
                    assert result.output["state"] == "VERIFIED_SUCCESS" and peer.effects == 1
                    await h["workflow"].finalize_governed_task(task_id=h["task"])
                    before = await h["workflows"].by_task(h["task"])
                    for _ in range(2):
                        assert await h["workflow"].retire_owned_confirmation(
                            h["task"], old.action_digest, old.gate_request_id, "expired"
                        )
                    assert await h["workflows"].by_task(h["task"]) == before
                    assert (await h["tasks"].get_task(h["task"])).status == "completed"
                    rows = await _terminal_rows(h)
                    assert [row.event_type for row in rows].count("task_completed") == 1
                    assert [row.event_type for row in rows].count(
                        "task_confirmation_invalidated"
                    ) == 1
                    assert [row.event_type for row in rows].count("evaluation_recorded") == 2
                    assert peer.effects == 1
                finally:
                    authenticated_session.reset(marker)

        asyncio.run(run(), loop_factory=make_event_loop)
