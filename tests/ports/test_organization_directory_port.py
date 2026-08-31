from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryPage,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)


def test_directory_models_expose_only_structural_fields() -> None:
    department = OrganizationDepartment(
        department_id="dept-a",
        parent_department_id=None,
        display_name="Synthetic department",
        subcompany_id="sub-a",
    )
    membership = OrganizationUserMembership(
        user_id="user-a",
        department_id="dept-a",
        organization_id="org-a",
        subcompany_id="sub-a",
    )
    snapshot = OrganizationDirectorySnapshot(
        departments=(department,),
        user_pages=(
            OrganizationDirectoryPage(
                current_page=1,
                next_page=None,
                is_end=True,
                memberships=(membership,),
            ),
        ),
        authoritative_user_count_before=1,
        authoritative_user_count_after=1,
        is_complete=True,
        fetched_at=datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert snapshot.departments == (department,)
    assert snapshot.memberships == (membership,)
    assert snapshot.returned_user_count == 1
    assert "organization_id" not in OrganizationDepartment.model_fields
    assert membership.organization_id == "org-a"
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
