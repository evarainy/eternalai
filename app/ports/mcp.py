"""MCP authorization and driver boundaries. No SDK or infrastructure types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict


class McpAuthorizationContext(BaseModel):
    """Constructed by Gateway from authenticated state, never parsed from arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    user_id: str
    login_session_fingerprint: str
    task_id: str
    chat_session_id: str
    target_system: str = "business_platform"
    service_config_id: str
    service_config_version: int
    connection_id: str
    binding_epoch: int
    grant_epoch: int
    registration_id: str
    capability_id: str
    capability_version: str
    operation_id: str | None = None
    workflow_authorization_ref: str | None = None


class McpTaskRuleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str
    user_id: str
    service_config_id: str
    task_id: str
    person_id: str
    week_start: str
    policy_version: str


class McpTaskRulesPort(Protocol):
    async def resolve(
        self, context: McpAuthorizationContext, task_id: str
    ) -> McpTaskRuleEvidence | None:
        """Approved company rule projection for this principal and original task."""
        ...


class McpValidatedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["VERIFIED_SUCCESS", "WAITING_EXTERNAL_CONFIRM"]
    persistence: dict[str, Any]
    public_result: dict[str, Any] | None = None
    review_url: str | None = None


class McpValidatedRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data: dict[str, Any]


class McpSubmitPermit(BaseModel):
    """Verified provider evidence, supplied only by the configured trusted port."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: McpAuthorizationContext
    remote_tool: Literal["clothing_plan_submit", "talk_record_submit"]
    arguments_digest: str
    basis: Literal["verified_personal_confirmation", "approved_atomic_rejection"]
    evidence_digest: str
    policy_version: str
    valid_until: datetime


class McpSubmitPreconditionPort(Protocol):
    approved: bool
    synthetic: bool
    version: str

    async def verify(
        self, context: McpAuthorizationContext, tool: str, arguments: dict[str, Any]
    ) -> McpSubmitPermit | None:
        """Verify personal confirmation or an approved atomic rejection guarantee."""
        ...

    async def artifact_deadline(
        self, context: McpAuthorizationContext, tool: str, arguments: dict[str, Any]
    ) -> datetime | None:
        """Original provider artifact deadline; unknown is never an invented TTL."""
        ...


class McpDriverPort(Protocol):
    async def call(
        self,
        context: McpAuthorizationContext,
        tool: str,
        arguments: dict[str, Any],
        *,
        input_digest: str,
        safety_digest: str,
        write: bool,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]: ...


class McpTokenResolverPort(Protocol):
    async def resolve(self, context: McpAuthorizationContext) -> str:
        """Recheck current ownership/epochs/expiry immediately before HTTP dispatch."""
        ...

    async def reject(self, context: McpAuthorizationContext) -> None:
        """Invalidate only this matching current grant after verified HTTP 401/403."""
        ...


class McpOAuthHttpPort(Protocol):
    async def post(
        self, endpoint: str, payload: dict[str, Any], *, form: bool
    ) -> dict[str, Any]: ...
