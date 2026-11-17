"""The caller's own appearance and home layout, read and replaced as one document.

Neither route takes a user parameter: identity comes only from the signed
session, so one user cannot even express another user's preferences.
"""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal
from app.ports.user_preferences import (
    UserPreferences,
    UserPreferencesError,
    UserPreferencesPort,
)

_log = logging.getLogger(__name__)
# A real document is well under 1 KiB; the cap keeps this from becoming storage.
MAX_BODY_BYTES = 16 * 1024
# Constant URL shared by everyone on a workstation: never let the browser cache it.
_NO_STORE = {"Cache-Control": "no-store"}
_MESSAGES = {
    "user_preferences_unavailable": "Preference storage is unavailable.",
    "user_preferences_too_large": "The preferences document is too large.",
    "user_preferences_invalid": "The preferences document is not valid.",
}


def _fail(status: int, code: str) -> NoReturn:
    raise HTTPException(
        status, detail={"code": code, "message": _MESSAGES[code]}, headers=_NO_STORE,
    )


def make_router(
    store: UserPreferencesPort | None, require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    def configured() -> UserPreferencesPort:
        if store is None:
            _fail(503, "user_preferences_unavailable")
        return store

    @router.get("", response_model=UserPreferences)
    async def read_preferences(
        response: Response, principal: Principal = Depends(require_principal),
    ) -> UserPreferences:
        response.headers.update(_NO_STORE)
        try:
            return await configured().read(principal)
        except UserPreferencesError as exc:
            _log.warning("user_preferences op=read result=unavailable error=%s", exc)
            _fail(503, "user_preferences_unavailable")

    @router.put(
        "", status_code=204, response_class=Response,
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": UserPreferences.model_json_schema()},
        }}},
    )
    async def write_preferences(
        request: Request, principal: Principal = Depends(require_principal),
    ) -> Response:
        port = configured()
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            _fail(413, "user_preferences_too_large")
        try:
            preferences = UserPreferences.model_validate_json(raw)
        except ValidationError:
            _fail(422, "user_preferences_invalid")
        try:
            await port.write(principal, preferences)
        except UserPreferencesError as exc:
            _log.warning("user_preferences op=write result=unavailable error=%s", exc)
            _fail(503, "user_preferences_unavailable")
        _log.info("user_preferences op=write result=ok bytes=%d", len(raw))
        return Response(status_code=204, headers=_NO_STORE)

    return router


__all__ = ("MAX_BODY_BYTES", "make_router")
