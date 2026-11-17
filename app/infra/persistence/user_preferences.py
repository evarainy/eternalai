"""PostgreSQL storage for the caller's own preferences; one row per principal."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.auth import Principal
from app.ports.user_preferences import UserPreferences, UserPreferencesError


def _json(value: object) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


class PostgreSQLUserPreferencesStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def read(self, principal: Principal) -> UserPreferences:
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    text(
                        "SELECT appearance, home_layout FROM public.user_preferences "
                        "WHERE tenant_id=:tenant AND ai_user_id=:user"
                    ),
                    {"tenant": principal.org_ctx.tenant_id, "user": principal.ai_user_id},
                )
                row = result.mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise UserPreferencesError(type(exc).__name__) from None
        return UserPreferences() if row is None else UserPreferences(**dict(row))

    async def write(self, principal: Principal, preferences: UserPreferences) -> None:
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO public.user_preferences "
                        "(tenant_id, ai_user_id, appearance, home_layout, updated_at) "
                        "VALUES (:tenant, :user, CAST(:appearance AS JSONB), "
                        "CAST(:home_layout AS JSONB), now()) "
                        "ON CONFLICT (tenant_id, ai_user_id) DO UPDATE SET "
                        "appearance=EXCLUDED.appearance, home_layout=EXCLUDED.home_layout, "
                        "updated_at=EXCLUDED.updated_at"
                    ),
                    {
                        "tenant": principal.org_ctx.tenant_id,
                        "user": principal.ai_user_id,
                        "appearance": _json(preferences.appearance),
                        "home_layout": _json(preferences.home_layout),
                    },
                )
        except SQLAlchemyError as exc:
            raise UserPreferencesError(type(exc).__name__) from None
