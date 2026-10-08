from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.browser_skill.models import BrowserSessionRef
from app.infra.browser import playwright_actions
from app.infra.browser.browserless_wire import BrowserProviderError
from tests.browser_skill.factories import binding
from tests.infra.browser.provider_factories import Closable, deployment, source


@pytest.mark.parametrize("transport", ["cdp", "playwright"])
def test_production_connector_uses_only_selected_protocol(monkeypatch, transport) -> None:
    async def run():
        calls = []

        class Context:
            async def route(self, pattern, handler):
                calls.append(("route", pattern))
                self.handler = handler

        context = Context()
        browser = Closable()
        browser.contexts = [context]

        async def native(endpoint, timeout):
            calls.append(("playwright", endpoint))
            return browser

        async def cdp(endpoint, timeout):
            calls.append(("cdp", endpoint))
            return browser

        driver = Closable()
        driver.chromium = SimpleNamespace(connect=native, connect_over_cdp=cdp)

        async def start():
            return driver

        module = SimpleNamespace(async_playwright=lambda: SimpleNamespace(start=start))
        monkeypatch.setattr(playwright_actions.importlib.metadata, "version", lambda name: "1.63.0")
        monkeypatch.setattr(playwright_actions.importlib, "import_module", lambda name: module)
        session = BrowserSessionRef(session_ref="resource", binding=binding())
        live = await playwright_actions.PlaywrightConnector().connect(
            "wss://fixture.invalid",
            session,
            deployment(transport),
            source(),
        )
        assert live.context is context and live.session == session
        assert calls == [(transport, "wss://fixture.invalid"), ("route", "**/*")]
        await live.close()
        assert browser.closed == 1 and driver.closed == 1

    asyncio.run(run())


def test_client_version_mismatch_stops_before_import_or_connection(monkeypatch) -> None:
    monkeypatch.setattr(playwright_actions.importlib.metadata, "version", lambda name: "1.62.0")
    with pytest.raises(BrowserProviderError, match="unsupported"):
        playwright_actions.PlaywrightConnector().preflight(deployment())


@pytest.mark.parametrize("name", ["DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS"])
def test_inherited_driver_logging_or_preload_is_rejected_before_spawn(monkeypatch, name) -> None:
    monkeypatch.setenv(name, "synthetic")
    with pytest.raises(BrowserProviderError, match="unsupported"):
        playwright_actions.PlaywrightConnector().preflight(deployment())
