"""Stateless reconciliation of the full directory and explicit dispatch policy."""

from pydantic import BaseModel, ConfigDict, Field

from app.organization_directory_access import (
    DEFAULT_MAX_AGE_S,
    DirectoryAccessError,
    read_fresh_directory_view,
)
from app.ports import work_object_scope
from app.ports.organization_directory import OrganizationDirectoryPort


class DispatchDepartmentPolicyInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unregistered_ids: frozenset[str] = Field(repr=False)
    missing_allowed_ids: frozenset[str] = Field(repr=False)
    missing_prison_ids: frozenset[str] = Field(repr=False)
    conflict_ids: frozenset[str] = Field(repr=False)

    @property
    def is_clean(self) -> bool:
        return not (
            self.unregistered_ids or self.missing_allowed_ids
            or self.missing_prison_ids or self.conflict_ids
        )


def inspect_dispatch_department_policy(
    *, department_ids: frozenset[str],
) -> DispatchDepartmentPolicyInspection:
    allowed = work_object_scope._CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS
    prison = work_object_scope._PRISON_AREA_DEPARTMENT_IDS
    return DispatchDepartmentPolicyInspection(
        unregistered_ids=department_ids - (allowed | prison),
        missing_allowed_ids=allowed - department_ids,
        missing_prison_ids=prison - department_ids,
        conflict_ids=allowed & prison,
    )


async def check_dispatch_department_policy(
    directory: OrganizationDirectoryPort, *, max_age_s: int = DEFAULT_MAX_AGE_S,
) -> bool:
    try:
        fresh = await read_fresh_directory_view(directory, max_age_s=max_age_s)
        inspection = inspect_dispatch_department_policy(
            department_ids=frozenset(fresh.departments),
        )
        fresh.recheck()
        return inspection.is_clean
    except DirectoryAccessError:
        return False
