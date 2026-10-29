"""Trusted-entry authentication API and Principal dependency."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, NoReturn, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.v1.csrf import CSRFDependency
from app.ports.auth import (
    AuthenticatedSessionContext,
    AuthenticationError,
    AuthenticationPort,
    LoginCredential,
    Principal,
    SessionRevocationStorePort,
    SessionTokenError,
    SessionTokenPort,
    VerifiedSessionToken,
    authenticated_session,
)

SESSION_COOKIE_NAME = "eternalai_session"
SESSION_COOKIE_PATH = "/api/v1"
_logger = logging.getLogger(__name__)
# Session lifecycle events; fields are limited to event, reason and instance_ref.
_SESSION_LOGGER = logging.getLogger("uvicorn.error.auth_session")
PrincipalDependency = Callable[[Request], Awaitable[Principal]]
_MAX_LOGIN_BODY_BYTES = 16_384
_RETENTION_GRACE = timedelta(seconds=300)

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


class PrincipalRoleLister(Protocol):
    def __call__(self, ai_user_id: str, *, tenant_id: str) -> Awaitable[tuple[str, ...]]: ...


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: bool


class LogoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[False] = False


class SessionStatusResponse(BaseModel):
    """Deadlines of the presented ticket; ``absolute_expires_at`` null means no cap."""

    model_config = ConfigDict(extra="forbid")

    idle_expires_at: datetime
    absolute_expires_at: datetime | None
    server_time: datetime


class RenewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["renewed", "unchanged", "ended"]
    reason: Literal["legacy", "throttled", "capped", "principal_changed"] | None = None
    session: SessionStatusResponse | None = None


def make_require_session(
    session_tokens: SessionTokenPort | None,
    session_revocations: SessionRevocationStorePort | None = None,
) -> Callable[[Request], Awaitable[AuthenticatedSessionContext]]:
    async def require_session(request: Request) -> AuthenticatedSessionContext:
        authenticated_session.set(None)
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
                context = AuthenticatedSessionContext(
                    principal=metadata.principal,
                    fingerprint=metadata.fingerprint,
                    expires_at=metadata.expires_at,
                    renewable=metadata.version == 3,
                    absolute_expires_at=metadata.absolute_expires_at,
                )
                authenticated_session.set(context)
                return context
        if invalid:
            _raise_authentication_required()
        _raise_unavailable(logout=False)

    return require_session


def make_require_principal(
    session_tokens: SessionTokenPort | None,
    session_revocations: SessionRevocationStorePort | None = None,
) -> PrincipalDependency:
    require_session = make_require_session(session_tokens, session_revocations)

    async def require_principal(request: Request) -> Principal:
        return (await require_session(request)).principal

    return require_principal


def make_router(
    authentication: AuthenticationPort | None,
    session_tokens: SessionTokenPort | None,
    *,
    session_revocations: SessionRevocationStorePort | None = None,
    require_csrf: CSRFDependency,
    session_cookie_ttl_seconds: int | None,
    session_cookie_secure: bool,
    role_lister: PrincipalRoleLister | None = None,
) -> APIRouter:
    router = APIRouter()
    require_session = make_require_session(session_tokens, session_revocations)

    def retention(metadata: VerifiedSessionToken | AuthenticatedSessionContext) -> datetime:
        # Never earlier than this copy's own deadline; an uncapped instance keeps
        # the row past any copy issued under the current idle window. Cleanup of
        # these rows still needs the PHASE2_PLAN proof that no accepted copy remains.
        instance = metadata.instance_expires_at
        if instance is not None:
            return max(metadata.expires_at, instance)
        idle = timedelta(seconds=session_cookie_ttl_seconds or 0)
        return max(metadata.expires_at, datetime.now(UTC) + idle + _RETENTION_GRACE)

    def set_session_cookie(response: Response, token: str, max_age: int) -> None:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=max_age,
            httponly=True,
            secure=session_cookie_secure,
            samesite="lax",
            path=SESSION_COOKIE_PATH,
        )

    def delete_session_cookie(response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path=SESSION_COOKIE_PATH,
            secure=session_cookie_secure,
            httponly=True,
            samesite="lax",
        )

    async def revoke_prior_instance(request: Request) -> None:
        """Revoke the instance of a presented ticket, even an expired v3 copy."""

        prior = request.cookies.get(SESSION_COOKIE_NAME)
        if prior is None:
            return
        if session_tokens is None:
            _raise_unavailable(logout=False)
        try:
            metadata = session_tokens.inspect_for_revocation(prior)
        except SessionTokenError:
            return
        except Exception:
            _raise_unavailable(logout=False)
        if metadata is None:
            return
        if session_revocations is None:
            _raise_unavailable(logout=False)
        committed = False
        try:
            await session_revocations.revoke(metadata.fingerprint, expires_at=retention(metadata))
            committed = True
        except Exception:
            pass
        if not committed:
            _raise_unavailable(logout=False)
        _SESSION_LOGGER.info(
            "auth_login_prior_instance_revoked instance_ref=%s",
            _instance_ref(metadata.fingerprint),
        )

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
        request_id = uuid4().hex
        if (
            authentication is None
            or session_tokens is None
            or session_cookie_ttl_seconds is None
            or session_cookie_ttl_seconds <= 0
        ):
            _log_login_failure(request_id, "authentication_unavailable")
            _raise_authentication_failed()
        body = await _parse_login_credential(request)
        if body is None:
            _log_login_failure(request_id, "invalid_login_request")
            _raise_authentication_failed()
        assert authentication is not None
        assert session_tokens is not None
        assert session_cookie_ttl_seconds is not None
        principal = await _authenticate(authentication, body, request_id=request_id)
        if principal is None:
            _raise_authentication_failed()
        # After OA authentication, so a prior ticket expiring meanwhile is still revoked;
        # a new ticket is issued only once that revocation has committed.
        await revoke_prior_instance(request)
        token = _issue(session_tokens, principal, request_id=request_id)
        if token is None:
            _raise_authentication_failed()
        set_session_cookie(response, token, session_cookie_ttl_seconds)
        return LoginResponse(authenticated=True)

    async def no_store_csrf(request: Request) -> None:
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
        dependencies=[Depends(no_store_csrf)],
    )
    async def logout(request: Request, response: Response) -> LogoutResponse:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token is not None:
            if session_tokens is None:
                _raise_unavailable(logout=True)
            invalid = False
            metadata = None
            try:
                # An expired v3 copy still names its instance, whose other copies
                # (another tab, a stolen renewal) must not outlive this logout.
                metadata = session_tokens.inspect_for_revocation(token)
            except SessionTokenError:
                invalid = True
            except Exception:
                pass
            else:
                invalid = metadata is None
            if metadata is None and not invalid:
                _raise_unavailable(logout=True)
            if metadata is not None:
                if session_revocations is None:
                    _raise_unavailable(logout=True)
                committed = False
                try:
                    await session_revocations.revoke(
                        metadata.fingerprint, expires_at=retention(metadata)
                    )
                    committed = True
                except Exception:
                    pass
                if not committed:
                    _raise_unavailable(logout=True)
        response.headers["Cache-Control"] = "no-store"
        delete_session_cookie(response)
        return LogoutResponse()

    @router.get(
        "/session",
        operation_id="session_api_v1_auth_session_get",
        response_model=SessionStatusResponse,
        responses={
            401: {"description": "Authentication required"},
            503: {"description": "Authentication is temporarily unavailable"},
        },
    )
    async def session(
        response: Response,
        context: AuthenticatedSessionContext = Depends(require_session),
    ) -> SessionStatusResponse:
        # Read-only: sets no cookie and does not count as activity.
        response.headers["Cache-Control"] = "no-store"
        return _session_status(context.expires_at, context.instance_expires_at)

    @router.post(
        "/renew",
        operation_id="renew_api_v1_auth_renew_post",
        response_model=RenewResponse,
        responses={
            401: {"description": "Authentication required"},
            403: {"description": "CSRF validation failed"},
            503: {"description": "Renewal is temporarily unavailable"},
        },
    )
    async def renew(request: Request, response: Response) -> RenewResponse:
        # CSRF before authentication; the subject comes only from the verified cookie.
        await no_store_csrf(request)
        context = await require_session(request)
        response.headers["Cache-Control"] = "no-store"
        if session_tokens is None or session_revocations is None or role_lister is None:
            _raise_unavailable(logout=False)
        instance_ref = _instance_ref(context.fingerprint)
        principal = context.principal
        try:
            roles = await role_lister(principal.ai_user_id, tenant_id=principal.org_ctx.tenant_id)
        except Exception:
            _log_renew_denied("roles_unavailable", instance_ref)
            _raise_unavailable(logout=False)
        if set(roles) != set(principal.roles):
            committed = False
            try:
                await session_revocations.revoke(context.fingerprint, expires_at=retention(context))
                committed = True
            except Exception:
                pass
            if not committed:
                _raise_unavailable(logout=False)
            delete_session_cookie(response)
            _SESSION_LOGGER.info(
                "auth_session_ended reason=principal_changed instance_ref=%s", instance_ref
            )
            return RenewResponse(status="ended", reason="principal_changed")
        try:
            result = session_tokens.renew(request.cookies[SESSION_COOKIE_NAME])
        except SessionTokenError:
            _log_renew_denied("invalid", instance_ref)
            _raise_authentication_required()
        except Exception:
            _log_renew_denied("renew_unavailable", instance_ref)
            _raise_unavailable(logout=False)
        if isinstance(result, str):
            _log_renew_denied(result, instance_ref)
            return RenewResponse(
                status="unchanged",
                reason=result,
                session=_session_status(context.expires_at, context.instance_expires_at),
            )
        renewed_token, renewed = result
        try:
            # A logout or login may have revoked this instance while renewing.
            revoked = await session_revocations.is_revoked(renewed.fingerprint)
        except Exception:
            _log_renew_denied("revocation_unavailable", instance_ref)
            _raise_unavailable(logout=False)
        if revoked:
            _log_renew_denied("revoked", instance_ref)
            _raise_authentication_required()
        max_age = int((renewed.expires_at - datetime.now(UTC)).total_seconds())
        set_session_cookie(response, renewed_token, max(max_age, 1))
        _SESSION_LOGGER.info("auth_session_renewed instance_ref=%s", instance_ref)
        return RenewResponse(
            status="renewed",
            session=_session_status(renewed.expires_at, renewed.instance_expires_at),
        )

    return router


def _session_status(
    idle_expires_at: datetime, instance_expires_at: datetime | None
) -> SessionStatusResponse:
    return SessionStatusResponse(
        idle_expires_at=idle_expires_at,
        absolute_expires_at=instance_expires_at,
        server_time=datetime.now(UTC),
    )


def _instance_ref(fingerprint: bytes) -> str:
    """Correlates one login instance in logs without exposing the revocation key."""

    return sha256(fingerprint).hexdigest()[:8]


def _log_renew_denied(reason: str, instance_ref: str) -> None:
    _SESSION_LOGGER.info(
        "auth_session_renew_denied reason=%s instance_ref=%s", reason, instance_ref
    )


def _log_login_failure(request_id: str, category: str) -> None:
    _logger.warning(
        "auth_login_failed route=/api/v1/auth/login status=401 request_id=%s category=%s",
        request_id,
        category,
    )


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


async def _authenticate(
    authentication: AuthenticationPort,
    credential: LoginCredential,
    *,
    request_id: str,
) -> Principal | None:
    try:
        return await authentication.authenticate(credential)
    except Exception as error:
        _log_login_failure(request_id, f"authentication_{_failure_category(error)}")
        return None


def _issue(
    session_tokens: SessionTokenPort,
    principal: Principal,
    *,
    request_id: str,
) -> str | None:
    try:
        token = session_tokens.issue(principal)
    except Exception as error:
        _log_login_failure(request_id, f"session_issue_{_failure_category(error)}")
        return None
    if not isinstance(token, str) or not token:
        _log_login_failure(request_id, "session_issue_invalid")
        return None
    return token


def _failure_category(error: Exception) -> str:
    return (
        "authentication_error"
        if isinstance(error, AuthenticationError)
        else "unexpected_exception"
    )


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
    "PrincipalRoleLister",
    "RenewResponse",
    "SESSION_COOKIE_NAME",
    "SessionStatusResponse",
    "make_require_principal",
    "make_router",
)
