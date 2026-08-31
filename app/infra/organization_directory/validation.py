"""Fail-closed validation shared by organization directory adapters."""

from __future__ import annotations

from collections.abc import Sequence

from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectorySnapshot,
)


def validate_department_graph(
    departments: Sequence[OrganizationDepartment],
) -> None:
    parents: dict[str, str | None] = {}
    for department in departments:
        if department.department_id in parents:
            raise OrganizationDirectoryError("duplicate department id")
        parents[department.department_id] = department.parent_department_id

    for start in parents:
        seen: set[str] = set()
        current: str | None = start
        while current is not None and current in parents:
            if current in seen:
                raise OrganizationDirectoryError("organization department cycle detected")
            seen.add(current)
            current = parents[current]


def has_complete_snapshot_evidence(snapshot: OrganizationDirectorySnapshot) -> bool:
    """Recompute completeness from pagination evidence, not the declared flag."""
    if (
        snapshot.count_error_code is not None
        or snapshot.authoritative_user_count_after is None
        or snapshot.authoritative_user_count_before
        != snapshot.authoritative_user_count_after
        or not snapshot.user_pages
    ):
        return False

    expected_page = 1
    reached_end = False
    for index, page in enumerate(snapshot.user_pages):
        if page.current_page != expected_page or page.error_code is not None:
            return False
        is_last_received_page = index == len(snapshot.user_pages) - 1
        if page.is_end:
            if page.next_page is not None or not is_last_received_page:
                return False
            reached_end = True
        else:
            if page.next_page != page.current_page + 1:
                return False
            expected_page = page.next_page

    return (
        reached_end
        and snapshot.returned_user_count
        == snapshot.authoritative_user_count_before
    )


__all__ = ("has_complete_snapshot_evidence", "validate_department_graph")
