"""Server-only construction of service-scoped execution identity."""

from __future__ import annotations

from datetime import UTC, datetime

from app.mcp.diagnostics import GrantReason, record_mcp_event, remaining_seconds
from app.mcp.models import McpFailure, ToolBinding, digest
from app.ports.auth import authenticated_session
from app.ports.human_gate import HumanGatePort
from app.ports.mcp import McpAuthorizationContext
from app.ports.mcp_store import Connection, McpStorePort
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
        connection: Connection | None = None

        def blocked(code: str, reason: GrantReason | None) -> McpFailure:
            record_mcp_event(
                "identity_check",
                "failed",
                task_id=task_id,
                connection_id=connection.connection_id if connection else "",
                error_code=code,
                remaining_s=remaining_seconds(connection.expires_at) if connection else None,
                reason=reason,
            )
            return McpFailure(code)

        session = authenticated_session.get()
        if session is None:
            raise blocked("mcp_authorization_invalid", "session_missing")
        if (
            session.principal.ai_user_id != user_id
            or session.principal.org_ctx.tenant_id != tenant_id
        ):
            raise blocked("mcp_authorization_invalid", "principal_mismatch")
        if session.expires_at <= datetime.now(UTC):
            raise blocked("mcp_authorization_invalid", "session_expired")
        connection = await self.store.connection(
            tenant_id=tenant_id, user_id=user_id, service_config_id=mapping.service_config_id
        )

        if connection is None:
            raise blocked("mcp_identity_unbound", "unbound")
        if connection.login_session_fingerprint != session.fingerprint.hex():
            raise blocked("mcp_authorization_invalid", "session_mismatch")
        if connection.state in {"AUTHORIZING", "PENDING_IDENTITY"}:
            raise blocked("mcp_identity_pending", "pending")
        if connection.state == "REVOKED":
            raise blocked("mcp_identity_revoked", "revoked")
        if connection.state == "DISCONNECTED":
            raise blocked("mcp_identity_expired", "disconnected")
        if connection.state == "EXPIRED" or (
            connection.expires_at is not None and connection.expires_at <= datetime.now(UTC)
        ):
            raise blocked("mcp_identity_expired", "grant_expired")
        if connection.state != "ACTIVE" or not connection.identity_evidence:
            raise blocked("mcp_authorization_invalid", None)
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
        try:
            await self.store.resolve(context)
        except McpFailure as exc:
            # resolve folds revocation, row mismatch and decrypt errors into one code.
            blocked(exc.code, "resolve_failed")
            raise
        record_mcp_event(
            "identity_check",
            "success",
            task_id=task_id,
            connection_id=connection.connection_id,
            remaining_s=remaining_seconds(connection.expires_at),
        )
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
