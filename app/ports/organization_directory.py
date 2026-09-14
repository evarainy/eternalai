"""Read-only organization directory contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrganizationDirectoryError(RuntimeError):
    """Fail-closed directory snapshot or query failure."""


class OrganizationDirectoryReadError(OrganizationDirectoryError):
    def __init__(
        self,
        code: Literal["organization_directory_missing", "organization_directory_unavailable"],
    ) -> None:
        self.code = (
            code if code in {"organization_directory_missing", "organization_directory_unavailable"}
            else "organization_directory_unavailable"
        )
        super().__init__(self.code)


class OrganizationDepartment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    department_id: str
    parent_department_id: str | None = None
    display_name: str
    # OA source: psubcompanyid. This is not the membership orgid field.
    subcompany_id: str | None = None


class OrganizationUserMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    department_id: str
    organization_id: str | None = None
    subcompany_id: str | None = None
    job_title: str | None = None
    display_name: str | None = Field(default=None, repr=False)


class OrganizationDirectoryReadView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_version: int = Field(ge=1, strict=True)
    source_fetched_at: datetime
    last_success_at: datetime
    observed_at: datetime
    departments: tuple[OrganizationDepartment, ...] = Field(repr=False)
    memberships: tuple[OrganizationUserMembership, ...] = Field(repr=False)

    @field_validator("source_fetched_at", "last_success_at", "observed_at")
    @classmethod
    def _utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("directory timestamps must be timezone aware")
        return value.astimezone(UTC)


class OrganizationDirectoryPage(BaseModel):
    """One normalized OA user page plus its pagination outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_page: int = Field(ge=1)
    next_page: int | None = Field(default=None, ge=1)
    is_end: bool
    memberships: tuple[OrganizationUserMembership, ...]
    error_code: Literal["page_fetch_failed"] | None = None


class OrganizationDirectorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    departments: tuple[OrganizationDepartment, ...]
    user_pages: tuple[OrganizationDirectoryPage, ...]
    authoritative_user_count_before: int = Field(ge=0)
    authoritative_user_count_after: int | None = Field(default=None, ge=0)
    count_error_code: Literal["authoritative_count_after_failed"] | None = None
    is_complete: bool
    fetched_at: datetime

    @property
    def memberships(self) -> tuple[OrganizationUserMembership, ...]:
        return tuple(
            membership
            for page in self.user_pages
            for membership in page.memberships
        )

    @property
    def returned_user_count(self) -> int:
        return len(self.memberships)


class OrganizationDirectoryPort(Protocol):
    async def read_view(self) -> OrganizationDirectoryReadView: ...

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


class OrganizationDirectorySourcePort(Protocol):
    """Credential-free structural reads used by the pagination executor."""

    async def fetch_departments(self) -> tuple[OrganizationDepartment, ...]: ...

    async def fetch_authoritative_user_count(self) -> int: ...

    async def fetch_user_page(self, current_page: int) -> OrganizationDirectoryPage: ...


__all__ = (
    "OrganizationDepartment",
    "OrganizationDirectoryError",
    "OrganizationDirectoryPage",
    "OrganizationDirectoryPort",
    "OrganizationDirectoryReadError",
    "OrganizationDirectoryReadView",
    "OrganizationDirectorySnapshot",
    "OrganizationDirectorySourcePort",
    "OrganizationUserMembership",
)
