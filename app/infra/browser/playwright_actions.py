"""Provider connection seam only; action execution belongs to the single Executor."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.browser_skill.models import BrowserSessionRef
from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.deployment_manifest import BrowserDeployment, SyntheticSource


@dataclass(repr=False)
class LiveBrowser:
    """Infra-only references. No implicit page choice, URL/title authority or serialization."""

    session: BrowserSessionRef
    browser: Any = field(repr=False)
    context: Any = field(repr=False)
    driver: Any = field(repr=False)

    async def storage_state(self) -> object:
        return await self.context.storage_state(indexed_db=True)

    async def close(self) -> None:
        try:
            await self.browser.close()
        finally:
            await self.driver.stop()


class BrowserConnector(Protocol):
    def preflight(self, deployment: BrowserDeployment) -> None: ...

    async def connect(
        self,
        endpoint: str,
        session: BrowserSessionRef,
        deployment: BrowserDeployment,
        source: SyntheticSource,
    ) -> LiveBrowser: ...


class PlaywrightConnector:
    """Explicit async Python transport. Never starts a local browser or changes protocol."""

    def preflight(self, deployment: BrowserDeployment) -> None:
        # The driver's subprocess inherits environment. Refuse debug/keylogging
        # or arbitrary Node preloads before the credential-bearing URL exists.
        if any(
            os.environ.get(name) for name in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS")
        ):
            raise BrowserProviderError("unsupported", "restore", "unsupported")
        try:
            version = importlib.metadata.version("playwright")
        except importlib.metadata.PackageNotFoundError:
            raise BrowserProviderError("unsupported", "restore", "unsupported") from None
        if ".".join(version.split(".")[:2]) != deployment.playwright_version:
            raise BrowserProviderError("unsupported", "restore", "unsupported")

    async def connect(
        self,
        endpoint: str,
        session: BrowserSessionRef,
        deployment: BrowserDeployment,
        source: SyntheticSource,
    ) -> LiveBrowser:
        self.preflight(deployment)
        module = importlib.import_module("playwright.async_api")
        driver = await module.async_playwright().start()
        try:
            if deployment.transport == "playwright":
                browser = await driver.chromium.connect(
                    endpoint,
                    timeout=deployment.timeout_seconds * 1000,
                )
            else:
                browser = await driver.chromium.connect_over_cdp(
                    endpoint,
                    timeout=deployment.timeout_seconds * 1000,
                )
            contexts = browser.contexts
            if len(contexts) != 1:
                # Never silently replace a restored context with an unauthenticated one.
                await browser.close()
                raise BrowserProviderError(
                    "unsupported",
                    "restore",
                    "state",
                    sent=True,
                    cleanup=True,
                )
            context = contexts[0]

            async def route_request(route: Any) -> None:
                if source.permits_url(route.request.url):
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")

            await context.route("**/*", route_request)
            # No real data is permitted here. Route interception is defense in depth,
            # not evidence of the future M2 network/ServiceWorker/redirect firewall.
            return LiveBrowser(session, browser, context, driver)
        except BaseException:
            await driver.stop()
            raise
