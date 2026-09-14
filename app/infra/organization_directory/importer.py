"""Normalize credential-free OA directory responses into a local snapshot."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from app.infra.organization_directory.validation import (
    InvalidDirectorySnapshot,
    has_complete_snapshot_evidence,
    validate_department_graph,
    validate_display_name,
    validate_memberships,
)
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryPage,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)

_CREDENTIAL_FIELDS = frozenset({"sessionkey", "datakey"})


def build_directory_snapshot(
    *,
    departments: Sequence[OrganizationDepartment],
    user_pages: Sequence[OrganizationDirectoryPage],
    authoritative_user_count_before: int,
    authoritative_user_count_after: int | None,
    fetched_at: datetime,
    count_error_code: Literal["authoritative_count_after_failed"] | None = None,
) -> OrganizationDirectorySnapshot:
    """Aggregate normalized pages and derive completeness from fetch evidence.

    Query credentials belong to the transport adapter and are intentionally not
    accepted by this boundary.
    """
    _validate_count(authoritative_user_count_before)
    if authoritative_user_count_after is not None:
        _validate_count(authoritative_user_count_after)

    normalized_departments = tuple(departments)
    validate_department_graph(normalized_departments)
    snapshot = OrganizationDirectorySnapshot(
        departments=normalized_departments,
        user_pages=tuple(user_pages),
        authoritative_user_count_before=authoritative_user_count_before,
        authoritative_user_count_after=authoritative_user_count_after,
        count_error_code=count_error_code,
        is_complete=False,
        fetched_at=fetched_at,
    )
    validate_memberships(snapshot.memberships)
    return snapshot.model_copy(
        update={"is_complete": has_complete_snapshot_evidence(snapshot)}
    )


def build_directory_departments(
    department_rows: Sequence[Mapping[str, Any]],
) -> tuple[OrganizationDepartment, ...]:
    departments = tuple(_department(row) for row in department_rows)
    validate_department_graph(departments)
    return departments


def build_directory_page(
    *,
    current_page: int,
    next_page: int | None,
    is_end: bool,
    user_rows: Sequence[Mapping[str, Any]],
) -> OrganizationDirectoryPage:
    return OrganizationDirectoryPage(
        current_page=current_page,
        next_page=next_page,
        is_end=is_end,
        memberships=tuple(_membership(_raw_fields(row)) for row in user_rows),
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
        subcompany_id=_optional_text(row, "psubcompanyid"),
    )


def _membership(row: Mapping[str, Any]) -> OrganizationUserMembership:
    return OrganizationUserMembership(
        user_id=_required_text(row, "id"),
        department_id=_required_text(row, "departmentid"),
        organization_id=_optional_text(row, "orgid"),
        subcompany_id=_optional_text(row, "subcompanyid1"),
        job_title=_optional_job_title(row),
        display_name=_optional_display_name(row),
    )


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidDirectorySnapshot("invalid organization directory row")
    return value


def _optional_job_title(row: Mapping[str, Any]) -> str | None:
    value = row.get("jobtitle")
    if value is None:
        return None
    if type(value) is int and value >= 0:
        return str(value) if value else None
    if isinstance(value, str):
        if value in ("", "0"):
            return None
        if re.fullmatch(r"[1-9][0-9]*", value):
            return value
    raise InvalidDirectorySnapshot("invalid organization directory row")


def _optional_display_name(row: Mapping[str, Any]) -> str | None:
    value = row.get("lastname")
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidDirectorySnapshot("invalid organization directory name")
    # Reject controls even when stripping would erase them.
    import unicodedata

    if any(unicodedata.category(char).startswith("C") for char in value):
        raise InvalidDirectorySnapshot("invalid organization directory name")
    normalized = value.strip() or None
    validate_display_name(normalized)
    return normalized


def _optional_text(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise InvalidDirectorySnapshot("invalid organization directory row")
    return value


def _validate_count(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidDirectorySnapshot("invalid authoritative directory count")


__all__ = (
    "build_directory_departments",
    "build_directory_page",
    "build_directory_snapshot",
)
