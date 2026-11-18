"""Storage of personal saved read flows (功能草稿,本人可用).

Every operation is keyed by the server-authenticated owner, so reading or
changing somebody else's flow is not expressible. Versions are immutable; the
head row holds the state and the current version.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

UserSkillState = Literal["usable", "paused", "disabled"]
UserSkillPauseReason = Literal["capability_changed", "capability_unavailable"]


class UserSkillOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    ai_user_id: str


class UserSkillVersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition: dict[str, Any]
    definition_digest: bytes
    purpose: str
    business_rules: str


class UserSkillHead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    display_name: str
    state: UserSkillState
    pause_reason: UserSkillPauseReason | None
    current_version: int
    updated_at: datetime


class UserSkillVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str
    version: int
    display_name: str
    state: UserSkillState
    current_version: int
    payload: UserSkillVersionPayload


class UserSkillError(Exception):
    """Storage unavailable, or a closed code: ``name_taken``, ``conflict``, ``not_found``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UserSkillStorePort(Protocol):
    async def create(
        self, owner: UserSkillOwner, display_name: str, payload: UserSkillVersionPayload
    ) -> UserSkillHead: ...

    async def add_version(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        expected_current_version: int,
        payload: UserSkillVersionPayload,
    ) -> UserSkillHead: ...

    async def list_for_owner(self, owner: UserSkillOwner) -> list[UserSkillHead]: ...

    async def get_current(
        self, owner: UserSkillOwner, skill_id: str
    ) -> UserSkillVersion | None: ...

    async def list_current(
        self, owner: UserSkillOwner, states: frozenset[UserSkillState]
    ) -> list[UserSkillVersion]: ...

    async def set_state(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        *,
        from_states: frozenset[UserSkillState],
        to_state: UserSkillState,
        pause_reason: UserSkillPauseReason | None,
        expected_current_version: int,
    ) -> UserSkillHead | None: ...


__all__ = (
    "UserSkillError",
    "UserSkillHead",
    "UserSkillOwner",
    "UserSkillPauseReason",
    "UserSkillState",
    "UserSkillStorePort",
    "UserSkillVersion",
    "UserSkillVersionPayload",
)
