"""Read-only identity profile contract for the authenticated principal.

The port answers exactly two questions about **the caller themselves**: which
organization unit they sit in, and what their photo is.  It deliberately takes
no lookup key other than the server-derived ``ai_user_id`` and offers no
listing, paging or by-id search, so "read somebody else's profile" is not an
expressible operation at the contract level.

Names, unit and department labels, avatar bytes and any upstream avatar URL are
personnel information: implementations must keep them out of Trace, logs,
fixtures and reports.  The contract therefore never carries an upstream URL —
only already-fetched bytes with a media type drawn from a closed whitelist.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

OrgProfileStatus: TypeAlias = Literal[
    "ok",
    "unbound",
    "expired",
    "unavailable",
    "unparsable",
]
"""Closed set of locally defined outcomes; upstream status codes never leak."""

AvatarMediaType: TypeAlias = Literal[
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
]
"""Image types we are willing to serve back. SVG is excluded: it can script."""


class UserOrgProfile(BaseModel):
    """Normalized organization placement of one user.

    Only ``department_name`` is required: the department is what the workbench
    top bar shows.  The unit is best-effort and may be absent without making
    the profile a failure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    department_name: str = Field(min_length=1, max_length=64)
    department_id: str | None = Field(default=None, pattern=r"^\d{1,18}$")
    unit_name: str | None = Field(default=None, min_length=1, max_length=64)
    unit_id: str | None = Field(default=None, pattern=r"^\d{1,18}$")


class UserProfileSnapshot(BaseModel):
    """One read of the caller's organization placement plus avatar availability.

    ``org`` is present **if and only if** ``org_status`` is ``"ok"``.  That
    invariant is enforced here rather than at each call site so no failure path
    can hand out a half-filled placeholder organization.

    ``avatar_available`` is judged independently of ``org_status``: an
    unparsable ``orginfo`` does not by itself mean the avatar is missing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_status: OrgProfileStatus
    org: UserOrgProfile | None = None
    avatar_available: bool = False

    @model_validator(mode="after")
    def _org_matches_status(self) -> UserProfileSnapshot:
        if (self.org is not None) != (self.org_status == "ok"):
            raise ValueError("org must be present exactly when org_status is 'ok'")
        return self


class UserAvatar(BaseModel):
    """One already-fetched avatar image with a whitelisted media type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    media_type: AvatarMediaType
    content: bytes = Field(repr=False)

    def __repr__(self) -> str:
        return f"UserAvatar(media_type={self.media_type!r})"

    __str__ = __repr__


class UserProfilePort(Protocol):
    """Read the calling user's own profile; there is no way to read another's."""

    async def get_profile(self, ai_user_id: str) -> UserProfileSnapshot: ...

    async def get_avatar(self, ai_user_id: str) -> UserAvatar | None: ...


__all__ = (
    "AvatarMediaType",
    "OrgProfileStatus",
    "UserAvatar",
    "UserOrgProfile",
    "UserProfilePort",
    "UserProfileSnapshot",
)
