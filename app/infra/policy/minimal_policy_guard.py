"""Minimal pure-logic PolicyGuard implementation for Phase 0."""

from __future__ import annotations

from typing import Any

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.ports.policy_guard import PolicyDecision, PolicyGuardPort
from app.ports.request_context import RequestOrgContext


class MinimalPolicyGuard(PolicyGuardPort):
    """Deterministic minimal policy skeleton used by downstream gateway tests."""

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        if arguments is None:
            return PolicyDecision(
                decision="deny",
                reason_code="policy_denied",
            )
        if capability_id.startswith("admin_"):
            if "admin" not in request_context.roles:
                return PolicyDecision(
                    decision="deny",
                    reason_code="role_not_allowed",
                )
            if capability_id in ADMIN_LITE_POLICY_CAPABILITY_IDS:
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
