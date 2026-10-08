"""DB-independent regressions for durable MCP coordinator boundaries.

Memory fixtures exercise application wiring, not PostgreSQL transaction guarantees.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1.mcp import McpApiService, ResumeRequest
from app.infra.adapters.business_mcp.adapter import BusinessMcpAdapter
from app.infra.adapters.business_mcp.catalog import catalog
from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.infra.mcp.driver import McpDriver
from app.mcp.contracts import OutputContract
from app.mcp.models import McpFailure, digest
from app.mcp.operations import GovernedOperations
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.human_gate import (
    HumanGateDecisionRecord,
    VersionBinding,
    build_task_version_binding_manifest,
)
from app.ports.workflow_store import WorkflowOperation
from app.runtime.response_projection import project_response_data
from tests.infra.mcp.test_transport import Peer, Tokens, authorization, serving
from tests.mcp.test_contracts import VALID
from tests.workflow.test_mcp_recovery import BusinessPeer


class Store:
    def __init__(self, op):
        self.op = op
        self.transitions = []
        self.fail = False
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def execution_guard(self, op):
        async with self.lock:
            yield

    async def by_task(self, task_id):
        return self.op if task_id == self.op.context.task_id else None

    async def load(self, operation_id, *, tenant_id, user_id):
        assert (operation_id, tenant_id, user_id) == (
            self.op.operation_id,
            self.op.context.tenant_id,
            self.op.context.user_id,
        )
        return self.op

    async def confirmation(self, op):
        return await self.gates.get_request(op.gate_request_id) if op.gate_request_id else None

    async def transition(self, op, *, state, **changes):
        if self.fail:
            raise RuntimeError("synthetic CAS failure")
        if op.revision != self.op.revision:
            raise McpFailure("mcp_operation_conflict")
        update = {"state": state, "revision": op.revision + 1}
        for source, target in (
            ("renewed_context", "context"),
            ("renewed_action_digest", "action_digest"),
            ("renewed_gate_expires_at", "expires_at"),
            ("gate_request_id", "gate_request_id"),
            ("public_result", "public_result"),
            ("safe_output", "safe_output"),
            ("attempt_id", "attempt_id"),
            ("confirmation_error_code", "confirmation_error_code"),
        ):
            if source in changes:
                update[target] = changes[source]
        self.op = op.model_copy(update=update)
        self.transitions.append(state)
        return self.op


@pytest.mark.parametrize("annotation", ["examples", "default", "enum"])
def test_public_schema_ref_named_property_and_annotation_data(annotation):
    value = {"$ref": "literal business value"}
    field = {
        "type": "object", "properties": {"$ref": {"type": "string"}},
        "additionalProperties": False,
        annotation: [value] if annotation != "default" else value,
    }
    contract = OutputContract(
        "synthetic-literal", {"type": "object", "properties": {"payload": field}},
        ("payload",), ("payload",), ("payload",), "synthetic-literal",
    )
    output = {"payload": value}
    schema = contract.public_result_schema()
    assert "$defs" not in schema
    assert schema["anyOf"][0]["properties"]["payload"] == field
    assert project_response_data(output, schema) == output


@pytest.mark.parametrize("reference", ["#/$defs/missing", "https://example.invalid/schema"])
def test_public_schema_real_unresolved_refs_still_fail_closed(reference):
    contract = OutputContract(
        "synthetic-ref", {"type": "object", "properties": {"value": {"$ref": reference}}},
        ("value",), ("value",), ("value",), "synthetic-ref",
    )
    with pytest.raises(McpFailure) as rejected:
        contract.public_result_schema()
    assert rejected.value.code == "mcp_output_contract_invalid"


def test_public_result_local_refs_work_at_standalone_and_outer_roots():
    contract = OutputContract(
        "synthetic-ref",
        {
            "type": "object",
            "$defs": {
                "artifact": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                "hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "private": {"type": "object", "properties": {"access_token": {"type": "string"}}},
            },
            "properties": {
                "artifactId": {"$ref": "#/$defs/artifact"},
                "payloadHash": {"$ref": "#/$defs/hash"},
                "internal": {"type": "integer"},
            },
            "required": ["artifactId", "payloadHash"],
            "additionalProperties": False,
        },
        ("artifactId", "payloadHash"),
        ("artifactId", "payloadHash"),
        ("artifactId", "payloadHash", "internal"),
        "synthetic-ref",
    )
    expected = {"artifactId": "a" * 32, "payloadHash": "b" * 64}
    assert (
        project_response_data({**expected, "internal": 7}, contract.public_result_schema())
        == expected
    )
    with serving(Peer("2025-11-25")) as profile:
        specs, _ = catalog(profile, {"clothing_plan_preview": contract})
    outer = next(item for item in specs if item.type == "workflow" and item.status == "active")
    for state, result in [("WAITING_LOCAL_CONFIRM", None), ("VERIFIED_SUCCESS", expected)]:
        output = {"operation_id": "a" * 32, "state": state, "result": result}
        assert project_response_data(output, outer.output_schema) == output
    legacy = {"operation_id": "a" * 32, "state": "VERIFIED_SUCCESS"}
    assert project_response_data(legacy, outer.output_schema) == legacy


@pytest.mark.parametrize("state", ["WAITING_LOCAL_CONFIRM", "UNKNOWN"])
def test_gate_await_crossing_deadline_never_publishes_expired_wait(monkeypatch, state):
    async def run():
        h = await fixture()
        h.op = h.store.op = h.op.model_copy(update={"state": state})
        token = authenticated_session.set(h.session)
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.operations._gate
        clock = [datetime.now(UTC)]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        async def paused(*args, **kwargs):
            gate = await original(*args, **kwargs)
            clock[0] = gate.expires_at + timedelta(seconds=1)
            entered.set()
            await release.wait()
            return gate

        monkeypatch.setattr("app.mcp.operations.datetime", Clock)
        h.operations._gate = paused
        try:
            pending = asyncio.create_task(
                h.operations._renew_confirmation(h.op, h.op.context, "policy")
            )
            await entered.wait()
            release.set()
            with pytest.raises(McpFailure, match="mcp_confirmation_expired"):
                await pending
            assert h.store.op.state == (
                "EXPIRED" if state == "WAITING_LOCAL_CONFIRM" else "UNKNOWN"
            )
            assert "WAITING_LOCAL_CONFIRM" not in h.store.transitions
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


def test_coordinator_persists_adapter_public_result_before_delivery():
    from app.ports.capability_gateway import ExecutionResult
    from app.ports.mcp import McpValidatedOutcome

    async def run():
        h = await fixture()
        token = authenticated_session.set(h.session)
        gate = await h.operations._gate(h.op, h.op.context, h.op.action_digest, h.op.expires_at)
        h.store.op = h.op = h.op.model_copy(update={"gate_request_id": gate.request_id})
        await h.gates.record_decision(
            HumanGateDecisionRecord(
                request_id=gate.request_id,
                task_id=gate.task_id,
                decided_by_ai_user_id=gate.requested_for_ai_user_id,
                decided_session_id=gate.requested_session_id,
                decided_tenant_id=gate.requested_tenant_id,
                decision="confirmed",
                request_digest=gate.request_digest,
                binding_manifest_digest=gate.binding_manifest_digest,
                decided_at=gate.requested_at,
            )
        )
        visible = {"artifactId": "a" * 32, "payloadHash": "b" * 64}

        async def owned(op):
            h.operations.lifecycle_owner(op)

        sends = 0

        async def execute(*args, **kwargs):
            nonlocal sends
            assert h.store.op.state == "READY"
            sends += 1
            await h.store.transition(h.store.op, state="SENDING")
            return ExecutionResult(
                status="completed",
                trace_id="synthetic",
                mcp_outcome=McpValidatedOutcome(
                    state="VERIFIED_SUCCESS",
                    persistence={**visible, "internal": 7},
                    public_result=visible,
                ),
            )

        h.operations.owned = owned
        h.operations.gateway = SimpleNamespace(execute_capability=execute)
        try:
            result = await h.operations.resume(
                h.op.context.task_id, confirmed=True, expected_action_digest=h.op.action_digest
            )
            reloaded = WorkflowOperation.model_validate_json(h.store.op.model_dump_json())
            assert sends == 1 and result.output["result"] == reloaded.public_result == visible
            assert reloaded.safe_output["internal"] == 7
            assert "internal" not in result.output["result"]
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_for_ai_user_id", "other-user"),
        ("binding_manifest_digest", "c" * 64),
        ("action_digest", "d" * 64),
    ],
)
def test_superseded_cleanup_still_rejects_corrupt_captured_binding(field, value):
    async def run():
        h = await fixture()
        gate = await h.operations._gate(h.op, h.op.context, h.op.action_digest, h.op.expires_at)
        expired = datetime.now(UTC) - timedelta(minutes=2)
        gate = gate.model_copy(
            update={"requested_at": expired - timedelta(minutes=10), "expires_at": expired}
        )
        h.gates._requests[gate.request_id] = gate
        h.op = h.store.op = h.op.model_copy(
            update={"state": "EXPIRED", "expires_at": expired, "gate_request_id": gate.request_id}
        )
        token = authenticated_session.set(h.session)
        try:
            newer = await h.operations._renew_confirmation(h.op, h.op.context, "synthetic")
            h.gates._requests[gate.request_id] = gate.model_copy(update={field: value})
            with pytest.raises(McpFailure, match="mcp_operation_conflict"):
                await h.operations.retire_owned_confirmation(
                    h.op.context.task_id, h.op.action_digest, gate.request_id, "expired"
                )
            assert h.store.op == newer
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


async def fixture():
    now = datetime.now(UTC)
    session = AuthenticatedSessionContext(
        principal=Principal(
            ai_user_id="synthetic-user",
            display_name="Synthetic",
            roles=(),
            org_ctx=PrincipalOrgContext(tenant_id="default"),
        ),
        fingerprint=b"synthetic",
        expires_at=now + timedelta(hours=2),
    )
    context = authorization().model_copy(
        update={"login_session_fingerprint": session.fingerprint.hex()}
    )
    op = WorkflowOperation(
        operation_id="a" * 32,
        context=context,
        outer_capability_id="synthetic.workflow",
        outer_version="1",
        leaf_capability_id=context.capability_id,
        leaf_version="1",
        remote_tool="clothing_plan_preview",
        arguments={},
        canonical_args_digest=digest({}),
        action_digest="b" * 64,
        expires_at=now + timedelta(minutes=5),
    )
    gates = InMemoryHumanGate()
    await gates.bind_task(
        build_task_version_binding_manifest(
            task_id=context.task_id,
            bindings=(
                VersionBinding(
                    resource_type="workflow",
                    resource_id=op.outer_capability_id,
                    version=op.outer_version,
                    digest="a" * 64,
                ),
            ),
            locked_at=now,
        )
    )
    store = Store(op)
    store.gates = gates
    operations = GovernedOperations(store, SimpleNamespace(), gates, None, None, {})
    return SimpleNamespace(op=op, store=store, gates=gates, operations=operations, session=session)


def test_public_result_requires_three_approved_projections_and_closed_schema():
    data = {"artifactId": "a" * 32, "payloadHash": "b" * 64, "internal": 7}
    contract = OutputContract(
        "synthetic",
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
        "isolated synthetic",
    )
    assert contract.public_result(data) == {"artifactId": "a" * 32, "payloadHash": "b" * 64}
    with serving(Peer("2025-11-25")) as profile:
        specs, _ = catalog(profile, {"clothing_plan_preview": contract})
    outer = next(spec for spec in specs if spec.type == "workflow" and spec.status == "active")
    assert outer.output_schema["properties"]["result"] == contract.public_result_schema()
    output = {
        "operation_id": "a" * 32,
        "state": "VERIFIED_SUCCESS",
        "result": contract.public_result(data),
    }
    assert project_response_data(output, outer.output_schema) == output
    no_persistence = OutputContract(
        contract.version,
        contract.schema,
        contract.model_fields,
        contract.ui_fields,
        (),
        "synthetic",
    )
    assert no_persistence.public_result(data) is None
    assert no_persistence.public_result_schema() == {"type": "null"}
    legacy = asyncio.run(fixture()).op
    assert (
        WorkflowOperation.model_validate(legacy.model_dump(exclude={"public_result"})).public_result
        is None
    )
    assert (
        GovernedOperations.result(legacy.model_copy(update={"public_result": data}), "t").output[
            "result"
        ]
        is None
    )


@pytest.mark.parametrize("existing_decision", ["confirmed", "rejected", None])
def test_api_decision_crash_retry_reuses_original_timestamp(existing_decision):
    async def run():
        h = await fixture()
        request = await h.operations._gate(h.op, h.op.context, h.op.action_digest, h.op.expires_at)
        h.store.op = h.op = h.op.model_copy(update={"gate_request_id": request.request_id})
        original = None
        if existing_decision is not None:
            original = await h.gates.record_decision(
                HumanGateDecisionRecord(
                    request_id=request.request_id,
                    task_id=h.op.context.task_id,
                    decided_by_ai_user_id=h.op.context.user_id,
                    decided_session_id=h.op.context.chat_session_id,
                    decided_tenant_id=h.op.context.tenant_id,
                    decision=existing_decision,
                    request_digest=request.request_digest,
                    binding_manifest_digest=request.binding_manifest_digest,
                    decided_at=request.requested_at,
                )
            )

        class Workflows:
            sends = 0
            crash = True

            async def resume(self, **kwargs):
                if self.crash:
                    self.crash = False
                    raise RuntimeError("synthetic crash after decision")
                self.sends += 1
                await h.store.transition(h.op, state="VERIFIED_SUCCESS")

            async def finalize_governed_task(self, **kwargs):
                return None

        workflows = Workflows()
        with serving(Peer("2025-11-25")) as profile:
            service = McpApiService(
                SimpleNamespace(configs={profile.service_config_id: profile}),
                h.operations,
                workflows,
            )
            body = ResumeRequest(
                action="confirm",
                expected_revision=1,
                preview_digest=service.view(h.op).preview_digest,
            )
            if existing_decision == "rejected":
                with pytest.raises(McpFailure, match="mcp_confirmation_unavailable"):
                    await service.resume(h.op, body)
                assert workflows.sends == 0
            else:
                with pytest.raises(RuntimeError, match="synthetic crash after decision"):
                    await service.resume(h.op, body)
                if original is None:
                    original = await h.gates.get_decision(request.request_id)
                    assert original is not None and original.decision == "confirmed"
                assert (await service.resume(h.op, body)).state == "VERIFIED_SUCCESS"
                with pytest.raises(McpFailure, match="mcp_operation_conflict"):
                    await service.resume(h.store.op, body)
                assert workflows.sends == 1
        assert await h.gates.get_decision(request.request_id) == original

    asyncio.run(run())


@pytest.mark.parametrize("expired", [False, True])
def test_renewal_crash_uses_actual_gate_deadline_or_fails_closed(monkeypatch, expired):
    async def run():
        h = await fixture()
        token = authenticated_session.set(h.session)
        try:
            h.store.fail = True
            with pytest.raises(RuntimeError, match="synthetic CAS failure"):
                await h.operations._renew_confirmation(h.op, h.op.context, "synthetic-policy")
            request = next(iter(h.gates._requests.values()))
            assert h.store.transitions == []
            h.store.fail = False
            future = (
                request.expires_at + timedelta(seconds=1)
                if expired
                else request.requested_at + timedelta(seconds=3)
            )

            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return future

            monkeypatch.setattr("app.mcp.operations.datetime", Clock)
            if expired:
                with pytest.raises(McpFailure, match="mcp_confirmation_expired"):
                    await h.operations._renew_confirmation(h.op, h.op.context, "synthetic-policy")
                assert h.store.op.state == "EXPIRED"
            else:
                renewed = await h.operations._renew_confirmation(
                    h.op, h.op.context, "synthetic-policy"
                )
                assert renewed.gate_request_id == request.request_id
                assert renewed.expires_at == request.expires_at
            assert len(h.gates._requests) == 1
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


def test_new_login_cleanup_requires_exact_action_and_never_resolves_external_grant():
    async def run():
        h = await fixture()
        request = await h.operations._gate(h.op, h.op.context, h.op.action_digest, h.op.expires_at)
        h.store.op = h.op.model_copy(update={"gate_request_id": request.request_id})
        fresh = AuthenticatedSessionContext(
            principal=h.session.principal, fingerprint=b"new", expires_at=h.session.expires_at
        )
        token = authenticated_session.set(fresh)
        try:
            with pytest.raises(McpFailure, match="mcp_operation_conflict"):
                await h.operations.retire_owned_confirmation(
                    h.op.context.task_id, "c" * 64, request.request_id, "cancelled"
                )
            assert h.store.transitions == []
            assert await h.operations.retire_owned_confirmation(
                h.op.context.task_id, h.op.action_digest, request.request_id, "cancelled"
            )
            assert h.store.op.state == "CANCELLED"
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "other-task"),
        ("decided_by_ai_user_id", "other-user"),
        ("decided_session_id", "other-chat"),
        ("decided_tenant_id", "other-tenant"),
        ("request_digest", "c" * 64),
        ("binding_manifest_digest", "d" * 64),
    ],
)
def test_api_confirm_never_reuses_a_different_decision_binding(field, value):
    async def run():
        h = await fixture()
        gate = await h.operations._gate(h.op, h.op.context, h.op.action_digest, h.op.expires_at)
        h.store.op = h.op = h.op.model_copy(update={"gate_request_id": gate.request_id})
        decision = HumanGateDecisionRecord(
            request_id=gate.request_id,
            task_id=h.op.context.task_id,
            decided_by_ai_user_id=h.op.context.user_id,
            decided_session_id=h.op.context.chat_session_id,
            decided_tenant_id=h.op.context.tenant_id,
            decision="confirmed",
            request_digest=gate.request_digest,
            binding_manifest_digest=gate.binding_manifest_digest,
            decided_at=gate.requested_at,
        ).model_copy(update={field: value})

        async def conflicting(request_id):
            return decision

        async def no_send(**kwargs):
            raise AssertionError("different binding must never reach resume")

        h.gates.get_decision = conflicting
        with serving(Peer("2025-11-25")) as profile:
            service = McpApiService(
                SimpleNamespace(configs={profile.service_config_id: profile}),
                h.operations,
                SimpleNamespace(resume=no_send),
            )
            with pytest.raises(McpFailure, match="mcp_confirmation_unavailable"):
                await service.resume(
                    h.op,
                    ResumeRequest(
                        action="confirm",
                        expected_revision=h.op.revision,
                        preview_digest=service.view(h.op).preview_digest,
                    ),
                )
        assert h.store.transitions == []

    asyncio.run(run())


def test_expired_orphan_gate_keeps_unknown_until_explicit_new_generation(monkeypatch):
    async def run():
        h = await fixture()
        h.store.op = h.op = h.op.model_copy(
            update={
                "state": "UNKNOWN",
                "send_started": True,
                "artifact_expires_at": h.session.expires_at - timedelta(minutes=1),
            }
        )
        token = authenticated_session.set(h.session)
        try:
            h.store.fail = True
            with pytest.raises(RuntimeError, match="synthetic CAS failure"):
                await h.operations._renew_confirmation(h.op, h.op.context, "policy")
            orphan = next(iter(h.gates._requests.values()))
            future = orphan.expires_at + timedelta(seconds=1)

            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return future

            monkeypatch.setattr("app.mcp.operations.datetime", Clock)
            h.store.fail = False
            with pytest.raises(McpFailure, match="mcp_confirmation_expired"):
                await h.operations._renew_confirmation(h.op, h.op.context, "policy")
            assert h.store.op.state == "UNKNOWN" and h.store.transitions == []
            # Only an explicit CAS generation change can obtain a different gate.
            newer = await h.store.transition(h.op, state="UNKNOWN", renewed_action_digest="e" * 64)
            renewed = await h.operations._renew_confirmation(newer, newer.context, "policy")
            assert renewed.gate_request_id != orphan.request_id
            actual = await h.gates.get_request(renewed.gate_request_id)
            assert renewed.expires_at == actual.expires_at > future
            assert len(h.gates._requests) == 2
        finally:
            authenticated_session.reset(token)

    asyncio.run(run())


def test_new_result_descriptor_rejects_old_registry_and_old_pending_binding():
    from copy import deepcopy

    from app.infra.adapters.business_mcp.catalog import workflow_definitions
    from app.infra.workflow.production import validate_selected_workflow
    from app.ports.human_gate import VersionBindingMismatchError
    from app.version_binding import workflow_version_binding
    from tests.runtime.registry_fakes import StaticCapabilityRegistry

    async def run(profile):
        contract = OutputContract(
            "synthetic",
            {"type": "object", "properties": {"count": {"type": "integer"}}},
            ("count",),
            ("count",),
            ("count",),
            "synthetic",
        )
        specs, _ = catalog(profile, {"clothing_plan_preview": contract})
        outer = next(item for item in specs if item.type == "workflow" and item.status == "active")
        leaves = [item for item in specs if item.type != "workflow"]
        old_schema = deepcopy(outer.output_schema)
        old_schema["properties"].pop("result")
        old = outer.model_copy(
            update={"output_schema": old_schema, "output_schema_digest": digest(old_schema)}
        )
        definitions = workflow_definitions(profile)
        descriptors = {outer.capability_id: outer}
        registry = StaticCapabilityRegistry(old, *leaves)
        with pytest.raises(VersionBindingMismatchError, match="activation contract"):
            await validate_selected_workflow(
                old, registry=registry, definitions=definitions, descriptors=descriptors
            )
        registry = StaticCapabilityRegistry(outer, *leaves)
        await validate_selected_workflow(
            outer, registry=registry, definitions=definitions, descriptors=descriptors
        )
        definition = definitions[outer.capability_id]
        old_binding = workflow_version_binding(old, definition)
        new_binding = workflow_version_binding(outer, definition)
        assert old_binding.digest != new_binding.digest
        gates = InMemoryHumanGate()
        await gates.bind_task(
            build_task_version_binding_manifest(
                task_id="old-task", bindings=(old_binding,), locked_at=datetime.now(UTC)
            )
        )
        with pytest.raises(VersionBindingMismatchError):
            await gates.assert_task_bindings("old-task", (new_binding,))
        await gates.bind_task(
            build_task_version_binding_manifest(
                task_id="new-task", bindings=(new_binding,), locked_at=datetime.now(UTC)
            )
        )
        await gates.assert_task_bindings("new-task", (new_binding,))

    with serving(Peer("2025-11-25")) as profile:
        asyncio.run(run(profile))


def test_real_adapter_preview_result_survives_operation_json_and_response_projection():
    from app.infra.orchestration.agent_adapter import _workflow_execution_result
    from app.mcp.contracts import validate_input
    from app.ports.workflow_store import GovernedWorkflowAuthorization

    data = {"artifactId": "a" * 32, "payloadHash": "b" * 64, "internal": 7}
    contract = OutputContract(
        "synthetic",
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
        "synthetic",
        postcondition=lambda value: value == data,
    )

    class PreviewPeer(BusinessPeer):
        def reply(self, request):
            result = super().reply(request)
            if request["method"] == "tools/call":
                result["result"] = {
                    "structuredContent": data,
                    "content": [{"type": "text", "text": json.dumps(data)}],
                }
            return result

    peer = PreviewPeer("2025-11-25")
    with serving(peer) as profile:

        async def run():
            specs, mappings = catalog(profile, {"clothing_plan_preview": contract})
            outer = next(
                spec for spec in specs if spec.type == "workflow" and spec.status == "active"
            )
            mapping = next(
                item
                for item in mappings
                if item.internal_only and item.remote_tool == "clothing_plan_preview"
            )

            class McpStore(Tokens):
                async def mapping(self, capability_id, version):
                    return mapping

            class Permission:
                consumes = 0

                async def consume(self, authorization, context, **kwargs):
                    self.consumes += 1

            permit = Permission()
            context = authorization().model_copy(
                update={
                    "capability_id": mapping.capability_id,
                    "capability_version": mapping.capability_version,
                    "workflow_authorization_ref": "attempt",
                    "operation_id": "a" * 32,
                }
            )
            store = McpStore()
            adapter = BusinessMcpAdapter(
                McpDriver({profile.service_config_id: profile}, store),
                store,
                {(profile.service_config_id, "clothing_plan_preview"): contract},
                isolated_test_contracts=True,
                workflows=permit,
            )
            result = await adapter.execute(
                mapping.capability_id,
                VALID["clothing_plan_preview"],
                {
                    "mcp_authorization": context,
                    "workflow_authorization": GovernedWorkflowAuthorization(
                        operation_id="a" * 32,
                        attempt_id="attempt",
                        expected_revision=1,
                    ),
                },
            )
            assert result.status == "success" and permit.consumes == peer.effects == 1
            assert result.mcp_outcome.persistence == data
            expected = {"artifactId": data["artifactId"], "payloadHash": data["payloadHash"]}
            assert result.mcp_outcome.public_result == expected
            h = await fixture()
            saved = h.op.model_copy(
                update={
                    "state": "VERIFIED_SUCCESS",
                    "public_result": result.mcp_outcome.public_result,
                    "safe_output": data,
                }
            )
            reloaded = WorkflowOperation.model_validate_json(saved.model_dump_json())
            execution = _workflow_execution_result(GovernedOperations.result(reloaded, "trace"))
            projected = project_response_data(execution.data, outer.output_schema)
            assert projected["result"] == expected and "internal" not in projected["result"]
            from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
            from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
            from app.ports.agent_orchestration import AgentResponseContext
            from app.ports.response_projection_contract import ProjectionContractSnapshot

            orchestration = AgentOrchestrationAdapter(
                capability_registry=None,
                gateway=None,
                workflow_engine=None,
                response_builder=ResponseEnvelopeBuilder(),
            )
            envelope = orchestration.build_response(
                context=AgentResponseContext(
                    response_id="response",
                    task_id="task",
                    session_id="chat",
                    trace_id="trace",
                    capability_id=outer.capability_id,
                ),
                execution=execution,
                projection=ProjectionContractSnapshot.from_capability(outer),
            )
            assert envelope.status == "completed" and envelope.data == projected
            validate_input("clothing_plan_submit", projected["result"], now=datetime.now(UTC))
            validate_input(
                "clothing_result_get",
                {"artifactId": projected["result"]["artifactId"]},
                now=datetime.now(UTC),
            )
            api = McpApiService(
                SimpleNamespace(configs={profile.service_config_id: profile}), h.operations, None
            )
            assert api.view(reloaded).result == expected
            assert api.view(reloaded.model_copy(update={"state": "FAILED"})).result is None
            assert api.view(reloaded.model_copy(update={"public_result": None})).result is None

        asyncio.run(run())
