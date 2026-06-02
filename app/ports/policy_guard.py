"""Policy guard interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict

from app.ports.capability_gateway import RequestOrgContext

PolicyDecisionValue: TypeAlias = Literal["allow", "deny", "confirm"]

PolicyRequiredAction: TypeAlias = Literal["confirm", "none"]


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecisionValue
    reason_code: str | None = None
    required_action: PolicyRequiredAction | None = None


class PolicyGuardPort(Protocol):
    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision: ...
