"""Trusted-entry authentication API and Principal dependency."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.ports.auth import (
    AuthenticationPort,
    LoginCredential,
    Principal,
    SessionTokenPort,
)

SESSION_COOKIE_NAME = "eternalai_session"
PrincipalDependency = Callable[[Request], Awaitable[Principal]]
_MAX_LOGIN_BODY_BYTES = 16_384

_AUTHENTICATION_REQUIRED_DETAIL = {
    "code": "authentication_required",
    "message": "Valid authentication is required.",
}
_AUTHENTICATION_FAILED_DETAIL = {
    "code": "authentication_failed",
    "message": "Authentication failed.",
}
_LOGIN_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": LoginCredential.model_json_schema(),
            }
        },
    }
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

    @router.post(
        "/login",
        response_model=LoginResponse,
        openapi_extra=_LOGIN_REQUEST_BODY,
    )
    async def login(
        request: Request,
        response: Response,
    ) -> LoginResponse:
        if (
            authentication is None
            or session_tokens is None
            or session_cookie_ttl_seconds is None
            or session_cookie_ttl_seconds <= 0
        ):
            _raise_authentication_failed()
        body = await _parse_login_credential(request)
        if body is None:
            _raise_authentication_failed()
        assert authentication is not None
        assert session_tokens is not None
        assert session_cookie_ttl_seconds is not None
        token = await _authenticate_and_issue(authentication, session_tokens, body)
        if token is None:
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


async def _parse_login_credential(request: Request) -> LoginCredential | None:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if (
                not declared_length.isascii()
                or not declared_length.isdigit()
                or int(declared_length) > _MAX_LOGIN_BODY_BYTES
            ):
                return None
        except (ValueError, OverflowError):
            return None
    try:
        raw_body = await request.body()
        if len(raw_body) > _MAX_LOGIN_BODY_BYTES:
            raise ValueError("login request exceeds the size limit")
        payload = json.loads(raw_body)
        return LoginCredential.model_validate(payload)
    except Exception:
        return None


async def _authenticate_and_issue(
    authentication: AuthenticationPort,
    session_tokens: SessionTokenPort,
    credential: LoginCredential,
) -> str | None:
    try:
        principal = await authentication.authenticate(credential)
        token = session_tokens.issue(principal)
        if not isinstance(token, str) or not token:
            return None
        return token
    except Exception:
        return None


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
