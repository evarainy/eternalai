from __future__ import annotations

from sqlalchemy import text

from app.db.session import make_async_engine, make_async_session_factory


async def check_database_health(database_url: str | None = None) -> bool:
    engine = make_async_engine(database_url)
    try:
        session_factory = make_async_session_factory(engine)
        async with session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            return bool(result.scalar_one() == 1)
    finally:
        await engine.dispose()
