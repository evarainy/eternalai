"""Per-tenant dispatch leader roster maintained by that tenant's administrators.

Entries name directory users; an entry grants leader dispatch authority only while it is
unrevoked and the user has exactly one current directory membership. Revocation marks
rows and never deletes them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LeaderRole = Literal["unit_leader", "supervising_leader"]


class LeaderRosterError(RuntimeError):
    def __init__(
        self, code: Literal["leader_roster_unavailable", "leader_roster_entry_exists"]
    ) -> None:
        self.code = code
        super().__init__(code)


class LeaderRosterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: UUID
    tenant_id: str
    role: LeaderRole
    directory_user_id: str = Field(min_length=1, max_length=128)
    created_by_ai_user_id: str
    created_at: datetime
    revoked_by_ai_user_id: str | None = None
    revoked_at: datetime | None = None


class LeaderRosterRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: LeaderRosterEntry
    changed: bool


class LeaderRosterReadPort(Protocol):
    async def active_leader_directory_user_ids(self, tenant_id: str) -> frozenset[str]: ...


class LeaderRosterPort(LeaderRosterReadPort, Protocol):
    async def list_entries(self, tenant_id: str) -> tuple[LeaderRosterEntry, ...]: ...

    async def add_entry(
        self,
        *,
        tenant_id: str,
        entry_id: UUID,
        role: LeaderRole,
        directory_user_id: str,
        actor_ai_user_id: str,
        now: datetime,
    ) -> LeaderRosterEntry: ...

    async def revoke_entry(
        self,
        *,
        tenant_id: str,
        entry_id: UUID,
        actor_ai_user_id: str,
        now: datetime,
    ) -> LeaderRosterRevocation | None: ...


__all__ = (
    "LeaderRole",
    "LeaderRosterEntry",
    "LeaderRosterError",
    "LeaderRosterPort",
    "LeaderRosterReadPort",
    "LeaderRosterRevocation",
)
