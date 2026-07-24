"""Trusted-entry authentication API and Principal dependency."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.ports.auth import (
    AuthenticationError,
    AuthenticationPort,
    LoginCredential,
    Principal,
    SessionTokenPort,
)

SESSION_COOKIE_NAME = "eternalai_session"
PrincipalDependency = Callable[[Request], Awaitable[Principal]]

_AUTHENTICATION_REQUIRED_DETAIL = {
    "code": "authentication_required",
    "message": "Valid authentication is required.",
}
_AUTHENTICATION_FAILED_DETAIL = {
    "code": "authentication_failed",
    "message": "Authentication failed.",
}


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: bool


def make_require_principal(
    session_tokens: SessionTokenPort | None,
) -> PrincipalDependency:
    async def require_principal(request: Request) -> Principal:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        token_port = session_tokens
        if token_port is None or token is None:
            _raise_authentication_required()
        try:
            return token_port.verify(token)
        except Exception:
            _raise_authentication_required()

    return require_principal


def make_router(
    authentication: AuthenticationPort | None,
    session_tokens: SessionTokenPort | None,
    *,
    session_cookie_ttl_seconds: int | None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/login", response_model=LoginResponse)
    async def login(
        body: LoginCredential,
        response: Response,
    ) -> LoginResponse:
        if (
            authentication is None
            or session_tokens is None
            or session_cookie_ttl_seconds is None
            or session_cookie_ttl_seconds <= 0
        ):
            _raise_authentication_failed()
        try:
            assert authentication is not None
            assert session_tokens is not None
            assert session_cookie_ttl_seconds is not None
            principal = await authentication.authenticate(body)
            token = session_tokens.issue(principal)
        except AuthenticationError:
            _raise_authentication_failed()
        except Exception:
            _raise_authentication_failed()
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=session_cookie_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/api/v1",
        )
        return LoginResponse(authenticated=True)

    return router


def _raise_authentication_required() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_AUTHENTICATION_REQUIRED_DETAIL,
        headers={"WWW-Authenticate": "Session"},
    )


def _raise_authentication_failed() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_AUTHENTICATION_FAILED_DETAIL,
    )


__all__ = (
    "LoginResponse",
    "Principal",
    "PrincipalDependency",
    "SESSION_COOKIE_NAME",
    "make_require_principal",
    "make_router",
)
