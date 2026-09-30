"""Server-only construction of service-scoped execution identity."""

from __future__ import annotations

from datetime import UTC, datetime

from app.mcp.models import McpFailure, ToolBinding, digest
from app.ports.auth import authenticated_session
from app.ports.human_gate import HumanGatePort
from app.ports.mcp import McpAuthorizationContext
from app.ports.mcp_store import McpStorePort
from app.ports.workflow_store import GovernedWorkflowAuthorization, WorkflowStorePort


class McpExecutionContextFactory:
    def __init__(
        self, store: McpStorePort, workflows: WorkflowStorePort, gates: HumanGatePort
    ) -> None:
        self.store, self.workflows, self.gates = store, workflows, gates

    async def build(
        self,
        *,
        task_id: str,
        chat_session_id: str,
        user_id: str,
        tenant_id: str,
        mapping: ToolBinding,
        authorization: GovernedWorkflowAuthorization | None = None,
    ) -> McpAuthorizationContext:
        session = authenticated_session.get()
        if (
            session is None
            or session.principal.ai_user_id != user_id
            or session.principal.org_ctx.tenant_id != tenant_id
            or session.expires_at <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_authorization_invalid")
        connection = await self.store.connection(
            tenant_id=tenant_id, user_id=user_id, service_config_id=mapping.service_config_id
        )
        if (
            connection is None
            or connection.state != "ACTIVE"
            or connection.login_session_fingerprint != session.fingerprint.hex()
        ):
            raise McpFailure("mcp_authorization_invalid")
        context = McpAuthorizationContext(
            tenant_id=tenant_id,
            user_id=user_id,
            login_session_fingerprint=session.fingerprint.hex(),
            task_id=task_id,
            chat_session_id=chat_session_id,
            service_config_id=mapping.service_config_id,
            service_config_version=connection.service_config_version,
            connection_id=connection.connection_id,
            binding_epoch=connection.binding_epoch,
            grant_epoch=connection.grant_epoch,
            registration_id=connection.registration_id,
            capability_id=mapping.capability_id,
            capability_version=mapping.capability_version,
            operation_id=authorization.operation_id if authorization else None,
            workflow_authorization_ref=authorization.attempt_id if authorization else None,
        )
        # Validate now; transport resolves the secret again immediately before send.
        await self.store.resolve(context)
        return context

    async def verify_write(
        self,
        context: McpAuthorizationContext,
        authorization: GovernedWorkflowAuthorization,
        arguments: dict[str, object],
    ) -> None:
        op = await self.workflows.load(
            authorization.operation_id, tenant_id=context.tenant_id, user_id=context.user_id
        )
        if (
            op is None
            or op.state != "READY"
            or op.context != context
            or op.revision != authorization.expected_revision
            or op.attempt_id != authorization.attempt_id
            or op.canonical_args_digest != digest(arguments)
            or op.expires_at <= datetime.now(UTC)
            or op.gate_request_id is None
        ):
            raise McpFailure("mcp_workflow_authorization_invalid")
        request = await self.gates.get_request(op.gate_request_id)
        decision = await self.gates.get_decision(op.gate_request_id)
        if (
            request is None
            or decision is None
            or decision.decision != "confirmed"
            or request.action_digest != op.action_digest
            or request.task_id != context.task_id
            or request.requested_for_ai_user_id != context.user_id
            or request.requested_tenant_id != context.tenant_id
            or request.requested_session_id != context.chat_session_id
            or request.expires_at <= datetime.now(UTC)
            or decision.request_digest != request.request_digest
            or decision.binding_manifest_digest != request.binding_manifest_digest
        ):
            raise McpFailure("mcp_workflow_authorization_invalid")
