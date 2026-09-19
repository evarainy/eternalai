"""Exact lifecycle permissions, independent of HTTP and persistence."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.ports.work_object_lifecycle import LifecycleActor, compute_lifecycle_actions


@pytest.mark.parametrize("target", ["user", "department"])
@pytest.mark.parametrize("status", ["assigned", "department_pending", "in_progress", "completed"])
@pytest.mark.parametrize(
    "role", ["recipient", "coworker", "initiator", "foreign_department", "foreign_tenant"]
)
def test_exact_state_and_role_matrix(target, status, role):
    record = SimpleNamespace(
        tenant_id="default",
        owner_department_id="office-a",
        target_kind=target,
        assignee_directory_user_id="recipient",
        accepted_by_ai_user_id="ai-recipient" if status in {"in_progress", "completed"} else None,
        status=status,
    )
    actor = LifecycleActor("default", "ai-" + role, role, "office-a", 1, 100.0)
    if role == "foreign_department":
        actor = replace(actor, department_id="office-b")
    if role == "foreign_tenant":
        actor = replace(actor, tenant_id="other")
    expected = []
    if role not in {"foreign_department", "foreign_tenant"}:
        if status in {"assigned", "department_pending"} and (
            target == "department" or role == "recipient"
        ):
            expected = ["accept"]
        if status == "in_progress" and role == "recipient":
            expected = ["feedback", "complete"]
    assert compute_lifecycle_actions(record, actor) == expected
    assert compute_lifecycle_actions(record, None) == []
