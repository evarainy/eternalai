"""Primary-database, commit-confirmed revocation of one signed session."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.auth import SessionRevocationStoreError


class PostgreSQLSessionRevocationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_revoked(self, fingerprint: bytes) -> bool:
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    text("SELECT 1 FROM auth_session_revocations WHERE token_fingerprint = :fp"),
                    {"fp": fingerprint},
                )
                revoked = result.scalar_one_or_none() is not None
        except Exception:
            pass
        else:
            return revoked
        raise SessionRevocationStoreError("session revocation storage is unavailable")

    async def revoke(self, fingerprint: bytes, *, expires_at: datetime) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO auth_session_revocations (token_fingerprint, expires_at) "
                        "VALUES (:fp, :expires_at) ON CONFLICT (token_fingerprint) DO NOTHING"
                    ),
                    {"fp": fingerprint, "expires_at": expires_at},
                )
        except Exception:
            pass
        else:
            return
        raise SessionRevocationStoreError("session revocation storage is unavailable")
