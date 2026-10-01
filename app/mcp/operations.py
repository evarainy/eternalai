"""Durable governed workflow coordinator; no six-write automatic replay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping
from uuid import uuid4

from app.mcp.connections import McpExecutionContextFactory
from app.mcp.contracts import NON_IDEMPOTENT_TOOLS, SUBMIT_TOOLS, validate_input
from app.mcp.models import McpFailure, digest
from app.ports.auth import authenticated_session
from app.ports.capability_gateway import CapabilityGatewayPort, RequestOrgContext
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.human_gate import HumanGateConflictError, HumanGatePort, HumanGateRequest
from app.ports.mcp import McpAuthorizationContext, McpSubmitPreconditionPort
from app.ports.policy_guard import PolicyGuardPort
from app.ports.workflow_store import (
    GovernedWorkflowAuthorization,
    McpRecoveryPolicyPort,
    WorkflowOperation,
    WorkflowStorePort,
)
from app.workflow.models import (
    GovernedWorkflowDefinition,
    GovernedWorkflowPolicy,
    WorkflowDefinition,
    WorkflowRunResult,
    WorkflowRunStatus,
)


class GovernedOperations:
    def __init__(
        self,
        store: WorkflowStorePort,
        contexts: McpExecutionContextFactory,
        gates: HumanGatePort,
        gateway: CapabilityGatewayPort,
        policy: PolicyGuardPort,
        manifests: Mapping[str, GovernedWorkflowPolicy],
        recovery_policies: Mapping[tuple[str, str], McpRecoveryPolicyPort] | None = None,
        submit_preconditions: Mapping[tuple[str, str], McpSubmitPreconditionPort] | None = None,
        registry: CapabilityRegistryPort | None = None,
    ) -> None:
        self.store, self.contexts, self.gates = store, contexts, gates
        self.gateway, self.policy, self.manifests = gateway, policy, dict(manifests)
        self.recovery_policies = dict(recovery_policies or {})
        self.submit_preconditions = dict(submit_preconditions or {})
        self.registry = registry

    def admitted(self, definition: WorkflowDefinition) -> bool:
        if type(definition) is not GovernedWorkflowDefinition or definition.policy is None:
            return False
        policy = definition.policy
        return (
            self.manifests.get(definition.workflow_id) == policy
            and policy.outer_capability_id == definition.workflow_id
            and policy.version == definition.version
            and policy.retry_limit == 0
            and policy.risk == "high"
            and policy.confirmation_required is True
            and len(definition.steps) == 1
            and definition.steps[0].capability_id == policy.leaf_capability_id
            and definition.steps[0].confirmed_capability_id is None
            and definition.steps[0].when is None
            and not definition.steps[0].static_arguments
        )

    async def start(
        self,
        definition: WorkflowDefinition,
        *,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        initial_input: Mapping[str, Any],
        request_context: RequestOrgContext,
    ) -> WorkflowRunResult:
        if not self.admitted(definition) or not isinstance(definition, GovernedWorkflowDefinition):
            raise McpFailure("mcp_workflow_unregistered")
        policy = definition.policy
        assert policy is not None
        if await self.store.by_task(task_id) is not None:
            raise McpFailure("mcp_operation_conflict")
        decision = await self.policy.decide(
            ai_user_id, definition.workflow_id, dict(initial_input), request_context
        )
        if decision.decision != "confirm":
            raise McpFailure("mcp_workflow_authorization_invalid")
        mapping = await self.contexts.store.mapping(policy.leaf_capability_id, policy.version)
        if (
            mapping is None
            or not mapping.internal_only
            or mapping.remote_tool != policy.remote_tool
            or mapping.service_config_id != policy.service_config_id
        ):
            raise McpFailure("mcp_workflow_unregistered")
        validate_input(policy.remote_tool, dict(initial_input), now=datetime.now(UTC))
        context = await self.contexts.build(
            task_id=task_id,
            chat_session_id=session_id,
            user_id=ai_user_id,
            tenant_id=request_context.tenant_id,
            mapping=mapping,
        )
        operation_id = uuid4().hex
        context = context.model_copy(update={"operation_id": operation_id})
        binding = await self.gates.get_task_binding(task_id)
        if binding is None:
            raise McpFailure("mcp_workflow_authorization_invalid")
        session = authenticated_session.get()
        assert session is not None
        expires = min(session.expires_at, datetime.now(UTC) + timedelta(minutes=10))
        connection = await self.contexts.store.connection(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            service_config_id=context.service_config_id,
        )
        if connection is None or not connection.identity_evidence:
            raise McpFailure("mcp_authorization_invalid")
        artifact_deadline = None
        submit_policy = self.submit_preconditions.get(
            (context.service_config_id, policy.remote_tool)
        )
        if submit_policy is not None and submit_policy.approved and submit_policy.version:
            artifact_deadline = await submit_policy.artifact_deadline(
                context, policy.remote_tool, dict(initial_input)
            )
            if artifact_deadline is not None and (
                artifact_deadline.tzinfo is None or artifact_deadline <= datetime.now(UTC)
            ):
                raise McpFailure("mcp_artifact_expired")
        recovery_policy = self.recovery_policies.get(
            (context.service_config_id, policy.remote_tool)
        )
        if (
            artifact_deadline is None
            and recovery_policy is not None
            and recovery_policy.approved
            and recovery_policy.version
        ):
            artifact_deadline = await recovery_policy.artifact_deadline(
                context, policy.remote_tool, dict(initial_input)
            )
            if artifact_deadline is not None and (
                artifact_deadline.tzinfo is None or artifact_deadline <= datetime.now(UTC)
            ):
                raise McpFailure("mcp_artifact_expired")
        if artifact_deadline is not None:
            expires = min(expires, artifact_deadline)
        operation = WorkflowOperation(
            operation_id=operation_id,
            context=context,
            outer_capability_id=definition.workflow_id,
            outer_version=definition.version,
            leaf_capability_id=policy.leaf_capability_id,
            leaf_version=policy.version,
            remote_tool=policy.remote_tool,
            arguments=dict(initial_input),
            canonical_args_digest=digest(initial_input),
            expires_at=expires,
            artifact_expires_at=artifact_deadline,
            argument_preview=await self._preview(
                definition.workflow_id, definition.version, initial_input
            ),
            identity_evidence_digest=digest(connection.identity_evidence),
            external_confirmation_required=policy.remote_tool in SUBMIT_TOOLS,
            action_digest=digest(
                {
                    "context": context.model_dump(),
                    "arguments": dict(initial_input),
                    "manifest": binding.manifest_digest,
                    "outer": definition.workflow_id,
                    "outer_version": definition.version,
                    "mapping": mapping.model_dump(),
                }
            ),
        )
        operation = await self.store.create(operation)
        operation, _ = await self.ensure_confirmation(operation)
        return self.result(operation, request_context.request_id)

    @staticmethod
    def lifecycle_owner(op: WorkflowOperation) -> None:
        session = authenticated_session.get()
        if (
            session is None
            or session.expires_at <= datetime.now(UTC)
            or session.principal.ai_user_id != op.context.user_id
            or session.principal.org_ctx.tenant_id != op.context.tenant_id
        ):
            raise McpFailure("mcp_authorization_invalid")

    async def _gate(
        self,
        op: WorkflowOperation,
        context: McpAuthorizationContext,
        action_digest: str,
        expires: datetime,
        *,
        legacy: bool = False,
    ) -> HumanGateRequest:
        manifest = await self.gates.get_task_binding(op.context.task_id)
        if manifest is None:
            raise McpFailure("mcp_workflow_authorization_invalid")
        request_digest = digest({"operation_id": op.operation_id, "action_digest": action_digest})
        request_id = request_digest[:32]
        if legacy and op.gate_request_id:
            request = await self.gates.get_request(op.gate_request_id)
            if request is None:
                raise McpFailure("mcp_checkpoint_inconsistent")
        elif legacy:
            request = await self.store.confirmation(op)
        else:
            request = await self.gates.get_request(request_id)
        if request is None:
            candidate = HumanGateRequest(
                request_id=request_id,
                task_id=context.task_id,
                requested_for_ai_user_id=context.user_id,
                requested_session_id=context.chat_session_id,
                requested_tenant_id=context.tenant_id,
                action_digest=action_digest,
                request_digest=request_digest,
                binding_manifest_digest=manifest.manifest_digest,
                requested_at=datetime.now(UTC),
                expires_at=expires,
            )
            try:
                request = await self.gates.create_request(candidate)
            except HumanGateConflictError:
                request = await self.gates.get_request(request_id)
                if request is None:
                    raise
        if (
            request.task_id != context.task_id
            or request.requested_for_ai_user_id != context.user_id
            or request.requested_session_id != context.chat_session_id
            or request.requested_tenant_id != context.tenant_id
            or request.action_digest != action_digest
            or request.binding_manifest_digest != manifest.manifest_digest
            or (request.request_id == request_id and request.request_digest != request_digest)
            or len(request.request_digest) != 64
            or request.requested_at > datetime.now(UTC)
            or request.expires_at > expires
        ):
            raise McpFailure("mcp_checkpoint_inconsistent")
        return request

    async def ensure_confirmation(
        self,
        op: WorkflowOperation,
    ) -> tuple[WorkflowOperation, HumanGateRequest]:
        await self.owned(op)
        if op.state != "WAITING_LOCAL_CONFIRM":
            raise McpFailure("mcp_confirmation_unavailable")
        request = await self._gate(op, op.context, op.action_digest, op.expires_at, legacy=True)
        if request.expires_at <= datetime.now(UTC):
            await self.store.transition(op, state="EXPIRED")
            raise McpFailure("mcp_confirmation_expired")
        if op.gate_request_id != request.request_id or op.expires_at != request.expires_at:
            op = await self.store.transition(
                op,
                state="WAITING_LOCAL_CONFIRM",
                gate_request_id=request.request_id,
                renewed_context=op.context,
                renewed_action_digest=op.action_digest,
                renewed_gate_expires_at=request.expires_at,
            )
        return op, request

    async def retire_owned_confirmation(
        self,
        task_id: str,
        expected_action_digest: str,
        expected_gate_request_id: str,
        reason: str,
    ) -> bool | Literal["superseded"]:
        op = await self.store.by_task(task_id)
        if op is None:
            return False
        self.lifecycle_owner(op)
        async with self.store.execution_guard(op):
            current = await self.store.by_task(task_id)
            if current is None:
                raise McpFailure("mcp_operation_unavailable")
            self.lifecycle_owner(current)
            if current.action_digest != expected_action_digest:
                old = await self.gates.get_request(expected_gate_request_id)
                new = (
                    await self.gates.get_request(current.gate_request_id)
                    if current.gate_request_id
                    else None
                )
                manifest = await self.gates.get_task_binding(task_id)
                # A trusted expired gate may have been explicitly replaced while
                # its task/trace cleanup was interrupted. Audit it without retiring
                # the new generation or invoking ordinary discard.
                if (
                    reason == "expired"
                    and old is not None
                    and new is not None
                    and manifest is not None
                    and old.request_id != new.request_id
                    and old.action_digest == expected_action_digest
                    and old.expires_at <= datetime.now(UTC)
                    and new.requested_at >= old.expires_at
                    and new.action_digest == current.action_digest
                    and new.expires_at == current.expires_at
                    and all(
                        request.task_id == task_id
                        and request.requested_for_ai_user_id == current.context.user_id
                        and request.requested_tenant_id == current.context.tenant_id
                        and request.requested_session_id == current.context.chat_session_id
                        and request.binding_manifest_digest == manifest.manifest_digest
                        for request in (old, new)
                    )
                ):
                    return "superseded"
                raise McpFailure("mcp_operation_conflict")
            request = (
                await self.gates.get_request(current.gate_request_id)
                if current.gate_request_id
                else await self.store.confirmation(current)
            )
            manifest = await self.gates.get_task_binding(task_id)
            if (
                request is None
                or manifest is None
                or request.request_id != expected_gate_request_id
                or request.task_id != task_id
                or request.action_digest != expected_action_digest
                or request.requested_for_ai_user_id != current.context.user_id
                or request.requested_tenant_id != current.context.tenant_id
                or request.requested_session_id != current.context.chat_session_id
                or request.binding_manifest_digest != manifest.manifest_digest
            ):
                raise McpFailure("mcp_operation_conflict")
            if current.state in {"WAITING_LOCAL_CONFIRM", "READY"} and not current.send_started:
                if reason == "expired" and current.expires_at > datetime.now(UTC):
                    raise McpFailure("mcp_confirmation_not_expired")
                await self.store.transition(
                    current,
                    state="EXPIRED" if reason == "expired" else "CANCELLED",
                )
            elif current.state not in {"VERIFIED_SUCCESS", "FAILED", "CANCELLED", "EXPIRED"}:
                raise McpFailure("mcp_outcome_unknown")
        return True

    async def _preview(
        self, capability_id: str, version: str, arguments: Mapping[str, Any]
    ) -> dict[str, str | int | float | bool | None]:
        capability = await self.registry.get(capability_id) if self.registry else None
        if capability is None or capability.version != version:
            return {}
        return {
            field: value
            for field in capability.displayable_argument_fields
            if field in arguments
            and (
                (value := arguments[field]) is None
                or type(value) in (bool, int, float)
                or (
                    isinstance(value, str)
                    and len(value) <= 200
                    and not any(ord(c) < 32 for c in value)
                )
            )
        }

    async def owned(self, operation: WorkflowOperation) -> None:
        context = operation.context
        session = authenticated_session.get()
        if (
            session is None
            or session.principal.ai_user_id != context.user_id
            or session.principal.org_ctx.tenant_id != context.tenant_id
            or session.fingerprint.hex() != context.login_session_fingerprint
            or session.expires_at <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_authorization_invalid")
        await self.contexts.store.resolve(context)
        policy = self.manifests.get(operation.outer_capability_id)
        if (
            policy is None
            or policy.version != operation.outer_version
            or policy.leaf_capability_id != operation.leaf_capability_id
            or policy.service_config_id != context.service_config_id
            or policy.remote_tool != operation.remote_tool
            or digest(operation.arguments) != operation.canonical_args_digest
        ):
            raise McpFailure("mcp_checkpoint_inconsistent")

    async def resume(
        self,
        task_id: str,
        *,
        confirmed: bool,
        expected_action_digest: str | None,
    ) -> WorkflowRunResult:
        op = await self.store.by_task(task_id)
        if op is None:
            raise McpFailure("mcp_operation_unavailable")
        async with self.store.execution_guard(op):
            current = await self.store.by_task(task_id)
            if current is None:
                raise McpFailure("mcp_operation_unavailable")
            return await self._resume_locked(current, confirmed, expected_action_digest)

    async def _resume_locked(
        self, op: WorkflowOperation, confirmed: bool, expected_action_digest: str | None
    ) -> WorkflowRunResult:
        await self.owned(op)
        if op.state == "SENDING":
            op = await self.store.transition(op, state="UNKNOWN")
        if op.state != "WAITING_LOCAL_CONFIRM":
            return self.result(op, op.operation_id)
        if op.expires_at <= datetime.now(UTC):
            return self.result(await self.store.transition(op, state="EXPIRED"), op.operation_id)
        if not confirmed or expected_action_digest != op.action_digest:
            raise McpFailure("mcp_workflow_authorization_invalid")
        request = (
            await self.gates.get_request(op.gate_request_id)
            if op.gate_request_id
            else await self.store.confirmation(op)
        )
        if request is None:
            raise McpFailure("mcp_workflow_authorization_invalid")
        decision = await self.gates.get_decision(request.request_id)
        if (
            decision is None
            or decision.decision != "confirmed"
            or decision.request_digest != request.request_digest
            or request.expires_at <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_workflow_authorization_invalid")
        op = await self.store.transition(
            op, state="READY", gate_request_id=request.request_id, attempt_id=uuid4().hex
        )
        assert op.attempt_id is not None
        authorization = GovernedWorkflowAuthorization(
            operation_id=op.operation_id, attempt_id=op.attempt_id, expected_revision=op.revision
        )
        session = authenticated_session.get()
        assert session is not None
        result = await self.gateway.execute_capability(
            op.context.task_id,
            op.context.chat_session_id,
            op.context.user_id,
            op.leaf_capability_id,
            op.arguments,
            RequestOrgContext(**session.principal.org_ctx.model_dump(), request_id=op.operation_id),
            workflow_authorization=authorization,
        )
        current = await self.store.load(
            op.operation_id, tenant_id=op.context.tenant_id, user_id=op.context.user_id
        )
        if current is None:
            raise McpFailure("mcp_checkpoint_inconsistent")
        if current.state == "SENDING":
            if result.status == "completed" and result.mcp_outcome is not None:
                current = await self.store.transition(
                    current,
                    state=result.mcp_outcome.state,
                    safe_output=result.mcp_outcome.persistence,
                    public_result=result.mcp_outcome.public_result,
                    review_url=result.mcp_outcome.review_url,
                )
            else:
                current = await self.store.transition(current, state="UNKNOWN")
        elif current.state == "READY":
            # No send permission consumed; this attempt is closed, never automatically replayed.
            current = await self.store.transition(current, state="CANCELLED")
        return self.result(current, op.operation_id)

    async def recover(self, op: WorkflowOperation) -> WorkflowOperation:
        """Explicit recovery only; an active sender retains the same advisory lock."""
        await self.owned(op)
        async with self.store.execution_guard(op):
            current = await self.store.load(
                op.operation_id, tenant_id=op.context.tenant_id, user_id=op.context.user_id
            )
            if current is None or current.revision != op.revision:
                raise McpFailure("mcp_operation_conflict")
            await self.owned(current)
            if current.state == "READY" and not current.send_started:
                # This confirmed attempt never consumed permission. Close it permanently;
                # recovery must neither replay it nor reuse its old confirmation.
                return await self.store.transition(current, state="CANCELLED")
            if current.state != "SENDING":
                raise McpFailure("mcp_recovery_denied")
            return await self.store.transition(current, state="UNKNOWN")

    async def discard(self, task_id: str) -> bool:
        op = await self.store.by_task(task_id)
        if op is None:
            return False
        async with self.store.execution_guard(op):
            current = await self.store.by_task(task_id)
            if current is None:
                return False
            await self.owned(current)
            if current.state in {"WAITING_LOCAL_CONFIRM", "READY"}:
                await self.store.transition(current, state="CANCELLED")
        return True

    def _recovery_policy(self, op: WorkflowOperation) -> McpRecoveryPolicyPort:
        policy = self.recovery_policies.get((op.context.service_config_id, op.remote_tool))
        if policy is None or not policy.approved or not policy.version:
            raise McpFailure("mcp_recovery_contract_unconfirmed")
        return policy

    async def reconcile(self, op: WorkflowOperation) -> WorkflowOperation:
        await self.owned(op)
        policy = self._recovery_policy(op)
        if op.remote_tool in NON_IDEMPOTENT_TOOLS:
            raise McpFailure("mcp_manual_reconciliation_required")
        if op.state == "SENDING":
            op = await self.recover(op)
        if op.state not in {"UNKNOWN", "WAITING_EXTERNAL_CONFIRM"}:
            raise McpFailure("mcp_recovery_denied")
        arguments: dict[str, Any] | None
        if op.remote_tool == "clothing_plan_submit":
            arguments = {"artifactId": op.arguments["artifactId"]}
        elif op.remote_tool == "talk_task_claim":
            arguments = {"view": "mine"}
        else:
            record_id = policy.record_id(op)
            arguments = {"recordId": record_id} if record_id is not None else None
        verified_read = None
        if arguments is not None:
            manifest = self.manifests[op.outer_capability_id]
            if manifest.recovery_capability_id is None:
                raise McpFailure("mcp_recovery_denied")
            session = authenticated_session.get()
            assert session is not None
            result = await self.gateway.execute_capability(
                op.context.task_id,
                op.context.chat_session_id,
                op.context.user_id,
                manifest.recovery_capability_id,
                arguments,
                RequestOrgContext(
                    **session.principal.org_ctx.model_dump(), request_id=op.operation_id
                ),
            )
            if result.status != "completed" or result.mcp_read is None:
                raise McpFailure("mcp_recovery_read_unconfirmed")
            verified_read = result.mcp_read.data
        decision = await policy.reconcile(op, verified_read)
        if decision.state == "verified":
            return await self.store.transition(
                op, state="VERIFIED_SUCCESS", safe_output=decision.persistence
            )
        if decision.state == "failed":
            return await self.store.transition(op, state="FAILED", safe_output=decision.persistence)
        if decision.state == "pending":
            return op
        return await self._renew_confirmation(op, op.context, policy.version)

    async def takeover(self, op: WorkflowOperation) -> WorkflowOperation:
        async with self.store.execution_guard(op):
            current = await self.store.load(
                op.operation_id, tenant_id=op.context.tenant_id, user_id=op.context.user_id
            )
            if current is None or current.revision != op.revision:
                raise McpFailure("mcp_operation_conflict")
            return await self._takeover_locked(current)

    async def _takeover_locked(self, op: WorkflowOperation) -> WorkflowOperation:
        policy = self._recovery_policy(op)
        session = authenticated_session.get()
        if (
            session is None
            or session.principal.ai_user_id != op.context.user_id
            or session.principal.org_ctx.tenant_id != op.context.tenant_id
            or session.expires_at <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_authorization_invalid")
        mapping = await self.contexts.store.mapping(op.leaf_capability_id, op.leaf_version)
        if mapping is None:
            raise McpFailure("mcp_authorization_invalid")
        context = await self.contexts.build(
            task_id=op.context.task_id,
            chat_session_id=op.context.chat_session_id,
            user_id=op.context.user_id,
            tenant_id=op.context.tenant_id,
            mapping=mapping,
        )
        context = context.model_copy(update={"operation_id": op.operation_id})
        connection = await self.contexts.store.connection(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            service_config_id=context.service_config_id,
        )
        if (
            connection is None
            or not connection.identity_evidence
            or not policy.same_subject(op.identity_evidence_digest, connection.identity_evidence)
            or context.registration_id != op.context.registration_id
        ):
            raise McpFailure("mcp_authorization_invalid")
        if op.state == "SENDING":
            op = await self.store.transition(op, state="UNKNOWN")
        if op.remote_tool in NON_IDEMPOTENT_TOOLS and op.send_started:
            raise McpFailure("mcp_manual_reconciliation_required")
        if op.send_started:
            rebound = await self.store.transition(
                op,
                state="UNKNOWN",
                renewed_context=context,
                renewed_action_digest=digest(
                    {
                        "previous_action": op.action_digest,
                        "context": context.model_dump(),
                        "recovery_policy": policy.version,
                    }
                ),
            )
            return await self.reconcile(rebound)
        # Takeover alone never consumes an execution permission or sends a write.
        return await self._renew_confirmation(op, context, policy.version)

    async def _renew_confirmation(
        self, op: WorkflowOperation, context: McpAuthorizationContext, policy_version: str
    ) -> WorkflowOperation:
        now = datetime.now(UTC)
        session = authenticated_session.get()
        if session is None or session.expires_at <= now:
            raise McpFailure("mcp_authorization_invalid")
        if op.send_started and op.artifact_expires_at is None:
            raise McpFailure("mcp_artifact_deadline_unconfirmed")
        expires = min(session.expires_at, now + timedelta(minutes=10))
        if op.artifact_expires_at is not None:
            if op.artifact_expires_at <= now:
                raise McpFailure("mcp_artifact_expired")
            expires = min(expires, op.artifact_expires_at)
        action_digest = digest(
            {
                "original_action": op.action_digest,
                "revision": op.revision,
                "context": context.model_dump(),
                "recovery_policy": policy_version,
            }
        )
        request = await self._gate(op, context, action_digest, expires)
        now = datetime.now(UTC)
        if request.expires_at <= now:
            if op.state == "WAITING_LOCAL_CONFIRM" and op.expires_at <= now:
                await self.store.transition(op, state="EXPIRED")
            raise McpFailure("mcp_confirmation_expired")
        return await self.store.transition(
            op,
            state="WAITING_LOCAL_CONFIRM",
            renewed_context=context,
            renewed_action_digest=action_digest,
            renewed_gate_expires_at=request.expires_at,
            gate_request_id=request.request_id,
        )

    @staticmethod
    def result(op: WorkflowOperation, trace_id: str) -> WorkflowRunResult:
        status: WorkflowRunStatus = (
            "completed"
            if op.state == "VERIFIED_SUCCESS"
            else ("waiting_confirm" if op.state == "WAITING_LOCAL_CONFIRM" else "failed")
        )
        return WorkflowRunResult(
            workflow_id=op.outer_capability_id,
            workflow_version=op.outer_version,
            trace_id=trace_id,
            status=status,
            output={
                "operation_id": op.operation_id,
                "state": op.state,
                "result": op.public_result if op.state == "VERIFIED_SUCCESS" else None,
            },
            step_outputs={},
            error_code=None
            if status in {"completed", "waiting_confirm"}
            else "mcp_outcome_unknown"
            if op.state in {"UNKNOWN", "WAITING_EXTERNAL_CONFIRM", "SENDING"}
            else "policy_denied",
        )
