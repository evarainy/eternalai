"""Fail-closed identity mapping used until the credential/binding slice lands."""

from __future__ import annotations

from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityCheckResult,
    TargetSystem,
)


class UnconfiguredIdentityMapping:
    """Expose no implicit principal, binding, or execution identity."""

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        return IdentityCheckResult(
            bind_status="unbound",
            target_system=target_system,
            execution_identity=execution_identity,
            reason_code="identity_unbound",
        )

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> IdentityCheckResult | None:
        return None

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> list[IdentityCheckResult]:
        return []


__all__ = ("UnconfiguredIdentityMapping",)
