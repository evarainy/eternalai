"""PostgreSQL-backed read-only organization mirror."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.organization_directory.validation import validate_department_graph
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectoryPort,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)

_DEPARTMENT_ADAPTER = TypeAdapter(OrganizationDepartment)
_MEMBERSHIP_ADAPTER = TypeAdapter(OrganizationUserMembership)


class PostgreSQLOrganizationDirectory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def replace_snapshot(self, snapshot: OrganizationDirectorySnapshot) -> None:
        if not snapshot.is_complete or (
            snapshot.returned_user_count != snapshot.authoritative_user_count
            or snapshot.returned_user_count != len(snapshot.memberships)
        ):
            raise OrganizationDirectoryError("incomplete organization snapshot")
        validate_department_graph(snapshot.departments)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(text("DELETE FROM organization_user_memberships"))
                    await session.execute(text("DELETE FROM organization_departments"))
                    for department in snapshot.departments:
                        await session.execute(
                            text(
                                "INSERT INTO organization_departments "
                                "(department_id, parent_department_id, display_name, "
                                "organization_id, fetched_at) VALUES "
                                "(:department_id, :parent_department_id, :display_name, "
                                ":organization_id, :fetched_at)"
                            ),
                            {**department.model_dump(), "fetched_at": snapshot.fetched_at},
                        )
                    for membership in snapshot.memberships:
                        await session.execute(
                            text(
                                "INSERT INTO organization_user_memberships "
                                "(user_id, department_id, organization_id, subcompany_id, "
                                "fetched_at) VALUES (:user_id, :department_id, "
                                ":organization_id, :subcompany_id, :fetched_at)"
                            ),
                            {**membership.model_dump(), "fetched_at": snapshot.fetched_at},
                        )
        except OrganizationDirectoryError:
            raise
        except Exception as exc:
            raise OrganizationDirectoryError("organization snapshot replacement failed") from exc

    async def get_department(
        self, department_id: str
    ) -> OrganizationDepartment | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT department_id, parent_department_id, display_name, "
                        "organization_id FROM organization_departments "
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
                        " organization_id, ARRAY[department_id]::text[] AS path, false AS cycle"
                        " FROM organization_departments WHERE department_id = :department_id"
                        " UNION ALL"
                        " SELECT child.department_id, child.parent_department_id, "
                        " child.display_name, child.organization_id, "
                        " subtree.path || child.department_id, "
                        " child.department_id = ANY(subtree.path)"
                        " FROM organization_departments child JOIN subtree"
                        " ON child.parent_department_id = subtree.department_id"
                        " WHERE NOT subtree.cycle"
                        ") SELECT department_id, parent_department_id, display_name, "
                        "organization_id, cycle FROM subtree"
                    ),
                    {"department_id": department_id},
                )
            ).mappings().all()
        if any(row["cycle"] for row in rows):
            raise OrganizationDirectoryError("organization department cycle detected")
        return [
            _DEPARTMENT_ADAPTER.validate_python(
                {key: row[key] for key in (
                    "department_id", "parent_department_id", "display_name", "organization_id"
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
                        "SELECT user_id, department_id, organization_id, subcompany_id "
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
