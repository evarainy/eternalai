import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.admin.registry import AdminRegistryService
from app.api.v1.admin import make_router as make_admin_router
from app.api.v1.auth import (
    make_require_principal,
)
from app.api.v1.auth import (
    make_router as make_auth_router,
)
from app.api.v1.browser_runs import make_router as make_browser_runs_router
from app.api.v1.credential_bindings import CredentialBindingService
from app.api.v1.credential_bindings import make_router as make_credential_binding_router
from app.api.v1.csrf import (
    make_csrf_protected_principal,
    make_require_csrf,
)
from app.api.v1.health import HealthCheck
from app.api.v1.health import make_router as make_health_router
from app.api.v1.mcp import McpApiService
from app.api.v1.mcp import make_router as make_mcp_router
from app.api.v1.me import make_router as make_me_router
from app.api.v1.runtime import make_router as make_runtime_router
from app.api.v1.work_objects import WorkObjectService
from app.api.v1.work_objects import make_router as make_work_object_router
from app.browser_skill.supervisor import BrowserWorkerSupervisor
from app.composition import ProductionComponents, build_production_components
from app.config import ProductionSettings
from app.credential_polling import CredentialPollingScheduler
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    build_local_browser_vertical,
)
from app.infra.browser.local_resource_lifecycle import RegisteredLocalSite
from app.infra.browser.systemone_http import DecisionHTTPProvider
from app.mcp.models import McpFailure
from app.organization_directory_sync import OrganizationDirectoryScheduler
from app.ports.auth import (
    AuthenticationPort,
    Principal,
    SessionRevocationStorePort,
    SessionTokenPort,
)
from app.ports.browser_chat import BrowserChatError, BrowserChatPort, BrowserRunAccessPort
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
    mcp_service: McpApiService | None = None,
    browser_chat: BrowserChatPort | None = None,
    browser_run_access: BrowserRunAccessPort | None = None,
    browser_worker_supervisor: BrowserWorkerSupervisor | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        credential_started = organization_started = browser_started = False
        try:
            if validate_workflows is not None:
                await validate_workflows()
            if credential_polling_scheduler is not None:
                await credential_polling_scheduler.start()
                credential_started = True
            if organization_directory_scheduler is not None:
                await organization_directory_scheduler.start()
                organization_started = True
            if browser_worker_supervisor is not None:
                await browser_worker_supervisor.start()
                browser_started = True
            yield
        finally:
            try:
                if browser_started and browser_worker_supervisor is not None:
                    await browser_worker_supervisor.stop()
            finally:
                try:
                    if organization_started and organization_directory_scheduler is not None:
                        await organization_directory_scheduler.stop()
                finally:
                    if credential_started and credential_polling_scheduler is not None:
                        await credential_polling_scheduler.stop()

    application = FastAPI(title="EternalAI", version="0.1.0", lifespan=lifespan)

    @application.exception_handler(BrowserChatError)
    async def browser_chat_failure_handler(
        _request: object, exc: BrowserChatError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": {"code": exc.code, "message": "Browser request is unavailable."}},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @application.exception_handler(McpFailure)
    async def mcp_failure_handler(_request: object, _exc: McpFailure) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "mcp_action_unavailable"}},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    require_principal = make_require_principal(session_tokens, session_revocations)
    require_csrf = make_require_csrf(csrf_allowed_origins)
    csrf_protected_principal = make_csrf_protected_principal(
        require_principal,
        require_csrf,
    )
    application.include_router(
        make_mcp_router(mcp_service, csrf_protected_principal), prefix="/api/v1/mcp"
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
        make_runtime_router(
            runtime, csrf_protected_principal, session_binder, browser_chat=browser_chat,
        ),
        prefix="/api/v1/runtime",
    )
    application.include_router(
        make_browser_runs_router(
            browser_run_access if browser_run_access is not None else browser_chat,
            csrf_protected_principal, session_binder,
        ),
        prefix="/api/v1",
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
        make_me_router(
            user_profile, csrf_protected_principal,
            browser_chat=browser_chat,
        ),
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
    *,
    browser_vertical: BrowserVerticalComponents | None = None,
    browser_installation: Callable[[ProductionComponents], LocalBrowserInstallationDependencies]
    | None = None,
    production_components: ProductionComponents | None = None,
) -> FastAPI:
    """Create the fail-fast production application with no optional dependency gaps."""

    resolved_settings = (
        ProductionSettings.from_environment() if settings is None else settings
    )
    if (resolved_settings.browser.enabled and browser_vertical is None
            and browser_installation is None):
        raise RuntimeError("browser_vertical_installation_required")
    if ((browser_vertical is not None or browser_installation is not None)
            and not resolved_settings.browser.enabled):
        raise RuntimeError("browser_vertical_enablement_required")
    if browser_vertical is not None and browser_installation is not None:
        raise RuntimeError("browser_vertical_installation_ambiguous")
    if not resolved_settings.session_cookie_secure:
        logging.getLogger(__name__).warning(
            "session_cookie_secure_disabled key=%s",
            "SESSION_COOKIE_SECURE",
        )
    components = (
        build_production_components(resolved_settings)
        if production_components is None else production_components
    )
    if (browser_vertical is not None
            and browser_vertical._seed.source_kind == "registered_site"):
        raise RuntimeError("browser_production_installation_required")
    if browser_installation is not None:
        installation = browser_installation(components)
        if (type(installation) is not LocalBrowserInstallationDependencies
                or not isinstance(installation.source, RegisteredLocalSite)
                or resolved_settings.browser.transport != "playwright"
                or installation.session_factory is not components.browser_session_factory
                or installation.capability_registry is not components.browser_capability_registry
                or installation.session_binder is not components.session_binder
                or installation.policy is not components.browser_policy
                or installation.sessions is not components.browser_sessions
                or installation.trace is not components.browser_trace
                or type(installation.decision) is not DecisionHTTPProvider
                or installation.decision._deployment.disposition != "local_model"
                or installation.decision._deployment.endpoint_origin
                != resolved_settings.browser.decision_origin
                or installation.source.manifest.site.decision_manifest.request_model
                != resolved_settings.browser.request_model
                or installation.source.manifest.site.decision_manifest.deployment_model
                != resolved_settings.browser.deployment_model):
            raise RuntimeError("browser_production_authority_mismatch")
        browser_vertical = build_local_browser_vertical(installation)
        if browser_vertical is None:
            raise RuntimeError("browser_vertical_installation_required")
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
        mcp_service=components.mcp_service,
        browser_chat=browser_vertical.chat if browser_vertical is not None else None,
        browser_run_access=(
            browser_vertical.submission if browser_vertical is not None else None
        ),
        browser_worker_supervisor=(
            BrowserWorkerSupervisor(
                browser_vertical.run_business_ready, cleanup_ready=browser_vertical.cleanup_ready,
                start_resources=browser_vertical.start_resources,
                stop_resources=browser_vertical.stop_resources,
            )
            if browser_vertical is not None else None
        ),
    )


_default_settings = ProductionSettings.from_environment()
if _default_settings.browser.enabled:
    @asynccontextmanager
    async def _browser_installation_required(_application: FastAPI) -> AsyncIterator[None]:
        raise RuntimeError("browser_vertical_installation_required")
        yield  # pragma: no cover

    app = FastAPI(lifespan=_browser_installation_required)
else:
    app = create_production_app(_default_settings)


__all__ = ("app", "create_app", "create_production_app")
