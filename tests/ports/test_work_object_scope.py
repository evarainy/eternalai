from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.ports import work_object_scope as policy
from app.ports.organization_directory import OrganizationDepartment, OrganizationUserMembership
from app.ports.work_object_scope import (
    AuthorizedWorkObjectScope,
    DispatchAuthorizationDecision,
    compute_dispatch_authorization,
    compute_visibility_scope,
)


def _decision(
    *,
    job_title: str | None = "75",
    department_id: str = "572",
    target: str = "575",
    resolved: bool = True,
) -> DispatchAuthorizationDecision:
    return compute_dispatch_authorization(
        dispatcher_membership=OrganizationUserMembership(
            user_id="synthetic-directory-user",
            department_id=department_id,
            job_title=job_title,
        ),
        dispatcher_department=(
            OrganizationDepartment(department_id=department_id, display_name="Synthetic department")
            if resolved
            else None
        ),
        target_department_id=target,
    )


@pytest.mark.parametrize("job_title", [None, "", "999999", "科长", "075"])
@pytest.mark.parametrize("department_id", ["572", "synthetic-office", "office-approved"])
def test_non_head_jobtitle_denied(monkeypatch, job_title: str | None, department_id: str) -> None:
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-approved"})
    )
    decision = _decision(job_title=job_title, department_id=department_id)
    assert decision.decision == "deny"
    assert decision.reason_code == "not_department_head"
    assert (
        _decision(job_title="75", department_id=department_id, target=department_id).decision
        == "allow"
    )


@pytest.mark.parametrize("job_title", ["75", "380", "1405", "1701", "1999"])
def test_prison_area_head_cross_department_denied(job_title: str) -> None:
    decision = _decision(job_title=job_title)
    assert decision.decision == "deny"
    assert decision.reason_code == "cross_department_dispatch_denied"
    assert decision.dispatcher_department_type == "prison_area"


@pytest.mark.parametrize("job_title", ["75", "380", "1405", "1701", "1999"])
@pytest.mark.parametrize("target", ["572", "synthetic-other-office"])
def test_allowlisted_resolved_head_can_cross_department(
    monkeypatch, job_title: str, target: str
) -> None:
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"synthetic-office"})
    )
    decision = _decision(job_title=job_title, department_id="synthetic-office", target=target)
    assert decision.decision == "allow"
    assert decision.reason_code is None
    assert decision.alert_code is None


def test_prison_area_head_same_department_allowed() -> None:
    decision = _decision(target="572")
    assert decision.decision == "allow"
    assert decision.reason_code is None


def test_dispatch_decision_carries_no_visibility_field() -> None:
    assert set(DispatchAuthorizationDecision.model_fields) == {
        "decision",
        "reason_code",
        "department_id",
        "dispatcher_department_type",
        "matched_rule",
        "alert_code",
    }


def test_visibility_scope_signature_excludes_dispatch_history() -> None:
    assert set(inspect.signature(compute_visibility_scope).parameters) == {
        "principal_tenant_id",
        "principal_ai_user_id",
        "principal_department_id",
    }


def test_visibility_scope_does_not_inherit_department_subtree() -> None:
    assert set(AuthorizedWorkObjectScope.model_fields) == {
        "principal_tenant_id",
        "principal_ai_user_id",
        "principal_department_id",
    }
    scope = compute_visibility_scope(
        principal_tenant_id="tenant-dispatch-a",
        principal_ai_user_id="synthetic-principal",
        principal_department_id="synthetic-child",
    )
    assert scope.model_dump() == {
        "principal_tenant_id": "tenant-dispatch-a",
        "principal_ai_user_id": "synthetic-principal",
        "principal_department_id": "synthetic-child",
    }


@pytest.mark.parametrize("department_id", ["", " ", "synthetic-missing-department", "572"])
def test_undeterminable_department_is_unknown(department_id: str) -> None:
    decision = _decision(department_id=department_id, resolved=False)
    assert decision.dispatcher_department_type == "unknown"
    assert decision.matched_rule == "department_unresolved"
    assert decision.decision == "deny"
    assert decision.reason_code == "cross_department_dispatch_denied"


def test_mismatched_department_lookup_does_not_grant_office_authority() -> None:
    decision = compute_dispatch_authorization(
        dispatcher_membership=OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="572",
            job_title="75",
        ),
        dispatcher_department=OrganizationDepartment(
            department_id="synthetic-office",
            display_name="Synthetic office",
        ),
        target_department_id="575",
    )
    assert decision.decision == "deny"
    assert decision.matched_rule == "department_unresolved"


