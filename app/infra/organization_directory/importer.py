"""Normalize credential-free OA directory responses into a local snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.infra.organization_directory.validation import validate_department_graph
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)

_CREDENTIAL_FIELDS = frozenset({"sessionkey", "datakey"})


def build_directory_snapshot(
    *,
    department_rows: Sequence[Mapping[str, Any]],
    user_rows: Sequence[Mapping[str, Any]],
    authoritative_user_count: int,
    fetched_at: datetime,
) -> OrganizationDirectorySnapshot:
    """Build a safe snapshot from already-fetched OA rows.

    Query credentials belong to the transport adapter and are intentionally not
    accepted by this boundary.
    """
    if not isinstance(authoritative_user_count, int) or authoritative_user_count < 0:
        raise OrganizationDirectoryError("invalid authoritative directory count")

    departments = tuple(_department(row) for row in department_rows)
    validate_department_graph(departments)
    memberships = tuple(_membership(_raw_fields(row)) for row in user_rows)
    returned_count = len(user_rows)
    return OrganizationDirectorySnapshot(
        departments=departments,
        memberships=memberships,
        authoritative_user_count=authoritative_user_count,
        returned_user_count=returned_count,
        is_complete=returned_count == authoritative_user_count,
        fetched_at=fetched_at,
    )


def _raw_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in row.items():
        normalized_key = str(key)
        folded = normalized_key.casefold()
        if folded.endswith("span") or folded in _CREDENTIAL_FIELDS:
            continue
        safe[normalized_key] = value
    return safe


def _department(row: Mapping[str, Any]) -> OrganizationDepartment:
    return OrganizationDepartment(
        department_id=_required_text(row, "id"),
        parent_department_id=_optional_text(row, "pid"),
        display_name=_required_text(row, "name"),
        organization_id=_optional_text(row, "psubcompanyid"),
    )


def _membership(row: Mapping[str, Any]) -> OrganizationUserMembership:
    return OrganizationUserMembership(
        user_id=_required_text(row, "id"),
        department_id=_required_text(row, "departmentid"),
        organization_id=_optional_text(row, "orgid"),
        subcompany_id=_optional_text(row, "subcompanyid1"),
    )


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OrganizationDirectoryError("invalid organization directory row")
    return value


def _optional_text(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise OrganizationDirectoryError("invalid organization directory row")
    return value


__all__ = ("build_directory_snapshot",)
