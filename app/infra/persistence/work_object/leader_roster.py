"""PostgreSQL dispatch leader roster; every statement is bound to one tenant."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.dispatch_leader_roster import (
    LeaderRole,
    LeaderRosterEntry,
    LeaderRosterError,
    LeaderRosterRevocation,
)

_LOGGER = logging.getLogger(__name__)
_COLUMNS = (
    "entry_id,tenant_id,role,directory_user_id,created_by_ai_user_id,created_at,"
    "revoked_by_ai_user_id,revoked_at"
)


class PostgreSQLLeaderRoster:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def active_leader_directory_user_ids(self, tenant_id: str) -> frozenset[str]:
        try:
            async with self._session_factory() as session:
                rows = await session.execute(
                    text(
                        "SELECT DISTINCT directory_user_id FROM dispatch_leader_roster_entries "
                        "WHERE tenant_id=:tenant AND revoked_at IS NULL"
                    ),
                    {"tenant": tenant_id},
                )
                return frozenset(rows.scalars())
        except SQLAlchemyError as exc:
            raise _unavailable("read_active", exc) from None

    async def list_entries(self, tenant_id: str) -> tuple[LeaderRosterEntry, ...]:
        try:
            async with self._session_factory() as session:
                rows = await session.execute(
                    text(
                        "SELECT " + _COLUMNS + " FROM dispatch_leader_roster_entries "
                        "WHERE tenant_id=:tenant ORDER BY created_at DESC,entry_id"
                    ),
                    {"tenant": tenant_id},
                )
                return tuple(LeaderRosterEntry.model_validate(dict(row)) for row in rows.mappings())
        except SQLAlchemyError as exc:
            raise _unavailable("list", exc) from None

    async def add_entry(
        self,
        *,
        tenant_id: str,
        entry_id: UUID,
        role: LeaderRole,
        directory_user_id: str,
        actor_ai_user_id: str,
        now: datetime,
    ) -> LeaderRosterEntry:
        try:
            async with self._session_factory() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                "INSERT INTO dispatch_leader_roster_entries "
                                "(entry_id,tenant_id,role,directory_user_id,"
                                "created_by_ai_user_id,created_at) "
                                "VALUES (:entry,:tenant,:role,:user,:actor,:now) "
                                "ON CONFLICT (tenant_id,role,directory_user_id) "
                                "WHERE revoked_at IS NULL DO NOTHING RETURNING " + _COLUMNS
                            ),
                            {
                                "entry": entry_id,
                                "tenant": tenant_id,
                                "role": role,
                                "user": directory_user_id,
                                "actor": actor_ai_user_id,
                                "now": now,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as exc:
            raise _unavailable("add", exc) from None
        if row is None:
            raise LeaderRosterError("leader_roster_entry_exists")
        return LeaderRosterEntry.model_validate(dict(row))

    async def revoke_entry(
        self,
        *,
        tenant_id: str,
        entry_id: UUID,
        actor_ai_user_id: str,
        now: datetime,
    ) -> LeaderRosterRevocation | None:
        try:
            async with self._session_factory() as session, session.begin():
                revoked = (
                    (
                        await session.execute(
                            text(
                                "UPDATE dispatch_leader_roster_entries "
                                "SET revoked_by_ai_user_id=:actor,revoked_at=:now "
                                "WHERE tenant_id=:tenant AND entry_id=:entry "
                                "AND revoked_at IS NULL RETURNING " + _COLUMNS
                            ),
                            {
                                "actor": actor_ai_user_id,
                                "now": now,
                                "tenant": tenant_id,
                                "entry": entry_id,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if revoked is not None:
                    return LeaderRosterRevocation(
                        entry=LeaderRosterEntry.model_validate(dict(revoked)), changed=True
                    )
                existing = (
                    (
                        await session.execute(
                            text(
                                "SELECT " + _COLUMNS + " FROM dispatch_leader_roster_entries "
                                "WHERE tenant_id=:tenant AND entry_id=:entry"
                            ),
                            {"tenant": tenant_id, "entry": entry_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as exc:
            raise _unavailable("revoke", exc) from None
        if existing is None:
            return None
        return LeaderRosterRevocation(
            entry=LeaderRosterEntry.model_validate(dict(existing)), changed=False
        )


def _unavailable(stage: str, exc: SQLAlchemyError) -> LeaderRosterError:
    _LOGGER.error(
        "leader_roster stage=%s code=leader_roster_unavailable error_type=%s",
        stage,
        type(exc).__name__,
    )
    return LeaderRosterError("leader_roster_unavailable")
