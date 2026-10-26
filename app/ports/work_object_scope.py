"""Pure Work Object authorization over freshly resolved directory facts.

Callers must resolve the current principal's stable join key on every decision.
Dispatch endpoints and Trace emission are owned by DISPATCH-001. No directory I/O
or credentials belong here; a missing membership produces an explicit alert signal.
Unit and supervising leaders come from the caller-supplied per-tenant roster.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ports.organization_directory import OrganizationDepartment, OrganizationUserMembership

_DEPARTMENT_HEAD_JOBTITLE_IDS: frozenset[str] = frozenset({"75", "380", "1405", "1701", "1999"})
_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS: frozenset[str] = frozenset()
_PRISON_AREA_DEPARTMENT_IDS: frozenset[str] = frozenset(
    {
        "572",
        "575",
        "580",
        "585",
        "588",
        "589",
        "590",
        "591",
        "592",
        "593",
        "594",
        "595",
        "596",
        "597",
        "598",
        "599",
        "600",
        "601",
        "602",
        "603",
        "604",
        "605",
        "606",
        "607",
        "608",
        "619",
        "622",
        "1419",
        "1420",
        "1923",
    }
)


class DispatchAuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["allow", "deny"]
    reason_code: (
        Literal[
            "directory_membership_missing",
            "not_department_head",
            "cross_department_dispatch_denied",
        ]
        | None
    )
    department_id: str | None
    dispatcher_department_type: Literal["prison_area", "office", "unknown"]
    matched_rule: Literal[
        "department_unresolved",
        "prison_area_id",
        "cross_department_allowlist",
        "department_not_allowlisted",
    ]
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
    leader_directory_user_ids: frozenset[str],
) -> DispatchAuthorizationDecision:
    """Use lookup results, never token-baked department/job title or caller labels.

    This compatibility entry handles department targets. The complete user/department
    matrix is may_dispatch_to; historical department allowlists do not grant authority.
    """
    department_id = dispatcher_membership.department_id if dispatcher_membership else None
    resolved = (
        bool(department_id and department_id.strip())
        and dispatcher_department is not None
        and dispatcher_department.department_id == department_id
    )
    reason: (
        Literal[
            "directory_membership_missing",
            "not_department_head",
            "cross_department_dispatch_denied",
        ]
        | None
    ) = None
    if dispatcher_membership is None or not resolved:
        reason = "directory_membership_missing"
    elif not is_department_head(dispatcher_membership) and not is_leader(
        dispatcher_membership, leader_directory_user_ids
    ):
        reason = "not_department_head"
    return DispatchAuthorizationDecision(
        decision="allow" if reason is None else "deny",
        reason_code=reason,
        department_id=department_id,
        dispatcher_department_type="office" if resolved else "unknown",
        matched_rule="department_not_allowlisted" if resolved else "department_unresolved",
        alert_code="directory_membership_missing"
        if reason == "directory_membership_missing"
        else None,
    )


def is_department_head(membership: OrganizationUserMembership) -> bool:
    return membership.job_title in _DEPARTMENT_HEAD_JOBTITLE_IDS


def is_leader(
    membership: OrganizationUserMembership, leader_directory_user_ids: frozenset[str]
) -> bool:
    return membership.user_id in leader_directory_user_ids


def may_dispatch_to(
    membership: OrganizationUserMembership,
    *,
    leader_directory_user_ids: frozenset[str],
    target_kind: str,
    target_department_id: str,
    target_directory_user_id: str | None = None,
) -> bool:
    if is_leader(membership, leader_directory_user_ids):
        return True
    if target_kind == "user" and target_directory_user_id == membership.user_id:
        return True
    return is_department_head(membership) and (
        target_kind == "department" or target_department_id == membership.department_id
    )


class AuthorizedWorkObjectScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_tenant_id: str
    principal_ai_user_id: str
    principal_department_id: str | None
    principal_directory_user_id: str | None = None
    responsible_department_ids: frozenset[str] = frozenset()
    directory_snapshot_version: int | None = None
    valid_until: float | None = None


def compute_visibility_scope(
    *,
    principal_tenant_id: str,
    principal_ai_user_id: str,
    principal_department_id: str | None,
    principal_directory_user_id: str | None = None,
    responsible_department_ids: frozenset[str] = frozenset(),
    directory_snapshot_version: int | None = None,
    valid_until: float | None = None,
) -> AuthorizedWorkObjectScope:
    """No subtree inheritance or authority derived from dispatch history.

    Tenant, department ownership and initiator predicates are consumed by stores.
    """
    return AuthorizedWorkObjectScope(
        principal_tenant_id=principal_tenant_id,
        principal_ai_user_id=principal_ai_user_id,
        principal_department_id=principal_department_id,
        principal_directory_user_id=principal_directory_user_id,
        responsible_department_ids=responsible_department_ids,
        directory_snapshot_version=directory_snapshot_version,
        valid_until=valid_until,
    )
