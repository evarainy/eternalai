"""PostgreSQL-backed read-only organization mirror."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.organization_directory.sync_state import (
    PostgreSQLOrganizationDirectorySync,
    validate_snapshot,
)
from app.infra.organization_directory.validation import (
    has_complete_snapshot_evidence,
    validate_department_graph,
)
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectoryPort,
    OrganizationDirectoryReadError,
    OrganizationDirectoryReadView,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)
from app.ports.organization_directory_sync import DirectorySourceError

_DEPARTMENT_ADAPTER = TypeAdapter(OrganizationDepartment)
_MEMBERSHIP_ADAPTER = TypeAdapter(OrganizationUserMembership)


class PostgreSQLOrganizationDirectory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def replace_snapshot(self, snapshot: OrganizationDirectorySnapshot) -> None:
        if not snapshot.is_complete or not has_complete_snapshot_evidence(snapshot):
            raise OrganizationDirectoryError("incomplete organization snapshot")
        validate_department_graph(snapshot.departments)
        try:
            validate_snapshot(snapshot)
        except DirectorySourceError:
            raise OrganizationDirectoryError("invalid organization snapshot") from None
        sync = PostgreSQLOrganizationDirectorySync(self._session_factory)
        try:
            async with sync.try_acquire() as lease:
                if lease is None:
                    raise OrganizationDirectoryError("organization directory lock busy")
                await lease.start_attempt()
                try:
                    await lease.replace_snapshot(snapshot)
                except DirectorySourceError as exc:
                    if exc.code != "storage_unavailable":
                        await lease.mark_failed(exc.code)
                    raise
        except Exception:
            raise OrganizationDirectoryError("organization snapshot replacement failed") from None

    async def read_view(self) -> OrganizationDirectoryReadView:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    )
                    state = (await session.execute(text(
                        "SELECT snapshot_version,source_fetched_at,last_success_at,"
                        "clock_timestamp() AS observed_at FROM organization_directory_sync_state "
                        "WHERE singleton_id=1"
                    ))).mappings().one()
                    if state["snapshot_version"] == 0:
                        raise OrganizationDirectoryReadError("organization_directory_missing")
                    departments = (await session.execute(text(
                        "SELECT department_id,parent_department_id,display_name,subcompany_id "
                        "FROM organization_departments ORDER BY department_id"
                    ))).mappings().all()
                    members = (
                        (
                            await session.execute(
                                text(
                                    "SELECT user_id,department_id,organization_id,"
                                    "subcompany_id,job_title,"
                                    "display_name FROM organization_user_memberships "
                                    "ORDER BY user_id,department_id"
                                )
                            )
                        )
                        .mappings()
                        .all()
                    )
                    return OrganizationDirectoryReadView(
                        **dict(state),
                        departments=tuple(
                            _DEPARTMENT_ADAPTER.validate_python(dict(row)) for row in departments
                        ),
                        memberships=tuple(
                            _MEMBERSHIP_ADAPTER.validate_python(dict(row)) for row in members
                        ),
                    )
        except OrganizationDirectoryReadError:
            raise
        except Exception:
            raise OrganizationDirectoryReadError("organization_directory_unavailable") from None

    async def get_department(
        self, department_id: str
    ) -> OrganizationDepartment | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT department_id, parent_department_id, display_name, "
                        "subcompany_id FROM organization_departments "
                        "WHERE department_id = :department_id"
                    ),
                    {"department_id": department_id},
                )
            ).mappings().one_or_none()
        return None if row is None else _DEPARTMENT_ADAPTER.validate_python(dict(row))

    async def list_department_subtree(
        self, department_id: str
    ) -> list[OrganizationDepartment]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        " SELECT department_id, parent_department_id, display_name, "
                        " subcompany_id, ARRAY[department_id]::text[] AS path, false AS cycle"
                        " FROM organization_departments WHERE department_id = :department_id"
                        " UNION ALL"
                        " SELECT child.department_id, child.parent_department_id, "
                        " child.display_name, child.subcompany_id, "
                        " subtree.path || child.department_id, "
                        " child.department_id = ANY(subtree.path)"
                        " FROM organization_departments child JOIN subtree"
                        " ON child.parent_department_id = subtree.department_id"
                        " WHERE NOT subtree.cycle"
                        ") SELECT department_id, parent_department_id, display_name, "
                        "subcompany_id, cycle FROM subtree"
                    ),
                    {"department_id": department_id},
                )
            ).mappings().all()
        if any(row["cycle"] for row in rows):
            raise OrganizationDirectoryError("organization department cycle detected")
        return [
            _DEPARTMENT_ADAPTER.validate_python(
                {key: row[key] for key in (
                    "department_id", "parent_department_id", "display_name", "subcompany_id"
                )}
            )
            for row in rows
        ]

    async def list_user_memberships(
        self, user_id: str
    ) -> list[OrganizationUserMembership]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT user_id, department_id, organization_id, subcompany_id, "
                        "job_title, display_name "
                        "FROM organization_user_memberships WHERE user_id = :user_id "
                        "ORDER BY department_id"
                    ),
                    {"user_id": user_id},
                )
            ).mappings().all()
        return [_MEMBERSHIP_ADAPTER.validate_python(dict(row)) for row in rows]


if TYPE_CHECKING:
    def _protocol_check(store: PostgreSQLOrganizationDirectory) -> OrganizationDirectoryPort:
        return store


__all__ = ("PostgreSQLOrganizationDirectory",)
