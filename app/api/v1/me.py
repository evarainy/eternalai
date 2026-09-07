"""Read-only endpoints answering "who am I" for the authenticated caller.

Both routes take **zero parameters** — no path segment, no query string, no
body.  Identity comes only from the signed session cookie, so one user cannot
even express another user in a request; there is no IDOR surface to defend.

The response deliberately splits identity into two halves:

* ``display_name`` comes from the server-signed session ticket, so it survives
  OA being unreachable and is not a client self-assertion.
* ``org`` / ``avatar_path`` depend on OA and may be absent.

An OA outage therefore degrades the page, it does not log everybody out.
"""

from __future__ import annotations

from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal
from app.ports.user_profile import (
    OrgProfileStatus,
    UserAvatar,
    UserProfilePort,
    UserProfileSnapshot,
)

AVATAR_PATH: Literal["/api/v1/me/avatar"] = "/api/v1/me/avatar"

# Both routes answer at a constant URL, so a stored copy carries no user in its
# cache key, and there is no server-side logout that could evict one.  On a
# shared workstation that is a real window: the next person to sign in could be
# served the previous person's identity or photo out of the browser cache
# without a single request leaving the machine.  ``no-store`` closes it without
# depending on ``Vary: Cookie`` being honoured -- the session cookie does not
# necessarily change when the person at the keyboard does.
_NO_STORE = "no-store"
_PROFILE_UNAVAILABLE_DETAIL = {
    "code": "user_profile_unavailable",
    "message": "User profile reading is unavailable.",
}
_AVATAR_NOT_FOUND_DETAIL = {
    "code": "avatar_not_found",
    "message": "No avatar is available.",
}
_AVATAR_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            "image/jpeg": {},
            "image/png": {},
            "image/gif": {},
            "image/webp": {},
        },
        "description": "The caller's own avatar image.",
    }
}


class MeOrg(BaseModel):
    """Plain-text organization labels; never raw markup, never an upstream id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_name: str | None = None
    unit_id: str | None = None
    department_name: str
    department_id: str | None = None


class MeResponse(BaseModel):
    """A 200 always means authenticated; ``org`` may still be absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authenticated: Literal[True] = True
    display_name: str
    org: MeOrg | None = None
    org_status: OrgProfileStatus
    # A constant path or nothing. The OA-side avatar location is personnel
    # information and must not reach the browser, so the type makes it
    # impossible for one to be placed here.
    avatar_path: Literal["/api/v1/me/avatar"] | None = None


def make_router(
    user_profile: UserProfilePort | None,
    require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    def configured() -> UserProfilePort:
        if user_profile is None:
            _raise_profile_unavailable()
        return user_profile

    @router.get("", response_model=MeResponse)
    async def read_me(
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> MeResponse:
        response.headers["Cache-Control"] = _NO_STORE
        port = configured()
        snapshot = await _read_snapshot(port, principal.ai_user_id)
        org = (
            MeOrg(
                unit_name=snapshot.org.unit_name,
                unit_id=snapshot.org.unit_id,
                department_name=snapshot.org.department_name,
                department_id=snapshot.org.department_id,
            )
            if snapshot.org is not None
            else None
        )
        return MeResponse(
            display_name=principal.display_name,
            org=org,
            org_status=snapshot.org_status,
            avatar_path=AVATAR_PATH if snapshot.avatar_available else None,
        )

    @router.get("/avatar", response_class=Response, responses=_AVATAR_RESPONSES)
    async def read_avatar(
        principal: Principal = Depends(require_principal),
    ) -> Response:
        port = configured()
        avatar = await _read_avatar(port, principal.ai_user_id)
        if avatar is None:
            # One uniform failure for every cause: the browser only ever does
            # one thing (fall back to the surname), and distinguishing causes
            # would hand the caller upstream detail.
            _raise_avatar_not_found()
        return Response(
            content=avatar.content,
            media_type=avatar.media_type,
            headers={
                "Cache-Control": _NO_STORE,
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


async def _read_snapshot(
    user_profile: UserProfilePort,
    ai_user_id: str,
) -> UserProfileSnapshot:
    try:
        snapshot = await user_profile.get_profile(ai_user_id)
    except Exception:
        return UserProfileSnapshot(org_status="unavailable")
    if not isinstance(snapshot, UserProfileSnapshot):
        return UserProfileSnapshot(org_status="unavailable")
    return snapshot


async def _read_avatar(
    user_profile: UserProfilePort,
    ai_user_id: str,
) -> UserAvatar | None:
    try:
        avatar = await user_profile.get_avatar(ai_user_id)
    except Exception:
        return None
    if not isinstance(avatar, UserAvatar):
        return None
    return avatar


def _raise_profile_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_PROFILE_UNAVAILABLE_DETAIL,
    )


def _raise_avatar_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=_AVATAR_NOT_FOUND_DETAIL,
    )


__all__ = ("AVATAR_PATH", "MeOrg", "MeResponse", "make_router")
