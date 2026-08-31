from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)


def test_directory_models_expose_only_structural_fields() -> None:
    department = OrganizationDepartment(
        department_id="dept-a",
        parent_department_id=None,
        display_name="Synthetic department",
        organization_id="org-a",
    )
    membership = OrganizationUserMembership(
        user_id="user-a",
        department_id="dept-a",
        organization_id="org-a",
        subcompany_id="sub-a",
    )
    snapshot = OrganizationDirectorySnapshot(
        departments=(department,),
        memberships=(membership,),
        authoritative_user_count=1,
        returned_user_count=1,
        is_complete=True,
        fetched_at=datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert snapshot.departments == (department,)
    assert snapshot.memberships == (membership,)
    assert "manager" not in repr(snapshot).casefold()
    assert "authoriz" not in repr(snapshot).casefold()


def test_directory_models_reject_rendered_span_fields() -> None:
    with pytest.raises(ValidationError):
        OrganizationUserMembership.model_validate(
            {
                "user_id": "user-a",
                "department_id": "dept-a",
                "organization_id": "org-a",
                "subcompany_id": "sub-a",
                "departmentidspan": "<span>synthetic</span>",
            }
        )
