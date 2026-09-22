import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.admin.registry import AdminRegistryService
from app.api.v1.admin import make_router as make_admin_router
from app.api.v1.auth import (
    make_require_principal,
)
from app.api.v1.auth import (
    make_router as make_auth_router,
)
from app.api.v1.credential_bindings import CredentialBindingService
from app.api.v1.credential_bindings import make_router as make_credential_binding_router
from app.api.v1.csrf import (
    make_csrf_protected_principal,
    make_require_csrf,
)
from app.api.v1.health import HealthCheck
from app.api.v1.health import make_router as make_health_router
from app.api.v1.me import make_router as make_me_router
from app.api.v1.runtime import make_router as make_runtime_router
from app.api.v1.work_objects import WorkObjectService
from app.api.v1.work_objects import make_router as make_work_object_router
from app.composition import build_production_components
from app.config import ProductionSettings
from app.credential_polling import CredentialPollingScheduler
from app.organization_directory_sync import OrganizationDirectoryScheduler
from app.ports.auth import (
    AuthenticationPort,
    Principal,
    SessionRevocationStorePort,
    SessionTokenPort,
)
from app.ports.runtime import RuntimePort
from app.ports.user_profile import UserProfilePort

_EMPTY_CSRF_ALLOWED_ORIGINS: frozenset[str] = frozenset()


def create_app(
    runtime: RuntimePort | None = None,
    admin_registry_service: AdminRegistryService | None = None,
    work_object_service: WorkObjectService | None = None,
    credential_binding_service: CredentialBindingService | None = None,
    *,
    user_profile: UserProfilePort | None = None,
    authentication: AuthenticationPort | None = None,
    session_tokens: SessionTokenPort | None = None,
    session_revocations: SessionRevocationStorePort | None = None,
    session_binder: Callable[[Principal, str], str] | None = None,
    session_cookie_ttl_seconds: int | None = None,
    session_cookie_secure: bool = True,
    csrf_allowed_origins: frozenset[str] = _EMPTY_CSRF_ALLOWED_ORIGINS,
    health_checks: dict[str, HealthCheck] | None = None,
    health_timeout_seconds: float = 5.0,
    credential_polling_scheduler: CredentialPollingScheduler | None = None,
    organization_directory_scheduler: OrganizationDirectoryScheduler | None = None,
    diagnostic_checks: dict[str, HealthCheck] | None = None,
    validate_workflows: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        if validate_workflows is not None:
            await validate_workflows()
        if credential_polling_scheduler is not None:
            await credential_polling_scheduler.start()
        if organization_directory_scheduler is not None:
            await organization_directory_scheduler.start()
        try:
            yield
        finally:
            if organization_directory_scheduler is not None:
                await organization_directory_scheduler.stop()
            if credential_polling_scheduler is not None:
                await credential_polling_scheduler.stop()

    application = FastAPI(title="EternalAI", version="0.1.0", lifespan=lifespan)
    require_principal = make_require_principal(session_tokens, session_revocations)
    require_csrf = make_require_csrf(csrf_allowed_origins)
    csrf_protected_principal = make_csrf_protected_principal(
        require_principal,
        require_csrf,
    )
    application.include_router(
        make_health_router(
            health_checks,
            timeout_seconds=health_timeout_seconds,
            diagnostic_checks=diagnostic_checks,
        ),
        prefix="/api/v1",
    )
    application.include_router(
        make_auth_router(
            authentication,
            session_tokens,
            require_csrf=require_csrf,
            session_revocations=session_revocations,
            session_cookie_ttl_seconds=session_cookie_ttl_seconds,
            session_cookie_secure=session_cookie_secure,
        ),
        prefix="/api/v1/auth",
    )
    application.include_router(
        make_runtime_router(runtime, csrf_protected_principal, session_binder),
        prefix="/api/v1/runtime",
    )
    application.include_router(
        make_admin_router(admin_registry_service, csrf_protected_principal),
        prefix="/api/v1/admin",
    )
    application.include_router(
        make_work_object_router(work_object_service, csrf_protected_principal),
        prefix="/api/v1/work-objects",
    )
    application.include_router(
        make_me_router(user_profile, csrf_protected_principal),
        prefix="/api/v1/me",
    )
    application.include_router(
        make_credential_binding_router(
            credential_binding_service,
            csrf_protected_principal,
        ),
        prefix="/api/v1/credential-bindings",
    )
    return application


def create_production_app(
    settings: ProductionSettings | None = None,
) -> FastAPI:
    """Create the fail-fast production application with no optional dependency gaps."""

    resolved_settings = (
        ProductionSettings.from_environment() if settings is None else settings
    )
    if not resolved_settings.session_cookie_secure:
        logging.getLogger(__name__).warning(
            "session_cookie_secure_disabled key=%s",
            "SESSION_COOKIE_SECURE",
        )
    components = build_production_components(resolved_settings)
    return create_app(
        runtime=components.runtime,
        admin_registry_service=components.admin_registry_service,
        work_object_service=components.work_object_service,
        credential_binding_service=components.credential_binding_service,
        user_profile=components.user_profile,
        authentication=components.authentication,
        session_tokens=components.session_tokens,
        session_revocations=components.session_revocations,
        session_binder=components.session_binder.bind,
        session_cookie_ttl_seconds=components.session_cookie_ttl_seconds,
        session_cookie_secure=resolved_settings.session_cookie_secure,
        csrf_allowed_origins=resolved_settings.csrf_allowed_origins,
        health_checks=dict(components.health_checks),
        health_timeout_seconds=components.health_timeout_seconds,
        credential_polling_scheduler=components.credential_polling_scheduler,
        organization_directory_scheduler=components.organization_directory_scheduler,
        diagnostic_checks=dict(components.diagnostic_checks),
        validate_workflows=components.validate_workflows,
    )


app = create_production_app()


__all__ = ("app", "create_app", "create_production_app")
