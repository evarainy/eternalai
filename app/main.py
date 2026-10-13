"""Default ASGI entry; explicit installations use app.application."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import application as _application
from app.application import create_app
from app.composition import ProductionComponents, build_production_components
from app.config import ProductionSettings
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.d_form_installation import DFormInstallationDependencies
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    build_production_browser_vertical,
)
from app.infra.browser.multi_installation import BrowserVerticalCollection
from app.ports.browser_gateway import BrowserGatewayRegistration


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
) -> FastAPI:
    """Compatibility entry, including the existing local composition injection seams."""
    resolved = ProductionSettings.from_environment() if settings is None else settings
    return _application.create_production_app(
        resolved,
        browser_vertical=browser_vertical,
        browser_installation=browser_installation,
        production_components=production_components,
        browser_gateway_registration=browser_gateway_registration,
        _application_factory=create_app,
        _local_vertical_builder=build_production_browser_vertical,
        _component_builder=build_production_components,
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
