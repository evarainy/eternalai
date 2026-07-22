"""Minimal pure-logic PolicyGuard implementation for Phase 0."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.ports.policy_guard import (
    ManagementPlanePolicyContext,
    PolicyDecision,
    PolicyGuardPort,
    PolicyRequestContext,
)


class MinimalPolicyGuard(PolicyGuardPort):
    """Deterministic minimal policy skeleton used by downstream gateway tests."""

    def __init__(
        self,
        admin_capability_ids: Collection[str] = (),
    ) -> None:
        self._admin_capability_ids = frozenset(admin_capability_ids)

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: PolicyRequestContext,
    ) -> PolicyDecision:
        if arguments is None:
            return PolicyDecision(
                decision="deny",
                reason_code="policy_denied",
            )
        if capability_id.startswith("admin_"):
            if not isinstance(request_context, ManagementPlanePolicyContext):
                return PolicyDecision(
                    decision="deny",
                    reason_code="role_not_allowed",
                )
            if "admin" not in request_context.roles:
                return PolicyDecision(
                    decision="deny",
                    reason_code="role_not_allowed",
                )
            if capability_id in self._admin_capability_ids:
                return PolicyDecision(decision="allow")
            return PolicyDecision(
                decision="deny",
                reason_code="admin_action_not_allowed",
            )
        if capability_id.endswith("_confirm"):
            return PolicyDecision(
                decision="confirm",
                reason_code="high_risk_action_requires_confirm",
                required_action="confirm",
            )
        return PolicyDecision(decision="allow")
