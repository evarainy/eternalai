from collections.abc import Callable

from fastapi import FastAPI

from app.admin.registry import AdminRegistryService
from app.api.v1.admin import make_router as make_admin_router
from app.api.v1.auth import (
    make_require_principal,
)
from app.api.v1.auth import (
    make_router as make_auth_router,
)
from app.api.v1.health import HealthCheck
from app.api.v1.health import make_router as make_health_router
from app.api.v1.runtime import make_router as make_runtime_router
from app.composition import build_production_components
from app.config import ProductionSettings
from app.ports.auth import (
    AuthenticationPort,
    Principal,
    SessionTokenPort,
)
from app.ports.runtime import RuntimePort


def create_app(
    runtime: RuntimePort | None = None,
    admin_registry_service: AdminRegistryService | None = None,
    *,
    authentication: AuthenticationPort | None = None,
    session_tokens: SessionTokenPort | None = None,
    session_binder: Callable[[Principal, str], str] | None = None,
    session_cookie_ttl_seconds: int | None = None,
    health_checks: dict[str, HealthCheck] | None = None,
    health_timeout_seconds: float = 5.0,
) -> FastAPI:
    application = FastAPI(title="EternalAI", version="0.1.0")
    require_principal = make_require_principal(session_tokens)
    application.include_router(
        make_health_router(
            health_checks,
            timeout_seconds=health_timeout_seconds,
        ),
        prefix="/api/v1",
    )
    application.include_router(
        make_auth_router(
            authentication,
            session_tokens,
            session_cookie_ttl_seconds=session_cookie_ttl_seconds,
        ),
        prefix="/api/v1/auth",
    )
    application.include_router(
        make_runtime_router(runtime, require_principal, session_binder),
        prefix="/api/v1/runtime",
    )
    application.include_router(
        make_admin_router(admin_registry_service, require_principal),
        prefix="/api/v1/admin",
    )
    return application


def create_production_app(
    settings: ProductionSettings | None = None,
) -> FastAPI:
    """Create the fail-fast production application with no optional dependency gaps."""

    resolved_settings = (
        ProductionSettings.from_environment() if settings is None else settings
    )
    components = build_production_components(resolved_settings)
    return create_app(
        runtime=components.runtime,
        admin_registry_service=components.admin_registry_service,
        authentication=components.authentication,
        session_tokens=components.session_tokens,
        session_binder=components.session_binder.bind,
        session_cookie_ttl_seconds=components.session_cookie_ttl_seconds,
        health_checks=dict(components.health_checks),
        health_timeout_seconds=components.health_timeout_seconds,
    )


app = create_production_app()


__all__ = ("app", "create_app", "create_production_app")
