"""Real PG/HumanGate/Gateway/SDK with synthetic HTTP side-effect accounting."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import make_async_engine
from app.event_loop import make_event_loop
from app.infra.adapters.business_mcp.adapter import BusinessMcpAdapter
from app.infra.adapters.business_mcp.catalog import catalog, workflow_definitions
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.human_gate.postgresql import PostgreSQLHumanGate
from app.infra.identity.mcp_identity import McpIdentityMapping
from app.infra.identity.unconfigured import UnconfiguredIdentityMapping
from app.infra.mcp.driver import McpDriver
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.persistence.capability_registry.schema import capabilities
from app.infra.persistence.mcp import schema as mcp_schema
from app.infra.persistence.mcp.repository import PostgreSQLMcpStore
from app.infra.persistence.mcp.workflow_repository import PostgreSQLWorkflowStore
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.mcp.connections import McpExecutionContextFactory
from app.mcp.contracts import INPUT_SCHEMAS, SAFETY_ANNOTATIONS, WRITE_TOOLS, OutputContract
from app.mcp.models import McpFailure, digest
from app.mcp.operations import GovernedOperations
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.capability_gateway import RequestOrgContext
from app.ports.human_gate import (
    HumanGateDecisionRecord,
    build_task_version_binding_manifest,
)
from app.ports.mcp import McpSubmitPermit
from app.ports.task_store import TaskRecord
from app.ports.workflow_store import GovernedWorkflowAuthorization, RecoveryResolution
from app.workflow.engine import WorkflowEngine
from tests.infra.mcp.test_transport import Peer, serving
from tests.infra.persistence.test_mcp_store import Revocations, bind, database
from tests.mcp.test_contracts import VALID
from tests.workflow.test_engine import RecordingTrace


class BusinessPeer(Peer):
    def reply(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = super().reply(request)
        if request["method"] == "tools/list":
            reply["result"]["tools"] = [
                {"name": name, "inputSchema": schema, "annotations": dict(SAFETY_ANNOTATIONS[name])}
                for name, schema in INPUT_SCHEMAS.items()
            ]
        return reply


def test_execution_guard_budget_exception_and_cancellation_release(migrated_database_url):
    with serving(BusinessPeer("2025-11-25")) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(
                migrated_database_url, profile, "talk_preparation_save", durable=True
            ) as h:
                store, op = h["workflows"], h["op"]
                await confirm(h)
                request = await store.confirmation(op)
                ready = await store.transition(
                    op, state="READY", gate_request_id=request.request_id, attempt_id=uuid4().hex
                )
                with pytest.raises(McpFailure, match="^mcp_workflow_authorization_invalid$"):
                    await store.consume(
                        GovernedWorkflowAuthorization(
                            operation_id=ready.operation_id,
                            attempt_id=ready.attempt_id,
                            expected_revision=ready.revision,
                        ),
                        ready.context,
                        capability_id=ready.leaf_capability_id,
                        arguments=ready.arguments,
                    )
                assert (await store.by_task(h["task"])).state == "READY"
                with pytest.raises(RuntimeError, match="^synthetic_fault$"):
                    async with store.execution_guard(op):
                        raise RuntimeError("synthetic_fault")
                async with store.execution_guard(op):
                    with pytest.raises(McpFailure, match="^mcp_operation_busy$"):
                        async with store.execution_guard(op):
                            pytest.fail("duplicate operation lock acquired")
                entered = asyncio.Event()

                async def holder():
                    async with store.execution_guard(op):
                        entered.set()
                        await asyncio.Event().wait()

                task = asyncio.create_task(holder())
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                async with store.execution_guard(op):
                    assert await store.by_task(h["task"]) is not None
                async with AsyncExitStack() as stack:
                    for _ in range(4):
                        await stack.enter_async_context(
                            store.execution_guard(
                                op.model_copy(update={"operation_id": uuid4().hex})
                            )
                        )
                    with pytest.raises(McpFailure, match="^mcp_operation_busy$"):
                        async with store.execution_guard(op):
                            pytest.fail("guard budget exceeded")
                    assert await store.by_task(h["task"]) is not None
                async with store.execution_guard(op):
                    assert await store.by_task(h["task"]) is not None

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("tool", ["clothing_plan_submit", "talk_record_submit"])
@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "unapproved",
        "session",
        "service",
        "client",
        "operation",
        "arguments",
        "expired",
        "before_http",
    ],
)
def test_submit_precondition_is_independent_and_rechecked_before_http(
    migrated_database_url, tool, fault
):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, tool) as h:

                class InvalidPermit(SyntheticSubmitPrecondition):
                    approved = fault != "unapproved"
                    calls = 0

                    async def verify(self, context, name, arguments):
                        self.calls += 1
                        if fault == "before_http" and self.calls > 1:
                            return None
                        permit = await super().verify(context, name, arguments)
                        field = {
                            "session": "login_session_fingerprint",
                            "service": "service_config_id",
                            "client": "registration_id",
                            "operation": "operation_id",
                        }.get(fault)
                        if field:
                            return permit.model_copy(
                                update={
                                    "context": context.model_copy(update={field: "synthetic-other"})
                                }
                            )
                        if fault == "arguments":
                            return permit.model_copy(update={"arguments_digest": "0" * 64})
                        if fault == "expired":
                            return permit.model_copy(
                                update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)}
                            )
                        return permit

                provider = InvalidPermit()
                h["adapter"].submit_preconditions = (
                    {} if fault == "missing" else {(profile.service_config_id, tool): provider}
                )
                await confirm(h)
                result = await h["workflow"].resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=h["op"].action_digest
                )
                op = await h["workflows"].by_task(h["task"])
                assert result.status == "failed" and peer.effects == 0
                assert op.state == ("UNKNOWN" if fault == "before_http" else "CANCELLED")
                assert op.send_started is (fault == "before_http")
                assert not any(body["method"] == "tools/call" for _, _, body in peer.calls)
                if fault == "before_http":
                    assert provider.calls == 2

        asyncio.run(run(), loop_factory=make_event_loop)


def output_contract() -> OutputContract:
    return OutputContract(
        version="synthetic-v1",
        schema={
            "type": "object",
            "required": ["synthetic"],
            "properties": {"synthetic": {"const": True}},
            "additionalProperties": False,
        },
        model_fields=("synthetic",),
        ui_fields=("synthetic",),
        persistence_fields=(),
        approval_evidence="isolated-test-only",
        postcondition=lambda data: data == {"synthetic": True},
    )


class SyntheticSubmitPrecondition:
    """Explicit synthetic supplier guarantee; never a production approval."""

    approved = True
    synthetic = True
    version = "synthetic-submit-v1"

    async def verify(self, context, tool, arguments):
        return McpSubmitPermit(
            context=context,
            remote_tool=tool,
            arguments_digest=digest(arguments),
            basis="approved_atomic_rejection",
            evidence_digest=digest("synthetic-provider-contract"),
            policy_version=self.version,
            valid_until=datetime.now(UTC) + timedelta(minutes=2),
        )

    async def artifact_deadline(self, context, tool, arguments):
        return datetime.now(UTC) + timedelta(minutes=20)


@asynccontextmanager
async def harness(
    url: str,
    profile: Any,
    tool: str,
    *,
    durable: bool = False,
    arguments=None,
    trusted_task_deadline=False,
    trace_writer=False,
    output_contracts=None,
):
    async with (
        durable_database(url, profile) if durable else database(url) as (
            store,
            conn,
            revocations,
        )
    ):
        await bind(store, profile)
        token = authenticated_session.set(
            AuthenticatedSessionContext(
                principal=Principal(
                    ai_user_id="synthetic-user",
                    display_name="Synthetic",
                    roles=(),
                    org_ctx=PrincipalOrgContext(tenant_id="default"),
                ),
                fingerprint=b"synthetic-session",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        try:
            registry = PostgreSQLCapabilityRegistry(store.sessions)
            gates = PostgreSQLHumanGate(store.sessions)
            workflows = PostgreSQLWorkflowStore(store)
            tasks = PostgreSQLTaskStore(store.sessions)
            contracts = {name: output_contract() for name in INPUT_SCHEMAS}
            contracts.update(output_contracts or {})
            specs, mappings = catalog(profile, contracts)
            for spec in specs:
                await registry.create(spec)
            for mapping in mappings:
                await store.bind_capability(mapping)
            definitions = workflow_definitions(profile)
            manifests = {key: value.policy for key, value in definitions.items()}
            policy = MinimalPolicyGuard(
                governed_outer_ids=manifests,
                governed_leaf_ids=[item.leaf_capability_id for item in manifests.values()],
            )
            contexts = McpExecutionContextFactory(store, workflows, gates)
            adapter = BusinessMcpAdapter(
                McpDriver({profile.service_config_id: profile}, store),
                store,
                {
                    (profile.service_config_id, name): contract
                    for name, contract in contracts.items()
                },
                isolated_test_contracts=True,
                workflows=workflows,
                profiles={profile.service_config_id: profile},
                submit_preconditions={
                    (profile.service_config_id, name): SyntheticSubmitPrecondition()
                    for name in ("clothing_plan_submit", "talk_record_submit")
                },
            )
            trace = PostgreSQLTraceWriter(store.sessions) if trace_writer else RecordingTrace()
            gateway = CapabilityGateway(
                capability_registry=registry,
                identity_mapping=McpIdentityMapping(UnconfiguredIdentityMapping(), store),
                adapters={"business_platform": adapter},
                policy_guard=policy,
                human_gate_port=gates,
                trace_port=trace,
                tenant_id="default",
                mcp_contexts=contexts,
            )
            operations = GovernedOperations(
                workflows,
                contexts,
                gates,
                gateway,
                policy,
                manifests,
                recovery_policies={(profile.service_config_id, tool): SyntheticRecoveryPolicy()}
                if trusted_task_deadline
                else None,
                submit_preconditions=adapter.submit_preconditions,
                registry=registry,
            )

            def engine():
                return WorkflowEngine(
                    definitions=definitions,
                    capability_registry=registry,
                    gateway=gateway,
                    task_store=tasks,
                    trace_port=trace,
                    human_gate_port=gates,
                    governed_operations=operations,
                )

            workflow = engine()
            task = uuid4().hex
            trace_id = uuid4().hex
            outer = f"business.{profile.service_config_id}.{tool}"
            spec = next(item for item in specs if item.capability_id == outer)
            await tasks.create_task(
                TaskRecord(
                    task_id=task,
                    session_id="synthetic-chat",
                    ai_user_id="synthetic-user",
                    tenant_id="default",
                    status="running",
                    trace_id=trace_id,
                )
            )
            bindings = await workflow.version_bindings(workflow_capability=spec)
            await gates.bind_task(
                build_task_version_binding_manifest(
                    task_id=task, bindings=bindings.bindings, locked_at=datetime.now(UTC)
                )
            )
            result = await workflow.execute(
                workflow_id=outer,
                expected_version=spec.version,
                workflow_capability=spec,
                task_id=task,
                session_id="synthetic-chat",
                ai_user_id="synthetic-user",
                initial_input=VALID[tool] if arguments is None else arguments,
                request_context=RequestOrgContext(request_id=trace_id, tenant_id="default"),
            )
            op = await workflows.by_task(task)
            assert op is not None and result.status == "waiting_confirm"
            yield locals()
        finally:
            authenticated_session.reset(token)


async def confirm(h: dict[str, Any], *, decide: bool = True) -> None:
    op, gates = h["op"], h["gates"]
    request = await h["workflow"].governed_confirmation(op.context.task_id)
    assert request is not None and request.action_digest == op.action_digest
    h["op"] = await h["operations"].store.by_task(op.context.task_id)
    if not decide:
        return
    await gates.record_decision(
        HumanGateDecisionRecord(
            request_id=request.request_id,
            task_id=request.task_id,
            decided_by_ai_user_id=op.context.user_id,
            decided_session_id=op.context.chat_session_id,
            decided_tenant_id=op.context.tenant_id,
            decision="confirmed",
            request_digest=request.request_digest,
            binding_manifest_digest=request.binding_manifest_digest,
            decided_at=datetime.now(UTC),
        )
    )


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_six_writes_require_durable_confirmation_and_do_not_replay(
    migrated_database_url: str, tool: str
) -> None:
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, tool) as h:
                op = h["op"]
                assert peer.effects == 0 and peer.calls == []
                denied = await h["gateway"].execute_capability(
                    op.context.task_id,
                    op.context.chat_session_id,
                    op.context.user_id,
                    op.leaf_capability_id,
                    op.arguments,
                    RequestOrgContext(request_id="direct", tenant_id="default"),
                )
                assert denied.status == "denied" and peer.effects == 0
                with pytest.raises(McpFailure, match="mcp_workflow_authorization_invalid"):
                    await h["workflow"].resume(
                        task_id=op.context.task_id,
                        confirmed=True,
                        expected_action_digest=op.action_digest,
                    )
                assert peer.effects == 0
                await confirm(h)
                restarted = h["engine"]()
                result = await restarted.resume(
                    task_id=op.context.task_id,
                    confirmed=True,
                    expected_action_digest=op.action_digest,
                )
                assert result.status == "completed"
                assert result.output == {
                    "operation_id": op.operation_id,
                    "state": "VERIFIED_SUCCESS",
                    "result": None,
                }
                again = await h["engine"]().resume(
                    task_id=op.context.task_id,
                    confirmed=True,
                    expected_action_digest=op.action_digest,
                )
                assert again.output == result.output
                assert peer.effects == 1
                sends = [body for _, _, body in peer.calls if body["method"] == "tools/call"]
                assert len(sends) == 1 and sends[0]["params"]["arguments"] == VALID[tool]

        asyncio.run(run(), loop_factory=make_event_loop)


@asynccontextmanager
async def durable_database(url, profile):
    """Committed synthetic rows for child-process tests; clean only this random service."""
    engine = make_async_engine(url)
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        "127.0.0.1",
        15432,
        "eternalai_test",
    )
    assert profile.service_config_id.startswith("process-")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    revocations = Revocations()
    try:
        yield PostgreSQLMcpStore(sessions, b"s" * 32, revocations), None, revocations
    finally:
        async with engine.begin() as conn:
            tasks = (
                (
                    await conn.execute(
                        sa.select(mcp_schema.workflow_runs.c.task_id).where(
                            mcp_schema.workflow_runs.c.service_config_id
                            == profile.service_config_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for table in (
                mcp_schema.operations,
                mcp_schema.workflow_runs,
                mcp_schema.grants,
                mcp_schema.transactions,
                mcp_schema.connections,
                mcp_schema.mappings,
                mcp_schema.registrations,
                mcp_schema.services,
            ):
                await conn.execute(
                    sa.delete(table).where(
                        table.c.service_config_id == profile.service_config_id,
                        table.c.tenant_id == "default",
                    )
                )
            for name in (
                "trace_events",
                "human_gate_requests",
                "task_version_binding_manifests",
                "task_events",
                "tasks",
            ):
                table = sa.table(name, sa.column("task_id"))
                await conn.execute(sa.delete(table).where(table.c.task_id.in_(tasks)))
            ids = [spec.capability_id for spec in catalog(profile, {})[0]]
            await conn.execute(sa.delete(capabilities).where(capabilities.c.capability_id.in_(ids)))
        async with engine.connect() as conn:
            for table in (
                mcp_schema.operations,
                mcp_schema.workflow_runs,
                mcp_schema.grants,
                mcp_schema.transactions,
                mcp_schema.connections,
                mcp_schema.mappings,
                mcp_schema.registrations,
                mcp_schema.services,
            ):
                remaining = await conn.scalar(
                    sa.select(sa.func.count()).select_from(table).where(
                        table.c.service_config_id == profile.service_config_id
                    )
                )
                assert remaining == 0
            for name in (
                "trace_events",
                "human_gate_requests",
                "task_version_binding_manifests",
                "task_events",
                "tasks",
            ):
                table = sa.table(name, sa.column("task_id"))
                remaining = await conn.scalar(
                    sa.select(sa.func.count()).select_from(table).where(table.c.task_id.in_(tasks))
                )
                assert remaining == 0
            remaining = await conn.scalar(
                sa.select(sa.func.count()).select_from(capabilities).where(
                    capabilities.c.capability_id.in_(ids)
                )
            )
            assert remaining == 0
        await engine.dispose()


def process_probe(payload):
    """Child entry; all database and exception output remains process-local."""

    async def run():
        from app.mcp.models import ServiceConfig

        engine = make_async_engine(os.environ["DATABASE_URL"])
        assert (engine.url.host, engine.url.port, engine.url.database) == (
            "127.0.0.1",
            15432,
            "eternalai_test",
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgreSQLMcpStore(sessions, b"s" * 32, Revocations())
        workflows = PostgreSQLWorkflowStore(store)
        op = await workflows.load(
            payload["operation_id"], tenant_id="default", user_id="synthetic-user"
        )
        assert op is not None and op.canonical_args_digest == payload["args_digest"]
        if payload["mode"] == "restart":
            assert op.state == payload["recovered_state"]
            assert op.send_started is (op.state == "UNKNOWN")
            async with workflows.execution_guard(op):
                assert op.context.registration_id == payload["registration_id"]
            print("restart_" + op.state.lower(), flush=True)
            await engine.dispose()
            return
        async with workflows.execution_guard(op):
            if payload["mode"] == "before_consume":
                request = await workflows.confirmation(op)
                assert request is not None
                op = await workflows.transition(
                    op, state="READY", gate_request_id=request.request_id, attempt_id=uuid4().hex
                )
            authorization = GovernedWorkflowAuthorization(
                operation_id=op.operation_id,
                attempt_id=op.attempt_id,
                expected_revision=op.revision,
            )
            if payload["mode"] == "before_consume":
                assert op.state == "READY" and not op.send_started
            elif payload["mode"] == "before_http":
                await workflows.consume(
                    authorization,
                    op.context,
                    capability_id=op.leaf_capability_id,
                    arguments=op.arguments,
                )
            else:
                profile = ServiceConfig.model_validate(payload["profile"])
                adapter = BusinessMcpAdapter(
                    McpDriver({profile.service_config_id: profile}, store),
                    store,
                    {(profile.service_config_id, op.remote_tool): output_contract()},
                    isolated_test_contracts=True,
                    workflows=workflows,
                    profiles={profile.service_config_id: profile},
                    submit_preconditions={
                        (profile.service_config_id, op.remote_tool): SyntheticSubmitPrecondition()
                    },
                )
                result = await adapter.execute(
                    op.leaf_capability_id,
                    op.arguments,
                    {"mcp_authorization": op.context, "workflow_authorization": authorization},
                )
                assert result.status == "success"
            print("fault_point_reached", flush=True)
            while True:
                await asyncio.sleep(1)

    try:
        asyncio.run(run(), loop_factory=make_event_loop)
    except BaseException:
        print("child_failed", flush=True)
        return 2
    return 0


@pytest.mark.parametrize("mode", ["before_consume", "before_http", "after_http"])
@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_real_process_kill_and_restart_does_not_replay(migrated_database_url, mode, tool):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original_profile:
        profile = original_profile.model_copy(
            update={"service_config_id": "process-" + uuid4().hex}
        )

        async def run():
            async with harness(migrated_database_url, profile, tool, durable=True) as h:
                await confirm(h)
                op = h["op"]
                request = await h["workflows"].confirmation(op)
                if mode != "before_consume":
                    await h["workflows"].transition(
                        op,
                        state="READY",
                        gate_request_id=request.request_id,
                        attempt_id=uuid4().hex,
                    )
                active_state = "READY" if mode == "before_consume" else "SENDING"
                recovered_state = "CANCELLED" if mode == "before_consume" else "UNKNOWN"
                payload = {
                    "operation_id": op.operation_id,
                    "args_digest": op.canonical_args_digest,
                    "registration_id": op.context.registration_id,
                    "profile": profile.model_dump(),
                    "mode": mode,
                    "recovered_state": recovered_state,
                }
                code = (
                    "import json,sys; from tests.workflow.test_mcp_recovery "
                    "import process_probe; sys.exit(process_probe(json.loads"
                    "(sys.stdin.readline())))"
                )
                child_env = {**os.environ, "DATABASE_URL": migrated_database_url}
                child = subprocess.Popen(
                    [sys.executable, "-c", code],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=child_env,
                )
                try:
                    child.stdin.write((json.dumps(payload) + "\n").encode())
                    child.stdin.flush()
                    line = await asyncio.wait_for(
                        asyncio.to_thread(child.stdout.readline), timeout=30
                    )
                    assert line == b"fault_point_reached\r\n" or line == b"fault_point_reached\n"
                    sending = await h["workflows"].by_task(h["task"])
                    assert sending.state == active_state
                    assert sending.send_started is (mode != "before_consume")
                    with pytest.raises(McpFailure, match="^mcp_operation_busy$"):
                        await h["operations"].recover(sending)
                    with pytest.raises(McpFailure, match="^mcp_operation_busy$"):
                        await h["operations"].discard(h["task"])
                    assert (await h["workflows"].by_task(h["task"])).state == active_state
                    await api_recover(h, profile, expected_status=409, active_state=active_state)
                    child.kill()
                    await asyncio.to_thread(child.communicate, timeout=10)
                    assert child.returncode != 0
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=10)
                payload["mode"] = "restart"
                await api_recover(h, profile, expected_status=200, active_state=active_state)
                restarted = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-c", code],
                    input=(json.dumps(payload) + "\n").encode(),
                    capture_output=True,
                    env=child_env,
                    timeout=30,
                )
                assert restarted.returncode == 0
                assert restarted.stdout.strip() == ("restart_" + recovered_state.lower()).encode()
                result = await h["engine"]().resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=op.action_digest
                )
                assert result.output["state"] == recovered_state
                assert peer.effects == (1 if mode == "after_http" else 0)
                assert (
                    len([body for _, _, body in peer.calls if body["method"] == "tools/call"])
                    == peer.effects
                )
                stored = await h["workflows"].by_task(h["task"])
                assert stored.arguments == op.arguments and stored.attempt_id == sending.attempt_id
                if mode == "before_consume":
                    assert stored.state == "CANCELLED" and not stored.send_started
                    # Neither the old gate nor its exact send permit can revive the attempt.
                    async with h["workflows"].execution_guard(stored):
                        with pytest.raises(
                            McpFailure, match="^mcp_workflow_authorization_invalid$"
                        ):
                            await h["workflows"].consume(
                                GovernedWorkflowAuthorization(
                                    operation_id=sending.operation_id,
                                    attempt_id=sending.attempt_id,
                                    expected_revision=sending.revision,
                                ),
                                sending.context,
                                capability_id=sending.leaf_capability_id,
                                arguments=sending.arguments,
                            )
                    assert peer.effects == 0 and peer.calls == []

        asyncio.run(run(), loop_factory=make_event_loop)


async def api_recover(h, profile, *, expected_status, active_state="SENDING"):
    """Same authenticated browser requests, including GET/no-CSRF negative, after process death."""
    import httpx

    from app.api.v1.mcp import McpApiService
    from app.infra.mcp.transport import BoundedOAuthHttp
    from app.infra.workflow.engine_adapter import WorkflowEngineAdapter
    from app.main import create_app
    from app.mcp.oauth import OAuthConnections
    from app.ports.auth import VerifiedSessionToken
    from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS
    from tests.mcp.test_oauth import ApprovedSyntheticPolicy

    owner = authenticated_session.get()

    class Tokens:
        def inspect(self, value):
            assert value in {"synthetic-owner", "synthetic-other-owner"}
            return VerifiedSessionToken(
                principal=owner.principal
                if value == "synthetic-owner"
                else owner.principal.model_copy(update={"ai_user_id": "synthetic-other-user"}),
                fingerprint=owner.fingerprint,
                expires_at=owner.expires_at,
                version=2,
            )

    service = McpApiService(
        OAuthConnections(
            {profile.service_config_id: profile},
            h["store"],
            BoundedOAuthHttp(),
            h["revocations"],
            ApprovedSyntheticPolicy(),
        ),
        h["operations"],
        WorkflowEngineAdapter(h["engine"]()),
    )
    app = create_app(
        mcp_service=service,
        session_tokens=Tokens(),
        session_revocations=h["revocations"],
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
        cookies={"eternalai_session": "synthetic-owner"},
    ) as client:
        path = "/api/v1/mcp/operations/" + h["op"].operation_id
        before = (await client.get(path)).json()
        assert before["state"] == active_state and before["recovery_action"] == "recover"
        assert (await h["workflows"].by_task(h["task"])).state == active_state
        request = {
            "action": "recover",
            "expected_revision": before["revision"],
            "preview_digest": before["preview_digest"],
        }
        assert (await client.post(path + "/resume", json=request)).status_code == 403
        client.cookies.clear()
        client.cookies.set("eternalai_session", "synthetic-other-owner")
        denied = await client.post(path + "/resume", json=request, headers=TEST_CSRF_HEADERS)
        assert denied.status_code == 409
        assert denied.json()["detail"]["code"] == "mcp_action_unavailable"
        assert (await h["workflows"].by_task(h["task"])).state == active_state
        client.cookies.clear()
        client.cookies.set("eternalai_session", "synthetic-owner")
        result = await client.post(path + "/resume", json=request, headers=TEST_CSRF_HEADERS)
        assert result.status_code == expected_status
        stored = await h["workflows"].by_task(h["task"])
        recovered_state = "CANCELLED" if active_state == "READY" else "UNKNOWN"
        assert stored.state == (recovered_state if expected_status == 200 else active_state)
        if expected_status == 409:
            assert result.json()["detail"]["code"] == "mcp_action_unavailable"
            cancel = await client.post(
                path + "/resume", json={**request, "action": "cancel"}, headers=TEST_CSRF_HEADERS
            )
            assert cancel.status_code == 409
            assert cancel.json()["detail"]["code"] == "mcp_action_unavailable"
            assert (await h["workflows"].by_task(h["task"])).state == active_state
        else:
            assert result.json()["state"] == recovered_state
            if active_state == "READY":
                assert result.json()["recovery_action"] == "none"
                again = await client.post(path + "/resume", json=request, headers=TEST_CSRF_HEADERS)
                assert again.status_code == 409
                assert again.json()["detail"]["code"] == "mcp_action_unavailable"
        assert stored.arguments == h["op"].arguments


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_concurrent_confirmation_consumes_only_one_send_permission(migrated_database_url, tool):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as original:
        profile = original.model_copy(update={"service_config_id": "process-" + uuid4().hex})

        async def run():
            async with harness(migrated_database_url, profile, tool, durable=True) as h:
                await confirm(h)
                results = await asyncio.gather(
                    *[
                        h["engine"]().resume(
                            task_id=h["task"],
                            confirmed=True,
                            expected_action_digest=h["op"].action_digest,
                        )
                        for _ in range(2)
                    ],
                    return_exceptions=True,
                )
                assert any(
                    not isinstance(item, BaseException) and item.status == "completed"
                    for item in results
                )
                assert all(
                    not isinstance(item, BaseException)
                    or isinstance(item, McpFailure)
                    and item.code in {"mcp_operation_conflict", "mcp_operation_busy"}
                    for item in results
                )
                assert peer.effects == 1
                stored = await h["workflows"].by_task(h["task"])
                assert stored.state == "VERIFIED_SUCCESS"
                # READY, SENDING and success follow the persisted confirmation snapshot.
                assert stored.revision == h["op"].revision + 3

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("old_gate_expired", [False, True])
def test_new_login_takeover_keeps_original_client_and_requires_fresh_gate(
    migrated_database_url, monkeypatch, old_gate_expired
):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, "clothing_plan_submit") as h:
                await confirm(h)
                op = h["op"]
                await bind(h["store"], profile, fingerprint=b"synthetic-new-session")
                session = authenticated_session.get()
                if old_gate_expired:
                    future = datetime.now(UTC) + timedelta(minutes=11)

                    class Clock(datetime):
                        @classmethod
                        def now(cls, tz=None):
                            return future

                    monkeypatch.setattr("app.mcp.operations.datetime", Clock)
                token = authenticated_session.set(
                    replace(
                        session,
                        fingerprint=b"synthetic-new-session",
                        expires_at=datetime.now(UTC) + timedelta(hours=1)
                        if old_gate_expired
                        else session.expires_at,
                    )
                )
                try:
                    with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                        await h["workflow"].resume(
                            task_id=h["task"],
                            confirmed=True,
                            expected_action_digest=op.action_digest,
                        )
                    with pytest.raises(McpFailure, match="mcp_recovery_contract_unconfirmed"):
                        await h["operations"].takeover(op)
                    h["operations"].recovery_policies[
                        (profile.service_config_id, op.remote_tool)
                    ] = SyntheticRecoveryPolicy()
                    renewed = await h["operations"].takeover(op)
                    assert renewed.arguments == op.arguments
                    if old_gate_expired:
                        assert op.expires_at < future < renewed.expires_at
                        assert renewed.expires_at == op.artifact_expires_at
                    else:
                        assert renewed.expires_at == op.expires_at
                    assert renewed.artifact_expires_at == op.artifact_expires_at
                    assert renewed.context.registration_id == op.context.registration_id
                    assert (
                        renewed.context.login_session_fingerprint
                        != op.context.login_session_fingerprint
                    )
                    assert renewed.context.grant_epoch > op.context.grant_epoch
                    assert peer.effects == 0
                    request = await h["workflows"].confirmation(renewed)
                    assert request.action_digest != op.action_digest
                    assert await h["gates"].get_decision(request.request_id) is None
                    with pytest.raises(McpFailure, match="mcp_workflow_authorization_invalid"):
                        await h["workflow"].resume(
                            task_id=h["task"],
                            confirmed=True,
                            expected_action_digest=renewed.action_digest,
                        )
                    assert peer.effects == 0
                finally:
                    authenticated_session.reset(token)

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("tool", ["clothing_plan_submit", "talk_record_submit"])
@pytest.mark.parametrize("valid_origin", [True, False])
def test_external_confirmation_never_becomes_local_success(
    migrated_database_url, tool, valid_origin
):
    peer = BusinessPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, tool) as h:
                url = (profile.issuer if valid_origin else "https://other.invalid") + "/review"
                h["adapter"].contracts[(profile.service_config_id, tool)] = replace(
                    output_contract(),
                    external_confirmation=lambda _: url,
                    postcondition=lambda _: False,
                )
                await confirm(h)
                result = await h["workflow"].resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=h["op"].action_digest
                )
                expected = "WAITING_EXTERNAL_CONFIRM" if valid_origin else "UNKNOWN"
                assert result.status == "failed" and result.output["state"] == expected
                stored = await h["workflows"].by_task(h["task"])
                assert stored.review_url == (url if valid_origin else None)
                assert stored.safe_output == {}
                await h["engine"]().resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=h["op"].action_digest
                )
                assert peer.effects == 1

        asyncio.run(run(), loop_factory=make_event_loop)


class SyntheticRecoveryPolicy:
    approved = True
    version = "synthetic-recovery-v1"

    def same_subject(self, previous_digest, current_evidence):
        return previous_digest == digest(current_evidence)

    def record_id(self, operation):
        return "synthetic-record"

    async def artifact_deadline(self, context, tool, arguments):
        return datetime.now(UTC) + timedelta(minutes=20)

    async def reconcile(self, operation, verified_read):
        assert verified_read == {"synthetic": True}
        return RecoveryResolution(state="retry_original")


@pytest.mark.parametrize("resolution", ["verified", "retry_original"])
def test_sent_operation_allows_readback_after_gate_and_artifact_expiry(
    migrated_database_url, monkeypatch, resolution
):
    peer = BusinessPeer("2025-11-25", fault="http500")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, "clothing_plan_submit") as h:
                await confirm(h)
                await h["workflow"].resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=h["op"].action_digest
                )
                op = await h["workflows"].by_task(h["task"])
                assert op.state == "UNKNOWN" and peer.effects == 1
                future = datetime.now(UTC) + timedelta(minutes=21)

                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return future

                class Recovery(SyntheticRecoveryPolicy):
                    async def reconcile(self, operation, verified_read):
                        assert verified_read == {"synthetic": True}
                        return RecoveryResolution(state=resolution)

                monkeypatch.setattr("app.mcp.operations.datetime", Clock)
                token = authenticated_session.set(
                    replace(authenticated_session.get(), expires_at=future + timedelta(hours=1))
                )
                h["operations"].recovery_policies[(profile.service_config_id, op.remote_tool)] = (
                    Recovery()
                )
                peer.fault = ""
                try:
                    if resolution == "verified":
                        updated = await h["operations"].reconcile(op)
                        assert (
                            updated.state == "VERIFIED_SUCCESS"
                            and updated.artifact_expires_at == op.artifact_expires_at
                        )
                    else:
                        with pytest.raises(McpFailure, match="^mcp_artifact_expired$"):
                            await h["operations"].reconcile(op)
                        assert (await h["workflows"].by_task(h["task"])).revision == op.revision
                    calls = [
                        body["params"]["name"]
                        for _, _, body in peer.calls
                        if body["method"] == "tools/call"
                    ]
                    assert calls == ["clothing_plan_submit", "clothing_result_get"]
                finally:
                    authenticated_session.reset(token)

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize(
    "tool,read_tool",
    [
        ("clothing_plan_submit", "clothing_result_get"),
        ("talk_record_submit", "talk_record_get"),
        ("talk_task_claim", "talk_tasks_list"),
    ],
)
@pytest.mark.parametrize("trusted_task_deadline", [False, True])
def test_recovery_reads_original_then_requires_new_confirmation(
    migrated_database_url: str,
    tool: str,
    read_tool: str,
    trusted_task_deadline: bool,
) -> None:
    peer = BusinessPeer("2025-11-25", fault="http500")
    with serving(peer) as profile:

        async def run():
            async with harness(
                migrated_database_url, profile, tool, trusted_task_deadline=trusted_task_deadline
            ) as h:
                h["operations"].recovery_policies.clear()
                await confirm(h)
                await h["workflow"].resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=h["op"].action_digest
                )
                op = await h["workflows"].by_task(h["task"])
                assert op.state == "UNKNOWN" and peer.effects == 1
                with pytest.raises(McpFailure, match="mcp_recovery_contract_unconfirmed"):
                    await h["operations"].reconcile(op)
                assert peer.effects == 1
                peer.fault = ""
                h["operations"].recovery_policies[(profile.service_config_id, tool)] = (
                    SyntheticRecoveryPolicy()
                )
                if tool == "talk_task_claim" and not trusted_task_deadline:
                    with pytest.raises(McpFailure, match="^mcp_artifact_deadline_unconfirmed$"):
                        await h["operations"].reconcile(op)
                    calls = [
                        body["params"]["name"]
                        for _, _, body in peer.calls
                        if body["method"] == "tools/call"
                    ]
                    assert calls == [tool, read_tool]
                    unchanged = await h["workflows"].by_task(h["task"])
                    assert unchanged.state == "UNKNOWN" and unchanged.revision == op.revision
                    return
                renewed = await h["operations"].reconcile(op)
                calls = [
                    body["params"] for _, _, body in peer.calls if body["method"] == "tools/call"
                ]
                assert [item["name"] for item in calls] == [tool, read_tool]
                assert (
                    renewed.state == "WAITING_LOCAL_CONFIRM" and renewed.arguments == op.arguments
                )
                assert renewed.context.registration_id == op.context.registration_id
                assert renewed.action_digest != op.action_digest
                assert renewed.previous_attempts == (op.attempt_id,)
                with pytest.raises(McpFailure, match="mcp_workflow_authorization_invalid"):
                    await h["workflow"].resume(
                        task_id=h["task"], confirmed=True, expected_action_digest=op.action_digest
                    )
                request = await h["workflows"].confirmation(renewed)
                assert request is not None
                await h["gates"].record_decision(
                    HumanGateDecisionRecord(
                        request_id=request.request_id,
                        task_id=request.task_id,
                        decided_by_ai_user_id=op.context.user_id,
                        decided_session_id=op.context.chat_session_id,
                        decided_tenant_id=op.context.tenant_id,
                        decision="confirmed",
                        request_digest=request.request_digest,
                        binding_manifest_digest=request.binding_manifest_digest,
                        decided_at=datetime.now(UTC),
                    )
                )
                final = await h["workflow"].resume(
                    task_id=h["task"], confirmed=True, expected_action_digest=renewed.action_digest
                )
                assert final.output["state"] == "VERIFIED_SUCCESS"
                calls = [
                    body["params"] for _, _, body in peer.calls if body["method"] == "tools/call"
                ]
                assert [item["name"] for item in calls] == [tool, read_tool, tool]
                assert calls[0]["arguments"] == calls[2]["arguments"] == VALID[tool]

        asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("fault", ["http500", "after_cas"])
def test_uncertain_send_survives_reconstruction_without_retry(
    migrated_database_url: str, fault: str
) -> None:
    peer = BusinessPeer("2025-11-25", fault="http500" if fault == "http500" else "")
    with serving(peer) as profile:

        async def run():
            async with harness(migrated_database_url, profile, "talk_preparation_save") as h:
                await confirm(h)
                op = h["op"]
                if fault == "after_cas":
                    request = await h["workflows"].confirmation(op)
                    ready = await h["workflows"].transition(
                        op,
                        state="READY",
                        gate_request_id=request.request_id,
                        attempt_id=uuid4().hex,
                    )
                    async with h["workflows"].execution_guard(ready):
                        await h["workflows"].consume(
                            GovernedWorkflowAuthorization(
                                operation_id=op.operation_id,
                                attempt_id=ready.attempt_id,
                                expected_revision=ready.revision,
                            ),
                            ready.context,
                            capability_id=op.leaf_capability_id,
                            arguments=op.arguments,
                        )
                result = await h["engine"]().resume(
                    task_id=op.context.task_id,
                    confirmed=True,
                    expected_action_digest=op.action_digest,
                )
                assert result.output["state"] == "UNKNOWN"
                await h["engine"]().resume(
                    task_id=op.context.task_id,
                    confirmed=True,
                    expected_action_digest=op.action_digest,
                )
                assert peer.effects == (1 if fault == "http500" else 0)
                stored = await h["workflows"].by_task(op.context.task_id)
                assert stored.state == "UNKNOWN" and stored.send_started is True
                assert (
                    stored.arguments == op.arguments
                    and stored.context.registration_id == op.context.registration_id
                )

        asyncio.run(run(), loop_factory=make_event_loop)
