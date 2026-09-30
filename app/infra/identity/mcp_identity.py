"""MCP identities delegate existing systems unchanged and never infer same-person."""

from __future__ import annotations

from datetime import UTC, datetime

from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityCheckResult,
    IdentityMappingMutationResult,
    IdentityMappingPort,
    TargetSystem,
)
from app.ports.mcp_store import McpStorePort


class McpIdentityMapping:
    def __init__(self, delegate: IdentityMappingPort, store: McpStorePort) -> None:
        self._delegate, self._store = delegate, store

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
        *,
        service_config_id: str | None = None,
    ) -> IdentityCheckResult:
        if target_system != "business_platform":
            return await self._delegate.resolve_execution_identity(
                ai_user_id, target_system, execution_identity, request_context
            )
        failed = IdentityCheckResult(
            bind_status="unbound",
            target_system=target_system,
            execution_identity=execution_identity,
        )
        if execution_identity != "user_delegated" or service_config_id is None:
            return failed
        connection = await self._store.connection(
            tenant_id=request_context.tenant_id,
            user_id=ai_user_id,
            service_config_id=service_config_id,
        )
        if connection is None or connection.state != "ACTIVE" or not connection.identity_evidence:
            return failed
        if connection.expires_at is None or connection.expires_at <= datetime.now(UTC):
            return failed.model_copy(update={"bind_status": "expired"})
        return failed.model_copy(
            update={"bind_status": "active", "binding_id": connection.connection_id}
        )

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
        *,
        tenant_id: str,
    ) -> IdentityCheckResult | None:
        if target_system == "business_platform":
            return None  # OAuth connections have their own authenticated, service-scoped API.
        return await self._delegate.get_mapping(
            ai_user_id,
            target_system,
            binding_scope,
            account_set_id,
            device_domain_id,
            tenant_id=tenant_id,
        )

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
        *,
        tenant_id: str,
    ) -> list[IdentityCheckResult]:
        if target_system == "business_platform":
            return []
        return await self._delegate.list_mappings(
            ai_user_id,
            target_system,
            binding_scope,
            account_set_id,
            device_domain_id,
            tenant_id=tenant_id,
        )

    async def revoke_mapping(
        self, binding_id: str, *, tenant_id: str
    ) -> IdentityMappingMutationResult | None:
        return await self._delegate.revoke_mapping(binding_id, tenant_id=tenant_id)

    async def reset_mapping(
        self, binding_id: str, *, tenant_id: str
    ) -> IdentityMappingMutationResult | None:
        return await self._delegate.reset_mapping(binding_id, tenant_id=tenant_id)
