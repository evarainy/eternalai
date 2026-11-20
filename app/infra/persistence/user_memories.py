"""PostgreSQL memory store with atomic limits and tombstone-aware deduplication."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.user_memories import (
    MEMORY_LIMIT,
    MemoryCandidate,
    MemoryOwner,
    UserMemory,
    UserMemoryStoreError,
)

_OWNER = "tenant_id = :tenant_id AND department_id = :department_id AND ai_user_id = :ai_user_id"
_LIVE = _OWNER + " AND deleted_at IS NULL"
_LIVE_ID = _LIVE + " AND id = :id"
_FIELDS = "id, category, content, user_edited, created_at, updated_at"
_SOURCE_EXISTS = text(
    "SELECT EXISTS (SELECT 1 FROM user_memories WHERE " + _OWNER
    + " AND source_digest = :source_digest)"
)
_INSERT = text(
    "INSERT INTO user_memories (id, tenant_id, department_id, ai_user_id, category, content, "
    "normalized, source_session_id, source_digest) VALUES "
    "(:id, :tenant_id, :department_id, :ai_user_id, :category, :content, "
    ":normalized, :session_id, :source_digest) "
    "ON CONFLICT ON CONSTRAINT uq_user_memories_owner_category_normalized DO NOTHING "
    "RETURNING id"
)


def _memory(row: RowMapping) -> UserMemory:
    return UserMemory(
        row["id"], row["category"], row["content"],
        row["user_edited"], row["created_at"], row["updated_at"],
    )


class PostgreSQLUserMemoryStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions.begin() as session:
                yield session
        except (OSError, SQLAlchemyError) as error:
            raise UserMemoryStoreError(type(error).__name__) from None

    async def list_active(self, owner: MemoryOwner) -> list[UserMemory]:
        async with self._transaction() as session:
            result = await session.execute(
                text(f"SELECT {_FIELDS} FROM user_memories WHERE {_LIVE} "
                     "ORDER BY updated_at DESC, id DESC"),
                asdict(owner),
            )
            return [_memory(row) for row in result.mappings()]

    async def update(self, owner: MemoryOwner, memory_id: UUID, content: str) -> UserMemory | None:
        async with self._transaction() as session:
            result = await session.execute(
                text("UPDATE user_memories SET content = :content, user_edited = true, "
                     f"updated_at = clock_timestamp() WHERE {_LIVE_ID} RETURNING {_FIELDS}"),
                {**asdict(owner), "id": memory_id, "content": content},
            )
            row = result.mappings().one_or_none()
            return None if row is None else _memory(row)

    async def delete(self, owner: MemoryOwner, memory_id: UUID) -> bool:
        async with self._transaction() as session:
            result = await session.execute(
                text("UPDATE user_memories SET deleted_at = clock_timestamp(), "
                     f"updated_at = clock_timestamp() WHERE {_LIVE_ID} RETURNING id"),
                {**asdict(owner), "id": memory_id},
            )
            return result.scalar_one_or_none() is not None

    async def has_source(self, owner: MemoryOwner, source_digest: str) -> bool:
        async with self._transaction() as session:
            result = await session.execute(
                _SOURCE_EXISTS, {**asdict(owner), "source_digest": source_digest}
            )
            return bool(result.scalar_one())

    async def add(
        self,
        owner: MemoryOwner,
        session_id: str,
        source_digest: str,
        candidates: list[MemoryCandidate],
    ) -> int:
        params = {**asdict(owner), "source_digest": source_digest, "session_id": session_id}
        async with self._transaction() as session:
            # Serialize count and inserts for this owner across workers.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": json.dumps(
                    ["user_memories", owner.tenant_id, owner.department_id, owner.ai_user_id]
                )},
            )
            if (await session.execute(_SOURCE_EXISTS, params)).scalar_one():
                return 0
            live = await session.execute(
                text("SELECT count(*) FROM user_memories WHERE " + _LIVE), params
            )
            slots = MEMORY_LIMIT - live.scalar_one()
            added = 0
            for candidate in candidates:
                if added >= slots:
                    break
                inserted = await session.execute(
                    _INSERT, {**params, **asdict(candidate), "id": uuid4()}
                )
                added += inserted.scalar_one_or_none() is not None
            return added
