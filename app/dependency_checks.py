"""Read-only dependency checks shared by the API health route and the worker self-check.

Both processes build the same database, Redis and organization-directory checks from
their own settings and session factory. Nothing here opens a directory Source or
writes any table; the directory service is only used for its local diagnostic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial

from app.config import RedisConnectionURL
from app.db.health import check_database_health
from app.infra.health import RedisHealthCheck
from app.organization_directory_policy import check_dispatch_department_policy
from app.organization_directory_sync import OrganizationDirectorySyncService
from app.ports.organization_directory import OrganizationDirectoryPort

ReadonlyCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class ReadonlyChecks:
    health: Mapping[str, ReadonlyCheck]
    diagnostics: Mapping[str, ReadonlyCheck]


def build_readonly_checks(
    *,
    database_url: str,
    redis_url: RedisConnectionURL,
    timeout_seconds: float,
    directory_max_age_s: int,
    organization_directory: OrganizationDirectoryPort,
    directory_sync: OrganizationDirectorySyncService,
) -> ReadonlyChecks:
    return ReadonlyChecks(
        health={
            "database": partial(
                check_database_health, database_url, timeout_seconds=timeout_seconds
            ),
            "redis": RedisHealthCheck(redis_url=redis_url, timeout_seconds=timeout_seconds),
        },
        diagnostics={
            "organization_directory": directory_sync.diagnostic,
            "organization_directory_dispatch_policy": partial(
                check_dispatch_department_policy,
                organization_directory,
                max_age_s=directory_max_age_s,
            ),
        },
    )


__all__ = ("ReadonlyCheck", "ReadonlyChecks", "build_readonly_checks")
