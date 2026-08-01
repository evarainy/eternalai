"""Identity mapping interface contract."""

from __future__ import annotations

from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict

from app.ports.capability_gateway import RequestOrgContext

TargetSystem: TypeAlias = Literal["oa", "u8", "hikvision_ivms"]
ExecutionIdentity: TypeAlias = Literal[
    "user_delegated",
    "system_scope",
    "admin_approved_proxy",
]
IdentityBindStatus: TypeAlias = Literal[
    "active",
    "unbound",
    "expired",
    "revoked",
    "verification_failed",
    "needs_binding_scope",
]


class IdentityCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bind_status: IdentityBindStatus
    binding_id: str | None = None
    target_system: TargetSystem
    execution_identity: ExecutionIdentity
    binding_scope: str | None = None
    account_set_id: str | None = None
    device_domain_id: str | None = None
    reason_code: str | None = None


class IdentityMappingMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping: IdentityCheckResult
    previous_bind_status: IdentityBindStatus
    changed: bool


class IdentityMappingMutationError(RuntimeError):
    """Report a safe identity-mapping mutation failure."""


class IdentityMappingPort(Protocol):
    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        execution_identity: ExecutionIdentity,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult: ...

    async def get_mapping(
        self,
        ai_user_id: str,
        target_system: TargetSystem,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> IdentityCheckResult | None: ...

    async def list_mappings(
        self,
        ai_user_id: str,
        target_system: TargetSystem | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> list[IdentityCheckResult]: ...

    async def revoke_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None: ...

    async def reset_mapping(
        self,
        binding_id: str,
    ) -> IdentityMappingMutationResult | None: ...
