"""Request-local directory facts with conservative database-clock freshness."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryPort,
    OrganizationDirectoryReadError,
    OrganizationDirectoryReadView,
    OrganizationUserMembership,
)

DEFAULT_MAX_AGE_S = 172800


class DirectoryAccessError(RuntimeError):
    def __init__(
        self,
        code: Literal[
            "organization_directory_missing",
            "organization_directory_stale",
            "organization_directory_unavailable",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


def check_freshness(
    version: int,
    source: datetime | None,
    success: datetime | None,
    now: datetime,
    max_age_s: int,
) -> None:
    values = (now,) + tuple(value for value in (source, success) if value is not None)
    if any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in values):
        raise DirectoryAccessError("organization_directory_unavailable")
    if type(version) is not int or version < 0:
        raise DirectoryAccessError("organization_directory_unavailable")
    if version == 0:
        if source is not None or success is not None:
            raise DirectoryAccessError("organization_directory_unavailable")
        raise DirectoryAccessError("organization_directory_missing")
    if source is None or success is None or source > success or success > now:
        raise DirectoryAccessError("organization_directory_unavailable")
    if max((now - source).total_seconds(), (now - success).total_seconds()) > max_age_s:
        raise DirectoryAccessError("organization_directory_stale")


@dataclass(frozen=True)
class FreshDirectoryView:
    view: OrganizationDirectoryReadView = field(repr=False)
    started_at: float
    monotonic: Callable[[], float] = field(repr=False)
    max_age_s: int
    departments: dict[str, OrganizationDepartment] = field(repr=False)
    memberships: dict[str, list[OrganizationUserMembership]] = field(repr=False)

    def recheck(self) -> None:
        elapsed = self.monotonic() - self.started_at
        if elapsed < 0:
            raise DirectoryAccessError("organization_directory_unavailable")
        check_freshness(
            self.view.snapshot_version,
            self.view.source_fetched_at,
            self.view.last_success_at,
            self.view.observed_at + timedelta(seconds=elapsed),
            self.max_age_s,
        )


async def read_fresh_directory_view(
    directory: OrganizationDirectoryPort,
    *,
    max_age_s: int = DEFAULT_MAX_AGE_S,
    monotonic: Callable[[], float] = time.monotonic,
) -> FreshDirectoryView:
    started = monotonic()
    try:
        view = await directory.read_view()
        view = OrganizationDirectoryReadView.model_validate(view.model_dump())
        check_freshness(
            view.snapshot_version,
            view.source_fetched_at,
            view.last_success_at,
            view.observed_at,
            max_age_s,
        )
        departments = {item.department_id: item for item in view.departments}
        memberships: dict[str, list[OrganizationUserMembership]] = defaultdict(list)
        keys: set[tuple[str, str]] = set()
        if len(departments) != len(view.departments):
            raise ValueError
        for member in view.memberships:
            key = (member.user_id, member.department_id)
            if key in keys:
                raise ValueError
            keys.add(key)
            memberships[member.user_id].append(member)
        result = FreshDirectoryView(view, started, monotonic, max_age_s, departments, memberships)
        result.recheck()
        return result
    except OrganizationDirectoryReadError as exc:
        raise DirectoryAccessError(exc.code) from None
    except DirectoryAccessError:
        raise
    except Exception:
        raise DirectoryAccessError("organization_directory_unavailable") from None
