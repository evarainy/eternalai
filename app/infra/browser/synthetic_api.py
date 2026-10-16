"""Thin isolated host for existing authenticated Runtime/browser routes only."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.v1.auth import make_require_principal
from app.api.v1.browser_runs import make_router as browser_router
from app.api.v1.csrf import make_csrf_protected_principal, make_require_csrf
from app.api.v1.me import make_router as me_router
from app.api.v1.runtime import make_router as runtime_router
from app.infra.browser.fixed_synthetic_seed import SYNTHETIC_TENANT, SYNTHETIC_USER
from app.infra.browser.synthetic_operator import SyntheticOperatorComponents
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserChatError
from app.ports.user_profile import UserAvatar, UserProfileSnapshot


class _SyntheticProfile:
    """No organization/avatar source is installed for this synthetic identity."""

    async def get_profile(self, ai_user_id: str) -> UserProfileSnapshot:
        if ai_user_id != SYNTHETIC_USER:
            raise ValueError("browser_synthetic_owner_denied")
        return UserProfileSnapshot(org_status="unavailable", avatar_available=False)

    async def get_avatar(self, ai_user_id: str) -> UserAvatar | None:
        if ai_user_id != SYNTHETIC_USER:
            raise ValueError("browser_synthetic_owner_denied")
        return None


def create_synthetic_api(components: SyntheticOperatorComponents) -> FastAPI:
    """Reuse the same authentication, CSRF, schemas and routes as the general host.

    The general app module instantiates environment-backed production components
    on import, so this host deliberately imports its existing route factories.
    There is no login/token-issuance path, general Runtime or worker in this host.
    """
    application = FastAPI(title="EternalAI synthetic browser", version="0.1.0")
    authenticate = make_require_principal(components.tokens, components.revocations)
    protected = make_csrf_protected_principal(
        authenticate, make_require_csrf(frozenset({"http://browser-synthetic-api:8000"})),
    )

    async def synthetic_principal(request: Request) -> Principal:
        principal = await protected(request)
        if (principal.org_ctx.tenant_id != SYNTHETIC_TENANT
                or principal.ai_user_id != SYNTHETIC_USER):
            raise HTTPException(403, {"code": "browser_synthetic_owner_denied"},
                                headers={"Cache-Control": "no-store"})
        return principal

    @application.exception_handler(BrowserChatError)
    async def browser_failure(_request: Request, error: BrowserChatError) -> JSONResponse:
        return JSONResponse(
            status_code=error.http_status,
            content={"detail": {"code": error.code, "message": "Browser request is unavailable."}},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    chat = components.vertical.chat
    application.include_router(runtime_router(
        None, synthetic_principal, components.binder.bind, browser_chat=chat,
    ), prefix="/api/v1/runtime")
    application.include_router(browser_router(
        chat, synthetic_principal, components.binder.bind,
    ), prefix="/api/v1")
    application.include_router(me_router(
        _SyntheticProfile(), synthetic_principal, browser_chat=chat,
    ), prefix="/api/v1/me")
    return application