@pytest.mark.parametrize("job_title", ["75", "380", "1405", "1701", "1999"])
def test_unlisted_resolved_head_is_same_department_only(job_title):
    for target, expected in (("new-unregistered", "allow"), ("572", "deny")):
        decision = _decision(department_id="new-unregistered", job_title=job_title, target=target)
        assert decision.dispatcher_department_type == "unknown"
        assert decision.matched_rule == "department_not_allowlisted"
        assert decision.decision == expected
        assert decision.reason_code == (
            None if expected == "allow" else "cross_department_dispatch_denied"
        )


def test_unknown_join_key_denies_dispatch_and_alerts() -> None:
    # None is the directory lookup result for an absent current-principal join key.
    decision = compute_dispatch_authorization(
        dispatcher_membership=None,
        dispatcher_department=None,
        target_department_id="572",
    )
    assert decision.decision == "deny"
    assert decision.reason_code == "directory_membership_missing"
    assert decision.alert_code == "directory_membership_missing"
    assert decision.dispatcher_department_type == "unknown"


@pytest.mark.parametrize("department_id,resolved,expected_type,rule", [
    ("572", True, "prison_area", "prison_area_id"),
    ("synthetic-office", True, "unknown", "department_not_allowlisted"),
    ("office-approved", True, "office", "cross_department_allowlist"),
    ("office-approved", False, "unknown", "department_unresolved"),
])
def test_trace_attributes_exclude_join_key_and_job_title(
    monkeypatch, department_id, resolved, expected_type, rule
) -> None:
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-approved"})
    )
    decision = _decision(department_id=department_id, resolved=resolved)
    assert decision.trace_attributes == {
        "department_id": department_id,
        "department_type": expected_type,
        "matched_rule": rule,
    }
    assert "synthetic-directory-user" not in repr(decision)
    assert "75" not in repr(decision.trace_attributes)


def test_undeterminable_department_allows_only_same_department() -> None:
    same = _decision(
        department_id="synthetic-unresolved", target="synthetic-unresolved", resolved=False
    )
    other = _decision(
        department_id="synthetic-unresolved", target="synthetic-other", resolved=False
    )
    assert same.decision == "allow" and same.reason_code is None
    assert other.decision == "deny" and other.reason_code == "cross_department_dispatch_denied"
    for decision in (same, other):
        assert decision.dispatcher_department_type == "unknown"
        assert decision.matched_rule == "department_unresolved"


def test_decision_reason_and_rule_are_closed() -> None:
    valid = _decision().model_dump()
    for field in ("reason_code", "matched_rule"):
        with pytest.raises(ValidationError) as error:
            DispatchAuthorizationDecision.model_validate({**valid, field: "synthetic-unknown"})
        assert error.value.errors()[0]["loc"] == (field,)
        assert error.value.errors()[0]["type"] == "literal_error"
    for reason in (
        None,
        "directory_membership_missing",
        "not_department_head",
        "cross_department_dispatch_denied",
    ):
        for rule in (
            "department_unresolved",
            "prison_area_id",
            "department_not_allowlisted",
            "cross_department_allowlist",
        ):
            decision = DispatchAuthorizationDecision.model_validate(
                {**valid, "reason_code": reason, "matched_rule": rule}
            )
            assert decision.reason_code == reason and decision.matched_rule == rule


@pytest.mark.parametrize("department", ["new-unregistered", "synthetic-office", "another-office"])
def test_empty_allowlist_never_grants_cross_department(department):
    assert policy._CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS == frozenset()
    assert _decision(department_id=department, target="572").decision == "deny"
    assert _decision(department_id=department, target=department).decision == "allow"


@pytest.mark.parametrize(
    "department,resolved", [("572", True), ("office-approved", False), ("", True), (" ", True)]
)
def test_allowlist_conflict_and_unresolved_department_cannot_cross(
    monkeypatch, department, resolved
):
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({department}))
    result = _decision(department_id=department, resolved=resolved, target="external")
    assert result.decision == "deny"
    assert result.matched_rule == (
        "prison_area_id" if department == "572" else "department_unresolved"
    )
    same = _decision(department_id=department, resolved=resolved, target=department)
    assert same.decision == ("allow" if department.strip() else "deny")


def test_allowlist_revocation_and_trace_classification(monkeypatch):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-approved"})
    )
    allowed = _decision(department_id="office-approved")
    assert allowed.decision == "allow"
    assert allowed.trace_attributes == {
        "department_id": "office-approved",
        "department_type": "office",
        "matched_rule": "cross_department_allowlist",
    }
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset())
    assert _decision(department_id="office-approved").decision == "deny"
