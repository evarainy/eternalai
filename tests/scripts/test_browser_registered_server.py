"""API/worker role regressions; source only until the authorized test window."""

import asyncio
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    RegisteredProductionInputs,
)
from scripts import run_browser_registered_server as entry


@pytest.mark.parametrize("role", ["api", "worker"])
def test_roles_share_installed_authorities_and_start_only_their_host(monkeypatch, role) -> None:
    settings = SimpleNamespace(browser=SimpleNamespace(enabled=True))
    components, vertical = object(), object()
    supplied = object.__new__(RegisteredProductionInputs)
    installation = object.__new__(LocalBrowserInstallationDependencies)
    closed = []

    @asynccontextmanager
    async def factory(current):
        assert current is components
        try:
            yield supplied
        finally:
            closed.append(True)

    monkeypatch.setattr(entry.ProductionSettings, "from_environment", lambda: settings)
    monkeypatch.setattr(entry, "build_production_components", lambda current: components)
    bind = Mock(return_value=installation)
    build = Mock(return_value=vertical)
    worker = AsyncMock()
    monkeypatch.setattr(entry, "build_registered_production_installation", bind)
    monkeypatch.setattr(entry, "build_production_browser_vertical", build)
    monkeypatch.setattr(entry, "_run_worker", worker)
    application = object()
    api = ModuleType("app.application")
    api.create_production_app = Mock(return_value=application)
    monkeypatch.setitem(sys.modules, "app.application", api)
    server = SimpleNamespace(started=True, serve=AsyncMock())
    server_factory = Mock(return_value=server)
    monkeypatch.setattr(entry.uvicorn, "Server", server_factory)
    monkeypatch.setattr(entry.uvicorn, "Config", Mock())
    asyncio.run(entry._serve(factory, 8000, role=role))
    bind.assert_called_once_with(components, supplied, artifact_scope_create=role == "worker")
    assert closed == [True]
    if role == "worker":
        worker.assert_awaited_once_with(vertical)
        build.assert_called_once_with(components, settings, installation)
        api.create_production_app.assert_not_called()
        server_factory.assert_not_called()
    else:
        worker.assert_not_awaited()
        build.assert_not_called()
        api.create_production_app.assert_called_once()
        assert api.create_production_app.call_args.kwargs["production_components"] is components
        binder = api.create_production_app.call_args.kwargs["browser_installation"]
        assert binder(components) is installation
        with pytest.raises(entry._StartupError):
            binder(object())
        server.serve.assert_awaited_once()


def test_worker_cancellation_closes_the_existing_supervisor_and_resources(monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        supervisor = Mock(start=AsyncMock(side_effect=started.set), stop=AsyncMock())
        constructor = Mock(return_value=supervisor)
        monkeypatch.setattr(entry, "BrowserWorkerSupervisor", constructor)
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", Mock())
        monkeypatch.setattr(loop, "remove_signal_handler", Mock())
        monkeypatch.setattr(entry.signal, "signal", Mock())
        vertical = SimpleNamespace(
            run_business_ready=AsyncMock(), cleanup_ready=AsyncMock(),
            start_resources=AsyncMock(), stop_resources=AsyncMock(),
        )
        task = asyncio.create_task(entry._run_worker(vertical))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        constructor.assert_called_once_with(
            vertical.run_business_ready, cleanup_ready=vertical.cleanup_ready,
            start_resources=vertical.start_resources, stop_resources=vertical.stop_resources,
        )
        supervisor.stop.assert_awaited_once()
        vertical.run_business_ready.assert_not_awaited()
        vertical.cleanup_ready.assert_not_awaited()
    asyncio.run(scenario())


def test_production_api_installs_access_without_browser_supervisor(monkeypatch) -> None:
    from app import main

    settings = SimpleNamespace(
        browser=SimpleNamespace(enabled=True), session_cookie_secure=True,
        csrf_allowed_origins=frozenset(), runtime_engine="legacy",
    )
    values = {
        "runtime": None, "admin_registry_service": None, "work_object_service": None,
        "leader_roster_admin_service": None,
        "work_object_series_service": None, "work_object_series_scheduler": None,
        "credential_binding_service": None, "user_profile": None, "authentication": None,
        "session_tokens": None, "session_revocations": None,
        "session_binder": SimpleNamespace(bind=lambda _principal, session: session),
        "session_cookie_ttl_seconds": 60, "health_checks": {}, "health_timeout_seconds": 5,
        "credential_polling_scheduler": None, "organization_directory_scheduler": None,
        "diagnostic_checks": {}, "validate_workflows": None, "mcp_service": None,
    }
    components = SimpleNamespace(**values)
    vertical = SimpleNamespace(chat=None, submission=object())
    installation = object.__new__(LocalBrowserInstallationDependencies)
    factory = Mock(return_value="api_only")
    monkeypatch.setattr(main, "create_app", factory)
    monkeypatch.setattr(main, "build_production_browser_vertical", Mock(return_value=vertical))
    result = main.create_production_app(
        settings, browser_installation=lambda _components: installation,
        production_components=components,
    )
    assert result == "api_only"
    options = factory.call_args.kwargs
    assert options["browser_submission"] is vertical.submission
    assert options["browser_run_access"] is vertical.submission
    assert options.get("browser_worker_supervisor") is None
