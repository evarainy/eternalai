"""PostgreSQL storage of personal saved read flows; owner-scoped in every query."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.user_skills import (
    UserSkillError,
    UserSkillHead,
    UserSkillOwner,
    UserSkillPauseReason,
    UserSkillState,
    UserSkillVersion,
    UserSkillVersionPayload,
)

_HEAD_COLUMNS = (
    "skill_id::text AS skill_id, display_name, state, pause_reason, current_version, updated_at"
)
_NAME_INDEX = "uq_user_skills_owner_name"


def _owner(owner: UserSkillOwner) -> dict[str, str]:
    return {"tenant": owner.tenant_id, "owner": owner.ai_user_id}


def _version_row(row: Any) -> UserSkillVersion:
    definition = row["definition"]
    if isinstance(definition, str):
        definition = json.loads(definition)
    return UserSkillVersion(
        skill_id=row["skill_id"],
        version=row["version"],
        display_name=row["display_name"],
        state=row["state"],
        current_version=row["current_version"],
        payload=UserSkillVersionPayload(
            definition=definition,
            definition_digest=bytes(row["definition_digest"]),
            purpose=row["purpose"],
            business_rules=row["business_rules"],
        ),
    )


async def _insert_version(
    session: AsyncSession,
    owner: UserSkillOwner,
    skill_id: str,
    version: int,
    payload: UserSkillVersionPayload,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.user_skill_versions (tenant_id, skill_id, version, definition, "
            "definition_digest, purpose, business_rules) VALUES (:tenant, CAST(:skill AS UUID), "
            ":version, CAST(:definition AS JSONB), :digest, :purpose, :rules)"
        ),
        {
            "tenant": owner.tenant_id,
            "skill": skill_id,
            "version": version,
            "definition": json.dumps(payload.definition, ensure_ascii=False),
            "digest": payload.definition_digest,
            "purpose": payload.purpose,
            "rules": payload.business_rules,
        },
    )


def _name_taken(exc: IntegrityError) -> bool:
    return _NAME_INDEX in str(exc.orig)


class PostgreSQLUserSkillStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(
        self, owner: UserSkillOwner, display_name: str, payload: UserSkillVersionPayload
    ) -> UserSkillHead:
        skill_id = str(uuid4())
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                "INSERT INTO public.user_skills (tenant_id, skill_id, "
                            "owner_user_id, "
                                "display_name, state, current_version) VALUES (:tenant, "
                                "CAST(:skill AS UUID), :owner, :name, 'usable', 1) RETURNING "
                                + _HEAD_COLUMNS
                            ),
                            {**_owner(owner), "skill": skill_id, "name": display_name},
                        )
                    )
                    .mappings()
                    .one()
                )
                await _insert_version(session, owner, skill_id, 1, payload)
        except IntegrityError as exc:
            raise UserSkillError("name_taken" if _name_taken(exc) else "conflict") from None
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return UserSkillHead(**dict(row))

    async def add_version(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        expected_current_version: int,
        payload: UserSkillVersionPayload,
    ) -> UserSkillHead:
        try:
            async with self._sessions() as session, session.begin():
                head = (
                    (
                        await session.execute(
                            text(
                                "SELECT current_version, state FROM public.user_skills WHERE "
                                "tenant_id=:tenant AND owner_user_id=:owner AND "
                                "skill_id=CAST(:skill AS UUID) FOR UPDATE"
                            ),
                            {**_owner(owner), "skill": skill_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if head is None:
                    raise UserSkillError("not_found")
                if head["current_version"] != expected_current_version:
                    raise UserSkillError("conflict")
                version = expected_current_version + 1
                await _insert_version(session, owner, skill_id, version, payload)
                # A new version is the repair of a paused flow; disabled stays disabled.
                row = (
                    (
                        await session.execute(
                            text(
                                "UPDATE public.user_skills SET current_version=:version, "
                                "updated_at=now(), state=CASE WHEN state='paused' THEN 'usable' "
                                "ELSE state END, pause_reason=NULL WHERE tenant_id=:tenant AND "
                                "owner_user_id=:owner AND skill_id=CAST(:skill AS UUID) RETURNING "
                                + _HEAD_COLUMNS
                            ),
                            {**_owner(owner), "skill": skill_id, "version": version},
                        )
                    )
                    .mappings()
                    .one()
                )
        except UserSkillError:
            raise
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return UserSkillHead(**dict(row))

    async def list_for_owner(self, owner: UserSkillOwner) -> list[UserSkillHead]:
        try:
            async with self._sessions() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT " + _HEAD_COLUMNS + " FROM public.user_skills WHERE "
                                "tenant_id=:tenant AND owner_user_id=:owner ORDER BY "
                                "lower(display_name), skill_id"
                            ),
                            _owner(owner),
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return [UserSkillHead(**dict(row)) for row in rows]

    async def _current(
        self, owner: UserSkillOwner, where: str, params: dict[str, Any]
    ) -> list[UserSkillVersion]:
        try:
            async with self._sessions() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT h.skill_id::text AS skill_id, h.display_name, h.state, "
                                "h.current_version, v.version, v.definition, v.definition_digest, "
                                "v.purpose, v.business_rules FROM public.user_skills h JOIN "
                                "public.user_skill_versions v ON v.tenant_id=h.tenant_id AND "
                                "v.skill_id=h.skill_id AND v.version=h.current_version WHERE "
                                "h.tenant_id=:tenant AND h.owner_user_id=:owner AND "
                                + where
                                + " ORDER BY lower(h.display_name), h.skill_id"
                            ),
                            {**_owner(owner), **params},
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return [_version_row(row) for row in rows]

    async def get_current(self, owner: UserSkillOwner, skill_id: str) -> UserSkillVersion | None:
        rows = await self._current(owner, "h.skill_id=CAST(:skill AS UUID)", {"skill": skill_id})
        return rows[0] if rows else None

    async def list_current(
        self, owner: UserSkillOwner, states: frozenset[UserSkillState]
    ) -> list[UserSkillVersion]:
        return await self._current(owner, "h.state = ANY(:states)", {"states": sorted(states)})

    async def set_state(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        *,
        from_states: frozenset[UserSkillState],
        to_state: UserSkillState,
        pause_reason: UserSkillPauseReason | None,
        expected_current_version: int,
    ) -> UserSkillHead | None:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                "UPDATE public.user_skills SET state=:to_state, "
                                "pause_reason=:reason, updated_at=now() WHERE tenant_id=:tenant "
                                "AND owner_user_id=:owner AND skill_id=CAST(:skill AS UUID) AND "
                                "state = ANY(:from_states) AND current_version=:expected "
                                "RETURNING " + _HEAD_COLUMNS
                            ),
                            {
                                **_owner(owner),
                                "skill": skill_id,
                                "to_state": to_state,
                                "reason": pause_reason,
                                "from_states": sorted(from_states),
                                "expected": expected_current_version,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return None if row is None else UserSkillHead(**dict(row))


__all__ = ("PostgreSQLUserSkillStore",)
