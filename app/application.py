import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.admin.d_form import AdminDFormManagementService
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
from app.infra.adapters.browser.adapter import BrowserCapabilityAdapter
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.d_form_bootstrap import DFormManagement
from app.infra.browser.d_form_installation import (
    DFormInstallationDependencies,
    build_d_form_installation,
)
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    build_production_browser_vertical,
)
from app.infra.browser.multi_installation import BrowserVerticalCollection
from app.infra.persistence.browser.chat_handoff import PostgreSQLBrowserIntentHandoff
from app.mcp.models import McpFailure
from app.organization_directory_sync import OrganizationDirectoryScheduler
from app.ports.auth import (
    AuthenticationPort,
    Principal,
    SessionRevocationStorePort,
    SessionTokenPort,
)
from app.ports.browser_chat import (
    BrowserChatError,
    BrowserChatPort,
    BrowserRunAccessPort,
    BrowserSubmissionPort,
)
from app.ports.browser_fill_stop import DFormManagementPort
from app.ports.browser_gateway import BrowserGatewayRegistration
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
    browser_submission: BrowserSubmissionPort | None = None,
    browser_management: DFormManagementPort | None = None,
    background_sync_disabled_receipt: bool = False,
    d_form_execution_without_sync: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        credential_started = organization_started = browser_started = False
        try:
            if d_form_execution_without_sync and not background_sync_disabled_receipt:
                raise RuntimeError("browser_d_form_execution_without_sync_invalid")
            if background_sync_disabled_receipt:
                worker_valid = (
                    type(browser_worker_supervisor) is BrowserWorkerSupervisor
                    if d_form_execution_without_sync else browser_worker_supervisor is None
                )
                if (credential_polling_scheduler is not None
                        or organization_directory_scheduler is not None
                        or not worker_valid
                        or validate_workflows is None):
                    raise RuntimeError("browser_background_sync_installation_invalid")
                print(json.dumps({
                    "event": ("d_form_execution_without_sync" if d_form_execution_without_sync
                              else "background_sync_disabled"),
                    "credential_polling": "not_installed",
                    "organization_directory": "not_installed",
                    "browser_worker": ("installed" if d_form_execution_without_sync
                                       else "not_installed"),
                }), flush=True)
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
            if background_sync_disabled_receipt:
                print(json.dumps({
                    "event": "lifecycle_ready", "workflow_validation": "passed",
                    **({"mode": "d_form_execution_without_sync", "browser_worker": "started"}
                       if d_form_execution_without_sync else {}),
                }), flush=True)
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
        _request: object,
        exc: BrowserChatError,
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
            runtime,
            csrf_protected_principal,
            session_binder,
            browser_chat=browser_chat,
        ),
        prefix="/api/v1/runtime",
    )
    application.include_router(
        make_browser_runs_router(
            browser_run_access if browser_run_access is not None else browser_chat,
            csrf_protected_principal,
            session_binder,
            submission=browser_submission,
        ),
        prefix="/api/v1",
    )
    admin_d_form_management = (
        AdminDFormManagementService(browser_management)
        if browser_management is not None else None
    )
    application.include_router(
        make_admin_router(
            admin_registry_service, csrf_protected_principal,
            d_form_management=admin_d_form_management,
        ),
        prefix="/api/v1/admin",
    )
    application.include_router(
        make_work_object_router(work_object_service, csrf_protected_principal),
        prefix="/api/v1/work-objects",
    )
    application.include_router(
        make_me_router(
            user_profile,
            csrf_protected_principal,
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
    browser_vertical: BrowserVerticalComponents | BrowserVerticalCollection | None = None,
    browser_installation: Callable[
        [ProductionComponents],
        LocalBrowserInstallationDependencies
        | tuple[LocalBrowserInstallationDependencies, ...]
        | DFormInstallationDependencies,
    ]
    | None = None,
    production_components: ProductionComponents | None = None,
    browser_gateway_registration: BrowserGatewayRegistration | None = None,
    browser_management: DFormManagement | None = None,
    background_sync_enabled: bool = True,
    d_form_execution_without_sync: bool = False,
    _application_factory: Callable[..., FastAPI] | None = None,
    _local_vertical_builder: Callable[..., BrowserVerticalComponents | BrowserVerticalCollection]
    | None = None,
    _component_builder: Callable[[ProductionSettings], ProductionComponents] | None = None,
) -> FastAPI:
    """Create the fail-fast production application with no optional dependency gaps."""

    if d_form_execution_without_sync and (
        background_sync_enabled or settings is None or browser_vertical is not None
        or browser_installation is None or type(production_components) is not ProductionComponents
    ):
        raise RuntimeError("browser_d_form_execution_without_sync_invalid")
    resolved_settings = ProductionSettings.from_environment() if settings is None else settings
    if (
        resolved_settings.browser.enabled
        and browser_vertical is None
        and browser_installation is None
        and browser_management is None
    ):
        raise RuntimeError("browser_vertical_installation_required")
    if (
        browser_vertical is not None or browser_installation is not None
    ) and not resolved_settings.browser.enabled:
        raise RuntimeError("browser_vertical_enablement_required")
    if browser_vertical is not None and browser_installation is not None:
        raise RuntimeError("browser_vertical_installation_ambiguous")
    if not resolved_settings.session_cookie_secure:
        logging.getLogger(__name__).warning(
            "session_cookie_secure_disabled key=%s",
            "SESSION_COOKIE_SECURE",
        )
    component_builder = (
        build_production_components if _component_builder is None else _component_builder
    )
    components = (
        component_builder(resolved_settings)
        if production_components is None
        else production_components
    )
    if browser_management is not None and (
        browser_management.components is not components
        or browser_management.loaded.settings is not resolved_settings
        or not resolved_settings.browser.enabled
    ):
        raise RuntimeError("browser_production_authority_mismatch")
    if isinstance(browser_vertical, BrowserVerticalCollection) or (
        browser_vertical is not None and browser_vertical._seed.source_kind == "registered_site"
    ):
        raise RuntimeError("browser_production_installation_required")
    if browser_installation is not None:
        supplied = browser_installation(components)
        if (d_form_execution_without_sync
                and type(supplied) is not DFormInstallationDependencies):
            raise RuntimeError("browser_d_form_execution_without_sync_invalid")
        if isinstance(supplied, DFormInstallationDependencies):
            if (
                supplied.session_factory is not components.browser_session_factory
                or supplied.capability_registry is not components.browser_capability_registry
                or supplied.session_binder is not components.session_binder
                or supplied.policy is not components.browser_policy
                or supplied.sessions is not components.browser_sessions
                or supplied.trace is not components.browser_trace
                or components.browser_intent_model != resolved_settings.llm_model
            ):
                raise RuntimeError("browser_production_authority_mismatch")
            browser_vertical = build_d_form_installation(supplied)
            if browser_management is not None:
                browser_management.install_vertical(browser_vertical)
            if browser_gateway_registration is None:
                seed = browser_vertical._seed
                browser_gateway_registration = BrowserGatewayRegistration(
                    tenant_id=supplied.binding.tenant_id,
                    user_id=supplied.binding.ai_user_id,
                    capability_id=seed.capability.capability_id,
                    capability_version=seed.capability.version,
                    skill_id=seed.skill.skill_id,
                    skill_version=seed.skill.version,
                    source_kind="registered_site",
                    effect="fill_stop",
                )
        else:
            builder = (
                build_production_browser_vertical
                if _local_vertical_builder is None
                else _local_vertical_builder
            )
            browser_vertical = builder(components, resolved_settings, supplied)
    if browser_gateway_registration is not None:
        registered = browser_gateway_registration
        if (
            not isinstance(browser_vertical, BrowserVerticalComponents)
            or browser_vertical._seed.source_kind != registered.source_kind
            or (
                registered.effect == "read_only"
                and (
                    browser_vertical._seed.effect != "read_only"
                    or browser_vertical._seed.execution_mode != "browser_async_v1"
                )
            )
            or (
                registered.effect == "fill_stop"
                and (
                    browser_vertical._seed.effect != "controlled_write"
                    or browser_vertical._seed.execution_mode != "d_form_fill_stop_v1"
                )
            )
            or browser_vertical._tenant_id != registered.tenant_id
            or browser_vertical._seed.capability.target_system != "oa"
            or browser_vertical._seed.capability.capability_id != registered.capability_id
            or browser_vertical._seed.capability.version != registered.capability_version
            or browser_vertical._seed.skill.skill_id != registered.skill_id
            or browser_vertical._seed.skill.version != registered.skill_version
            or browser_vertical.chat is None
            or components.browser_gateway is None
        ):
            raise RuntimeError("browser_gateway_installation_invalid")
        components.browser_gateway.install_browser_dispatch(
            registered,
            BrowserCapabilityAdapter(registered, browser_vertical.submission),
        )
        components.runtime.install_browser_handoff(
            PostgreSQLBrowserIntentHandoff(components.browser_session_factory),
            browser_vertical.submission,
            configured_d_form_model=(
                resolved_settings.llm_model if registered.effect == "fill_stop" else None
            ),
        )
    browser_supervisor = None
    if (
        isinstance(browser_vertical, BrowserVerticalComponents)
        and browser_vertical._seed.execution_mode == "d_form_fill_stop_v1"
    ):
        # The existing supervisor is the sole application execution loop.
        browser_supervisor = BrowserWorkerSupervisor(
            browser_vertical.run_business_ready,
            cleanup_ready=browser_vertical.cleanup_ready,
            start_resources=browser_vertical.start_resources,
            stop_resources=browser_vertical.stop_resources,
        )
    if (d_form_execution_without_sync
            and type(browser_supervisor) is not BrowserWorkerSupervisor):
        raise RuntimeError("browser_d_form_execution_without_sync_invalid")
    application_factory = create_app if _application_factory is None else _application_factory
    return application_factory(
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
        credential_polling_scheduler=(
            components.credential_polling_scheduler if background_sync_enabled else None
        ),
        organization_directory_scheduler=(
            components.organization_directory_scheduler if background_sync_enabled else None
        ),
        diagnostic_checks=dict(components.diagnostic_checks),
        validate_workflows=components.validate_workflows,
        mcp_service=components.mcp_service,
        browser_chat=browser_vertical.chat if browser_vertical is not None else None,
        browser_run_access=(browser_vertical.submission if browser_vertical is not None else None),
        browser_submission=(browser_vertical.submission if browser_vertical is not None else None),
        browser_worker_supervisor=browser_supervisor,
        browser_management=browser_management,
        **({"background_sync_disabled_receipt": True} if not background_sync_enabled else {}),
        **({"d_form_execution_without_sync": True} if d_form_execution_without_sync else {}),
    )


__all__ = ("create_app", "create_production_app")
