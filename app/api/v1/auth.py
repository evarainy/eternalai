"""Trusted-entry authentication API and Principal dependency."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.csrf import CSRFDependency
from app.ports.auth import (
    AuthenticationPort,
    LoginCredential,
    Principal,
    SessionRevocationStorePort,
    SessionTokenError,
    SessionTokenPort,
)

SESSION_COOKIE_NAME = "eternalai_session"
SESSION_COOKIE_PATH = "/api/v1"
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


class LogoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[False] = False


def make_require_principal(
    session_tokens: SessionTokenPort | None,
    session_revocations: SessionRevocationStorePort | None = None,
) -> PrincipalDependency:
    async def require_principal(request: Request) -> Principal:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        token_port = session_tokens
        if token is None:
            _raise_authentication_required()
        if token_port is None:
            _raise_unavailable(logout=False)
        invalid = False
        try:
            metadata = token_port.inspect(token)
        except SessionTokenError:
            invalid = True
        except Exception:
            pass
        else:
            if session_revocations is None:
                _raise_unavailable(logout=False)
            try:
                revoked = await session_revocations.is_revoked(metadata.fingerprint)
            except Exception:
                pass
            else:
                if revoked:
                    _raise_authentication_required()
                return metadata.principal
        if invalid:
            _raise_authentication_required()
        _raise_unavailable(logout=False)

    return require_principal


def make_router(
    authentication: AuthenticationPort | None,
    session_tokens: SessionTokenPort | None,
    *,
    session_revocations: SessionRevocationStorePort | None = None,
    require_csrf: CSRFDependency,
    session_cookie_ttl_seconds: int | None,
    session_cookie_secure: bool,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/login",
        response_model=LoginResponse,
        openapi_extra=_LOGIN_REQUEST_BODY,
        dependencies=[Depends(require_csrf)],
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
            secure=session_cookie_secure,
            samesite="lax",
            path=SESSION_COOKIE_PATH,
        )
        return LoginResponse(authenticated=True)

    async def logout_csrf(request: Request) -> None:
        try:
            await require_csrf(request)
        except HTTPException as exc:
            exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
            raise

    @router.post(
        "/logout",
        operation_id="logout_api_v1_auth_logout_post",
        response_model=LogoutResponse,
        responses={
            403: {"description": "CSRF validation failed"},
            503: {"description": "Logout is temporarily unavailable"},
        },
        dependencies=[Depends(logout_csrf)],
    )
    async def logout(request: Request, response: Response) -> LogoutResponse:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token is not None:
            if session_tokens is None:
                _raise_unavailable(logout=True)
            invalid = False
            metadata = None
            try:
                metadata = session_tokens.inspect(token)
            except SessionTokenError:
                invalid = True
            except Exception:
                pass
            if metadata is None and not invalid:
                _raise_unavailable(logout=True)
            if metadata is not None:
                if session_revocations is None:
                    _raise_unavailable(logout=True)
                committed = False
                try:
                    await session_revocations.revoke(
                        metadata.fingerprint, expires_at=metadata.expires_at
                    )
                    committed = True
                except Exception:
                    pass
                if not committed:
                    _raise_unavailable(logout=True)
        response.headers["Cache-Control"] = "no-store"
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path=SESSION_COOKIE_PATH,
            secure=session_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return LogoutResponse()

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


def _raise_unavailable(*, logout: bool) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "logout_unavailable" if logout else "authentication_unavailable",
            "message": (
                "Logout is temporarily unavailable."
                if logout
                else "Authentication is temporarily unavailable."
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


__all__ = (
    "LoginResponse",
    "Principal",
    "PrincipalDependency",
    "SESSION_COOKIE_NAME",
    "make_require_principal",
    "make_router",
)
