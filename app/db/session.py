from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.config import get_database_url, normalize_database_url


def make_async_engine(database_url: str | None = None) -> AsyncEngine:
    url = get_database_url() if database_url is None else normalize_database_url(database_url)
    return create_async_engine(url, pool_pre_ping=True)


def make_async_session_factory(
    engine: AsyncEngine | None = None,
    database_url: str | None = None,
) -> async_sessionmaker[AsyncSession]:
    resolved_engine = make_async_engine(database_url) if engine is None else engine
    return async_sessionmaker(bind=resolved_engine, expire_on_commit=False)
