"""Transport-level CSRF validation dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import NoReturn

from fastapi import HTTPException, Request, status

from app.ports.auth import Principal

CSRF_HEADER_NAME = "X-EternalAI-CSRF"
CSRF_HEADER_VALUE = "1"

CSRFDependency = Callable[[Request], Awaitable[None]]
PrincipalDependency = Callable[[Request], Awaitable[Principal]]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CSRF_VALIDATION_FAILED_DETAIL = {
    "code": "csrf_validation_failed",
    "message": "CSRF validation failed.",
}


def make_require_csrf(
    allowed_origins: Collection[str],
) -> CSRFDependency:
    configured_origins = frozenset(allowed_origins)

    async def require_csrf(request: Request) -> None:
        if request.method.upper() in _SAFE_METHODS:
            return
        origin_values = request.headers.getlist("origin")
        csrf_header_values = request.headers.getlist(CSRF_HEADER_NAME)
        if (
            len(origin_values) != 1
            or origin_values[0] not in configured_origins
            or len(csrf_header_values) != 1
            or csrf_header_values[0] != CSRF_HEADER_VALUE
        ):
            _raise_csrf_validation_failed()

    return require_csrf


def make_csrf_protected_principal(
    require_principal: PrincipalDependency,
    require_csrf: CSRFDependency,
) -> PrincipalDependency:
    async def csrf_protected_principal(request: Request) -> Principal:
        principal = await require_principal(request)
        await require_csrf(request)
        return principal

    return csrf_protected_principal


def _raise_csrf_validation_failed() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_CSRF_VALIDATION_FAILED_DETAIL,
    )


__all__ = (
    "CSRFDependency",
    "CSRF_HEADER_NAME",
    "CSRF_HEADER_VALUE",
    "make_csrf_protected_principal",
    "make_require_csrf",
)
