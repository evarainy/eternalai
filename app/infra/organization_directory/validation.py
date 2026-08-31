"""Fail-closed validation shared by organization directory adapters."""

from __future__ import annotations

from collections.abc import Sequence

from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
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


__all__ = ("validate_department_graph",)
