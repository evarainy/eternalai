from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

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
@pytest.mark.parametrize("department_id", ["572", "synthetic-office"])
def test_non_head_jobtitle_denied(job_title: str | None, department_id: str) -> None:
    decision = _decision(job_title=job_title, department_id=department_id)
    assert decision.decision == "deny"
    assert decision.reason_code == "not_department_head"


@pytest.mark.parametrize("job_title", ["75", "380", "1405", "1701", "1999"])
def test_prison_area_head_cross_department_denied(job_title: str) -> None:
    decision = _decision(job_title=job_title)
    assert decision.decision == "deny"
    assert decision.reason_code == "cross_department_dispatch_denied"
    assert decision.dispatcher_department_type == "prison_area"


@pytest.mark.parametrize("job_title", ["75", "380", "1405", "1701", "1999"])
@pytest.mark.parametrize("target", ["572", "synthetic-other-office"])
def test_office_head_any_department_allowed(job_title: str, target: str) -> None:
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
def test_undeterminable_department_defaults_to_prison_area(department_id: str) -> None:
    decision = _decision(department_id=department_id, resolved=False)
    assert decision.dispatcher_department_type == "prison_area"
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


def test_resolved_non_prison_department_is_office() -> None:
    decision = _decision(department_id="synthetic-resolved-office")
    assert decision.dispatcher_department_type == "office"
    assert decision.matched_rule == "resolved_non_prison_id"
    assert decision.decision == "allow"


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
    assert decision.dispatcher_department_type == "prison_area"


@pytest.mark.parametrize("department_id", ["572", "synthetic-office"])
def test_trace_attributes_exclude_join_key_and_job_title(department_id: str) -> None:
    decision = _decision(department_id=department_id)
    assert decision.trace_attributes == {
        "department_id": department_id,
        "department_type": "prison_area" if department_id == "572" else "office",
        "matched_rule": "prison_area_id" if department_id == "572" else "resolved_non_prison_id",
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
        assert decision.dispatcher_department_type == "prison_area"
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
        for rule in ("department_unresolved", "prison_area_id", "resolved_non_prison_id"):
            decision = DispatchAuthorizationDecision.model_validate(
                {**valid, "reason_code": reason, "matched_rule": rule}
            )
            assert decision.reason_code == reason and decision.matched_rule == rule
