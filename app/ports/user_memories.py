"""Persistence contract for a user's personal memories within a tenant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

MemoryCategory = Literal["answer_style", "frequent_topic", "work_background"]
MEMORY_LIMIT = 50
MEMORY_CONTENT_LIMIT = 200


@dataclass(frozen=True, slots=True)
class MemoryOwner:
    tenant_id: str
    ai_user_id: str


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    category: MemoryCategory
    content: str
    normalized: str


@dataclass(frozen=True, slots=True)
class UserMemory:
    id: UUID
    category: MemoryCategory
    content: str
    user_edited: bool
    created_at: datetime
    updated_at: datetime


class UserMemoryStoreError(RuntimeError):
    """A memory persistence failure with a redacted error code."""


class UserMemoryStore(Protocol):
    async def list_active(self, owner: MemoryOwner) -> list[UserMemory]: ...

    async def update(
        self, owner: MemoryOwner, memory_id: UUID, content: str,
    ) -> UserMemory | None: ...

    async def delete(self, owner: MemoryOwner, memory_id: UUID) -> bool: ...

    async def has_source(self, owner: MemoryOwner, source_digest: str) -> bool: ...

    async def add(
        self,
        owner: MemoryOwner,
        session_id: str,
        source_digest: str,
        candidates: list[MemoryCandidate],
    ) -> int: ...
