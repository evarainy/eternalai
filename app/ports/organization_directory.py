"""Read-only organization directory contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class OrganizationDirectoryError(RuntimeError):
    """Fail-closed directory snapshot or query failure."""


class OrganizationDepartment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    department_id: str
    parent_department_id: str | None = None
    display_name: str
    organization_id: str | None = None


class OrganizationUserMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    department_id: str
    organization_id: str | None = None
    subcompany_id: str | None = None


class OrganizationDirectorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    departments: tuple[OrganizationDepartment, ...]
    memberships: tuple[OrganizationUserMembership, ...]
    authoritative_user_count: int
    returned_user_count: int
    is_complete: bool
    fetched_at: datetime


class OrganizationDirectoryPort(Protocol):
    async def replace_snapshot(self, snapshot: OrganizationDirectorySnapshot) -> None: ...

    async def get_department(
        self, department_id: str
    ) -> OrganizationDepartment | None: ...

    async def list_department_subtree(
        self, department_id: str
    ) -> list[OrganizationDepartment]: ...

    async def list_user_memberships(
        self, user_id: str
    ) -> list[OrganizationUserMembership]: ...


__all__ = (
    "OrganizationDepartment",
    "OrganizationDirectoryError",
    "OrganizationDirectoryPort",
    "OrganizationDirectorySnapshot",
    "OrganizationUserMembership",
)
