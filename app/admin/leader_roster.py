"""Admin-only maintenance of the per-tenant dispatch leader roster.

The tenant always comes from the authenticated principal. Candidates and additions need
a fresh directory with one unique membership; revocation never depends on the directory
so authority can be withdrawn during a directory outage.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, NoReturn
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.admin.actions import ADMIN_POLICY_CAPABILITY_BY_ACTION, AdminAction
from app.admin.registry import AdminRequestContext, AdminRoleNotAllowedError
from app.organization_directory_access import (
    DEFAULT_MAX_AGE_S,
    DirectoryAccessError,
    FreshDirectoryView,
    read_fresh_directory_view,
)
from app.ports.dispatch_leader_roster import (
    LeaderRole,
    LeaderRosterEntry,
    LeaderRosterError,
    LeaderRosterPort,
)
from app.ports.organization_directory import OrganizationDirectoryPort
from app.ports.policy_guard import ManagementPlanePolicyContext, PolicyGuardPort

_LOGGER = logging.getLogger(__name__)
# The directory mirror serves only this tenant until per-tenant directory storage exists.
_DIRECTORY_TENANT = "default"

DirectoryStatus = Literal["found", "missing", "ambiguous", "unavailable"]


class AdminLeaderRosterError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LeaderRosterEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    role: LeaderRole
    directory_user_id: str
    active: bool
    directory_status: DirectoryStatus
    display_name: str | None
    department_display_name: str | None
    created_by_ai_user_id: str
    created_at: datetime
    revoked_by_ai_user_id: str | None
    revoked_at: datetime | None


class LeaderRosterListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LeaderRosterEntryView]
    directory_unavailable_code: str | None


class LeaderRosterCandidateView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory_user_id: str
    display_name: str
    department_id: str
    department_display_name: str


class LeaderRosterCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LeaderRosterCandidateView]
    has_more: bool


class LeaderRosterAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: LeaderRole
    directory_user_id: str = Field(min_length=1, max_length=128)


class LeaderRosterRevokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    role: LeaderRole
    revoked_at: datetime
    changed: bool


class AdminLeaderRosterService:
    def __init__(
        self,
        *,
        roster: LeaderRosterPort,
        organization_directory: OrganizationDirectoryPort,
        policy_guard: PolicyGuardPort,
        directory_max_age_s: int = DEFAULT_MAX_AGE_S,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._roster = roster
        self._directory = organization_directory
        self._policy_guard = policy_guard
        self._directory_max_age_s = directory_max_age_s
        self._clock = clock
        self._monotonic = monotonic

    async def list_entries(self, context: AdminRequestContext) -> LeaderRosterListResponse:
        await self._authorize("leader_roster_list", context)
        tenant = context.org_ctx.tenant_id
        try:
            entries = await self._roster.list_entries(tenant)
        except LeaderRosterError as exc:
            self._fail("list", exc.code)
        fresh: FreshDirectoryView | None = None
        reason: str | None = None
        try:
            fresh = await self._fresh(tenant)
        except AdminLeaderRosterError as exc:
            # Roster rows stay visible without names so administrators can still revoke.
            reason = exc.code
        return LeaderRosterListResponse(
            items=[_entry_view(entry, fresh) for entry in entries],
            directory_unavailable_code=reason,
        )

    async def list_candidates(
        self, context: AdminRequestContext, *, query: str = "", limit: int = 50
    ) -> LeaderRosterCandidatesResponse:
        await self._authorize("leader_roster_candidates", context)
        fresh = await self._fresh(context.org_ctx.tenant_id)
        needle = query.casefold()
        candidates = sorted(
            (
                LeaderRosterCandidateView(
                    directory_user_id=member.user_id,
                    display_name=member.display_name or member.user_id,
                    department_id=member.department_id,
                    department_display_name=fresh.departments[member.department_id].display_name,
                )
                for member in fresh.view.memberships
                if len(fresh.memberships[member.user_id]) == 1
                and member.department_id in fresh.departments
                and (
                    not needle
                    or needle in (member.display_name or member.user_id).casefold()
                    or needle in member.user_id.casefold()
                )
            ),
            key=lambda item: (item.department_id, item.directory_user_id),
        )
        return LeaderRosterCandidatesResponse(
            items=candidates[:limit], has_more=len(candidates) > limit
        )

    async def add_entry(
        self, request: LeaderRosterAddRequest, context: AdminRequestContext
    ) -> LeaderRosterEntryView:
        await self._authorize("leader_roster_add", context)
        tenant = context.org_ctx.tenant_id
        fresh = await self._fresh(tenant)
        members = fresh.memberships.get(request.directory_user_id, [])
        if len(members) > 1:
            self._fail("add", "leader_roster_person_ambiguous")
        if not members or members[0].department_id not in fresh.departments:
            self._fail("add", "leader_roster_person_not_found")
        try:
            fresh.recheck()
        except DirectoryAccessError as exc:
            self._fail("add", exc.code)
        try:
            entry = await self._roster.add_entry(
                tenant_id=tenant,
                entry_id=uuid4(),
                role=request.role,
                directory_user_id=request.directory_user_id,
                actor_ai_user_id=context.ai_user_id,
                now=self._clock(),
            )
        except LeaderRosterError as exc:
            self._fail("add", exc.code)
        _LOGGER.info("leader_roster stage=added entry_id=%s role=%s", entry.entry_id, entry.role)
        return _entry_view(entry, fresh)

    async def revoke_entry(
        self, entry_id: UUID, context: AdminRequestContext
    ) -> LeaderRosterRevokeResponse:
        await self._authorize("leader_roster_revoke", context)
        try:
            result = await self._roster.revoke_entry(
                tenant_id=context.org_ctx.tenant_id,
                entry_id=entry_id,
                actor_ai_user_id=context.ai_user_id,
                now=self._clock(),
            )
        except LeaderRosterError as exc:
            self._fail("revoke", exc.code)
        if result is None:
            # Absent and other-tenant entries are indistinguishable to the caller.
            self._fail("revoke", "leader_roster_entry_not_found")
        assert result.entry.revoked_at is not None
        _LOGGER.info(
            "leader_roster stage=revoked entry_id=%s role=%s changed=%s",
            result.entry.entry_id,
            result.entry.role,
            result.changed,
        )
        return LeaderRosterRevokeResponse(
            entry_id=result.entry.entry_id,
            role=result.entry.role,
            revoked_at=result.entry.revoked_at,
            changed=result.changed,
        )

    async def _authorize(self, action: AdminAction, context: AdminRequestContext) -> None:
        decision = await self._policy_guard.decide(
            ai_user_id=context.ai_user_id,
            capability_id=ADMIN_POLICY_CAPABILITY_BY_ACTION[action],
            arguments={},
            request_context=ManagementPlanePolicyContext(
                request_id=context.trace_id,
                tenant_id=context.org_ctx.tenant_id,
                org_id=context.org_ctx.org_id,
                department_id=context.org_ctx.department_id,
                roles=list(context.roles),
            ),
        )
        if decision.decision == "allow":
            return
        reason = decision.reason_code or "policy_denied"
        _LOGGER.warning("leader_roster stage=denied action=%s code=%s", action, reason)
        raise AdminRoleNotAllowedError(reason)

    async def _fresh(self, tenant_id: str) -> FreshDirectoryView:
        if tenant_id != _DIRECTORY_TENANT:
            raise AdminLeaderRosterError("directory_scope_denied")
        try:
            return await read_fresh_directory_view(
                self._directory,
                max_age_s=self._directory_max_age_s,
                monotonic=self._monotonic,
            )
        except DirectoryAccessError as exc:
            raise AdminLeaderRosterError(exc.code) from None

    @staticmethod
    def _fail(stage: str, code: str) -> NoReturn:
        _LOGGER.warning("leader_roster stage=%s code=%s", stage, code)
        raise AdminLeaderRosterError(code)


def _entry_view(
    entry: LeaderRosterEntry, fresh: FreshDirectoryView | None
) -> LeaderRosterEntryView:
    status: DirectoryStatus = "unavailable"
    display_name = department_name = None
    if fresh is not None:
        members = fresh.memberships.get(entry.directory_user_id, [])
        if len(members) > 1:
            status = "ambiguous"
        elif not members or members[0].department_id not in fresh.departments:
            status = "missing"
        else:
            status = "found"
            display_name = members[0].display_name or members[0].user_id
            department_name = fresh.departments[members[0].department_id].display_name
    return LeaderRosterEntryView(
        entry_id=entry.entry_id,
        role=entry.role,
        directory_user_id=entry.directory_user_id,
        active=entry.revoked_at is None,
        directory_status=status,
        display_name=display_name,
        department_display_name=department_name,
        created_by_ai_user_id=entry.created_by_ai_user_id,
        created_at=entry.created_at,
        revoked_by_ai_user_id=entry.revoked_by_ai_user_id,
        revoked_at=entry.revoked_at,
    )


__all__ = (
    "AdminLeaderRosterError",
    "AdminLeaderRosterService",
    "LeaderRosterAddRequest",
    "LeaderRosterCandidateView",
    "LeaderRosterCandidatesResponse",
    "LeaderRosterEntryView",
    "LeaderRosterListResponse",
    "LeaderRosterRevokeResponse",
)
