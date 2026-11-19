"""Storage of personal saved read flows (功能草稿,本人可用).

Every operation is keyed by the server-authenticated owner, so reading or
changing somebody else's flow is not expressible. Versions are immutable; the
head row holds the state and the current version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

UserSkillState = Literal["usable", "paused", "disabled"]
UserSkillPauseReason = Literal["capability_changed", "capability_unavailable"]
# (tenant_id, ai_user_id, bound session_id) from the trusted scope.
SessionKey = tuple[str, str, str]


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
    pause_reason: UserSkillPauseReason | None
    current_version: int
    updated_at: datetime
    payload: UserSkillVersionPayload


@dataclass(frozen=True)
class RecordEntry:
    """One completed read call of this session (exposed data only)."""

    seq: int
    turn_id: str
    capability_id: str
    capability_fingerprint: str
    arguments: dict[str, Any]
    data: dict[str, Any]


@dataclass(frozen=True)
class TrialSnapshot:
    """An immutable trial: the definition, texts and target a save publishes."""

    trial_id: str
    proposal_id: str | None
    payload: dict[str, Any]
    savable: bool
    saved_result: dict[str, Any] | None = None


class UserSkillError(Exception):
    """Storage unavailable, or a closed code: ``name_taken``, ``conflict``, ``not_found``,
    ``trial_expired``, ``not_savable``."""

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

    async def rename(
        self, owner: UserSkillOwner, skill_id: str, display_name: str
    ) -> UserSkillHead | None: ...

    async def delete(self, owner: UserSkillOwner, skill_id: str) -> bool: ...

    async def save_trial(
        self, key: SessionKey, trial_id: str, payload: UserSkillVersionPayload,
        *, refused: bool = False,
    ) -> dict[str, Any]:
        """Publish a ready trial and mark it saved in one transaction; replay a saved one.

        ``refused`` (a failed admission) is raised as ``not_admissible`` only after
        the saved replay, under the same locks.
        """
        ...


class UserSkillSessionStorePort(Protocol):
    """Session working state, keyed by the trusted (tenant, user, bound session)."""

    async def record(
        self,
        key: SessionKey,
        *,
        turn_id: str,
        capability_id: str,
        capability_fingerprint: str,
        arguments: dict[str, Any],
        data: dict[str, Any],
    ) -> bool: ...

    async def records(self, key: SessionKey) -> tuple[tuple[RecordEntry, ...], int]: ...

    async def has_records(self, key: SessionKey) -> bool: ...

    async def put_proposal(
        self, key: SessionKey, proposal_id: str, payload: dict[str, Any]
    ) -> None: ...

    async def proposal(self, key: SessionKey, proposal_id: str) -> dict[str, Any] | None: ...

    async def put_trial(self, key: SessionKey, trial: TrialSnapshot) -> None: ...

    async def trial(self, key: SessionKey, trial_id: str) -> TrialSnapshot | None: ...

    async def put_run(self, key: SessionKey, run_id: str, snapshot: dict[str, Any]) -> None: ...

    async def claim_takeover(self, key: SessionKey, run_id: str) -> bool:
        """Atomically make a live, hand-over-able run of a live flow the pending slot."""
        ...

    async def take_takeover(self, key: SessionKey) -> dict[str, Any] | None: ...


__all__ = (
    "RecordEntry",
    "SessionKey",
    "TrialSnapshot",
    "UserSkillError",
    "UserSkillHead",
    "UserSkillOwner",
    "UserSkillPauseReason",
    "UserSkillSessionStorePort",
    "UserSkillState",
    "UserSkillStorePort",
    "UserSkillVersion",
    "UserSkillVersionPayload",
)
