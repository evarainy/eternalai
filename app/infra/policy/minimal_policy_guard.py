"""Minimal pure-logic PolicyGuard implementation for Phase 0."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.admin.actions import AUDIT_READER_ROLE
from app.ports.policy_guard import (
    CandidateVisibility,
    CapabilityCandidatePolicyPort,
    ManagementPlanePolicyContext,
    PolicyDecision,
    PolicyGuardPort,
    PolicyRequestContext,
)
from app.ports.request_context import RequestOrgContext


def _is_management_plane_capability(capability_id: str) -> bool:
    return capability_id.startswith("admin_")


class MinimalPolicyGuard(PolicyGuardPort, CapabilityCandidatePolicyPort):
    """Deterministic minimal policy skeleton used by downstream gateway tests."""

    def __init__(
        self,
        admin_capability_ids: Collection[str] = (),
        audit_read_capability_ids: Collection[str] = (),
    ) -> None:
        self._admin_capability_ids = frozenset(admin_capability_ids)
        self._audit_read_capability_ids = frozenset(audit_read_capability_ids)

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
        if _is_management_plane_capability(capability_id):
            if not isinstance(request_context, ManagementPlanePolicyContext):
                return PolicyDecision(
                    decision="deny",
                    reason_code="role_not_allowed",
                )
            if capability_id not in self._admin_capability_ids:
                return PolicyDecision(
                    decision="deny",
                    reason_code="admin_action_not_allowed",
                )
            required_role = (
                AUDIT_READER_ROLE
                if capability_id in self._audit_read_capability_ids
                else "admin"
            )
            if required_role in request_context.roles:
                return PolicyDecision(decision="allow")
            return PolicyDecision(
                decision="deny",
                reason_code="role_not_allowed",
            )
        if capability_id.endswith("_confirm"):
            return PolicyDecision(
                decision="confirm",
                reason_code="high_risk_action_requires_confirm",
                required_action="confirm",
            )
        return PolicyDecision(decision="allow")

    async def preview_capability(
        self,
        *,
        ai_user_id: str,
        capability_id: str,
        request_context: RequestOrgContext,
    ) -> CandidateVisibility:
        """Exclude only what ``decide`` always denies on the business plane."""
        if _is_management_plane_capability(capability_id) and not isinstance(
            request_context, ManagementPlanePolicyContext
        ):
            return "exclude"
        return "defer"
