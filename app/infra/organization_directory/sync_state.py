"""Session-lock serialization and atomic publication on one pinned connection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.infra.organization_directory.validation import (
    has_complete_snapshot_evidence,
    validate_department_graph,
    validate_memberships,
)
from app.ports.organization_directory import OrganizationDirectorySnapshot
from app.ports.organization_directory_sync import (
    DirectoryErrorCode,
    DirectorySourceError,
    OrganizationDirectorySyncLease,
    OrganizationDirectorySyncStatus,
)

_LOCK = "73142, 1"


async def read_status(connection: AsyncConnection) -> OrganizationDirectorySyncStatus:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT *, clock_timestamp() AS observed_at "
                    "FROM organization_directory_sync_state "
                    "WHERE singleton_id = 1"
                )
            )
        )
        .mappings()
        .one()
    )
    return OrganizationDirectorySyncStatus.model_validate(dict(row))


def validate_snapshot(snapshot: OrganizationDirectorySnapshot) -> None:
    if not snapshot.is_complete or not has_complete_snapshot_evidence(snapshot):
        raise DirectorySourceError("snapshot_incomplete")
    try:
        validate_department_graph(snapshot.departments)
        validate_memberships(snapshot.memberships)
        departments = {item.department_id for item in snapshot.departments}
        if any(item.department_id not in departments for item in snapshot.memberships):
            raise ValueError
        if snapshot.fetched_at.tzinfo is None or snapshot.fetched_at.utcoffset() is None:
            raise ValueError
    except Exception:
        raise DirectorySourceError("snapshot_invalid") from None


class PostgreSQLDirectorySyncLease:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def read_status(self) -> OrganizationDirectorySyncStatus:
        async with self._connection.begin():
            return await read_status(self._connection)

    async def start_attempt(self) -> datetime:
        async with self._connection.begin():
            value = (
                await self._connection.execute(
                    text(
                        "UPDATE organization_directory_sync_state SET "
                        "last_attempt_started_at=clock_timestamp(), last_attempt_finished_at=NULL, "
                        "last_attempt_status='running', last_error_code=NULL WHERE singleton_id=1 "
                        "RETURNING last_attempt_started_at"
                    )
                )
            ).scalar_one()
            if not isinstance(value, datetime):
                raise DirectorySourceError("storage_unavailable")
            return value

    async def mark_failed(self, code: DirectoryErrorCode) -> None:
        async with self._connection.begin():
            await self._connection.execute(
                text(
                    "UPDATE organization_directory_sync_state SET "
                    "last_attempt_status='failed', last_error_code=:code, "
                    "last_attempt_finished_at=clock_timestamp() WHERE singleton_id=1"
                ),
                {"code": code},
            )

    async def replace_snapshot(self, snapshot: OrganizationDirectorySnapshot) -> None:
        # Validation before entering publication is a confirmed pre-commit failure.
        validate_snapshot(snapshot)
        async with self._connection.begin():
            state = await read_status(self._connection)
        if snapshot.fetched_at > state.observed_at or (
            state.source_fetched_at is not None and snapshot.fetched_at <= state.source_fetched_at
        ):
            raise DirectorySourceError("snapshot_invalid")
        try:
            async with self._connection.begin():
                await self._connection.execute(text("DELETE FROM organization_user_memberships"))
                await self._connection.execute(text("DELETE FROM organization_departments"))
                for department in snapshot.departments:
                    await self._connection.execute(
                        text(
                            "INSERT INTO organization_departments "
                            "(department_id,parent_department_id,display_name,"
                            "subcompany_id,fetched_at) "
                            "VALUES (:department_id,:parent_department_id,:display_name,"
                            ":subcompany_id,:fetched_at)"
                        ),
                        {**department.model_dump(), "fetched_at": snapshot.fetched_at},
                    )
                for member in snapshot.memberships:
                    await self._connection.execute(
                        text(
                            "INSERT INTO organization_user_memberships "
                            "(user_id,department_id,organization_id,subcompany_id,job_title,"
                            "display_name,fetched_at) VALUES (:user_id,:department_id,"
                            ":organization_id,:subcompany_id,:job_title,:display_name,:fetched_at)"
                        ),
                        {**member.model_dump(), "fetched_at": snapshot.fetched_at},
                    )
                await self._connection.execute(
                    text(
                        "WITH moment AS (SELECT clock_timestamp() AS now) "
                        "UPDATE organization_directory_sync_state "
                        "SET snapshot_version=snapshot_version+1, "
                        "source_fetched_at=:fetched_at,last_success_at=moment.now,"
                        "last_attempt_finished_at=moment.now,last_attempt_status='succeeded',"
                        "last_error_code=NULL FROM moment WHERE singleton_id=1"
                    ),
                    {"fetched_at": snapshot.fetched_at},
                )
        except Exception:
            # The server may have committed. No failure overwrite or immediate recovery.
            raise DirectorySourceError("storage_unavailable") from None


class PostgreSQLOrganizationDirectorySync:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def read_status(self) -> OrganizationDirectorySyncStatus:
        try:
            async with self._session_factory() as session:
                connection = await session.connection()
                return await read_status(connection)
        except Exception:
            raise DirectorySourceError("storage_unavailable") from None

    @asynccontextmanager
    async def try_acquire(self) -> AsyncIterator[OrganizationDirectorySyncLease | None]:
        # Use an engine connection: a Session would return it to the pool after commit.
        async with self._session_factory() as session:
            bind = session.bind
            if not isinstance(bind, AsyncEngine):
                raise DirectorySourceError("storage_unavailable")
            async with bind.connect() as connection:
                acquired = False
                try:
                    acquired = bool(
                        (
                            await connection.execute(text(f"SELECT pg_try_advisory_lock({_LOCK})"))
                        ).scalar_one()
                    )
                    await connection.commit()
                    yield PostgreSQLDirectorySyncLease(connection) if acquired else None
                finally:
                    if acquired:
                        try:
                            if connection.in_transaction():
                                await connection.rollback()
                            await connection.execute(text(f"SELECT pg_advisory_unlock({_LOCK})"))
                            await connection.commit()
                        except Exception:
                            await connection.invalidate()
