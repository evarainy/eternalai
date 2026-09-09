"""Pure Work Object authorization over freshly resolved directory facts.

Callers must resolve the current principal's stable join key on every decision.
Dispatch endpoints and Trace emission are owned by DISPATCH-001. No directory I/O
or credentials belong here; a missing membership produces an explicit alert signal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ports.organization_directory import OrganizationDepartment, OrganizationUserMembership

_DEPARTMENT_HEAD_JOBTITLE_IDS: frozenset[str] = frozenset({"75", "380", "1405", "1701", "1999"})
_PRISON_AREA_DEPARTMENT_IDS: frozenset[str] = frozenset({
    "572", "575", "580", "585", "588", "589", "590", "591", "592", "593",
    "594", "595", "596", "597", "598", "599", "600", "601", "602", "603",
    "604", "605", "606", "607", "608", "619", "622", "1419", "1420", "1923",
})


class DispatchAuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["allow", "deny"]
    reason_code: str | None
    department_id: str | None
    dispatcher_department_type: Literal["prison_area", "office"]
    matched_rule: str
    alert_code: Literal["directory_membership_missing"] | None = None

    @property
    def trace_attributes(self) -> dict[str, str | None]:
        return {
            "department_id": self.department_id,
            "department_type": self.dispatcher_department_type,
            "matched_rule": self.matched_rule,
        }


def compute_dispatch_authorization(
    *,
    dispatcher_membership: OrganizationUserMembership | None,
    dispatcher_department: OrganizationDepartment | None,
    target_department_id: str,
) -> DispatchAuthorizationDecision:
    """Use lookup results, never token-baked department/job title or caller labels.

    A missing/mismatched department is unresolved, even when the membership's
    department ID is outside the enumerated list. Only resolved non-list IDs
    receive office authority. Missing membership is a distinct fail-closed alert.
    """
    department_id = dispatcher_membership.department_id if dispatcher_membership else None
    resolved = (
        bool(department_id and department_id.strip())
        and dispatcher_department is not None
        and dispatcher_department.department_id == department_id
    )
    department_type: Literal["prison_area", "office"] = "prison_area"
    matched_rule = "department_unresolved"
    if resolved:
        if department_id in _PRISON_AREA_DEPARTMENT_IDS:
            matched_rule = "prison_area_id"
        else:
            department_type = "office"
            matched_rule = "resolved_non_prison_id"

    reason: str | None = None
    alert: Literal["directory_membership_missing"] | None = None
    if dispatcher_membership is None:
        reason = "directory_membership_missing"
        alert = "directory_membership_missing"
    elif dispatcher_membership.job_title not in _DEPARTMENT_HEAD_JOBTITLE_IDS:
        reason = "not_department_head"
    elif department_type == "prison_area" and not (
        department_id and department_id.strip() and department_id == target_department_id
    ):
        reason = "cross_department_dispatch_denied"
    return DispatchAuthorizationDecision(
        decision="allow" if reason is None else "deny",
        reason_code=reason,
        department_id=department_id,
        dispatcher_department_type=department_type,
        matched_rule=matched_rule,
        alert_code=alert,
    )


class AuthorizedWorkObjectScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_ai_user_id: str
    principal_department_id: str | None


def compute_visibility_scope(
    *, principal_ai_user_id: str, principal_department_id: str | None,
) -> AuthorizedWorkObjectScope:
    """No subtree inheritance or authority derived from dispatch history.

    Current stores can apply only the assignee identity. Department ownership and
    initiator predicates require the later DISPATCH-001 record contract.
    """
    return AuthorizedWorkObjectScope(
        principal_ai_user_id=principal_ai_user_id,
        principal_department_id=principal_department_id,
    )
