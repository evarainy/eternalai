"""Serve the production app with an operator-installed registered browser Site."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import keyword
import signal
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal, NoReturn, cast

import uvicorn

from app.browser_skill.supervisor import BrowserWorkerSupervisor
from app.composition import ProductionComponents, build_production_components
from app.config import ProductionSettings
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    RegisteredProductionInputs,
    build_production_browser_vertical,
    build_registered_production_installation,
)
from app.infra.browser.multi_installation import BrowserVerticalCollection

OperatorInput = LocalBrowserInstallationDependencies | RegisteredProductionInputs
OperatorFactory = Callable[
    [ProductionComponents], AbstractAsyncContextManager[
        OperatorInput | tuple[OperatorInput, ...]
    ],
]


class _StartupError(ValueError):
    pass


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _StartupError from None


def _load_factory(reference: str) -> OperatorFactory:
    """Select a local operator module, never a request supplied module."""
    if reference.count(":") != 1:
        raise _StartupError
    module_name, function_name = reference.split(":")
    identifiers = (*module_name.split("."), function_name)
    if any(
        not part or not part.isascii() or not part.isidentifier() or keyword.iskeyword(part)
        for part in identifiers
    ):
        raise _StartupError
    factory = getattr(importlib.import_module(module_name), function_name)
    if not callable(factory):
        raise _StartupError
    return cast(OperatorFactory, factory)


async def _serve(
    factory: OperatorFactory, port: int, host: str = "127.0.0.1",
    *, role: Literal["api", "worker"] = "api",
) -> None:
    settings = ProductionSettings.from_environment()
    if settings.browser.enabled is not True:
        raise _StartupError
    components = build_production_components(settings)
    manager = factory(components)
    if not isinstance(manager, AbstractAsyncContextManager):
        raise _StartupError
    async with manager as supplied:
        def bind(item: OperatorInput) -> LocalBrowserInstallationDependencies:
            if type(item) is RegisteredProductionInputs:
                return build_registered_production_installation(
                    components, item, artifact_scope_create=role == "worker",
                )
            if type(item) is LocalBrowserInstallationDependencies:
                return item
            raise _StartupError

        installation = (
            tuple(bind(item) for item in supplied)
            if isinstance(supplied, tuple) else bind(supplied)
        )
        if role == "worker":
            vertical = build_production_browser_vertical(components, settings, installation)
            await _run_worker(vertical)
            return
        if role != "api":
            raise _StartupError
        from app.main import create_production_app

        application = create_production_app(
            settings,
            browser_installation=lambda current: installation
            if current is components else _reject_component_mismatch(),
            production_components=components,
        )
        server = uvicorn.Server(uvicorn.Config(
            application, host=host, port=port,
            access_log=False, log_level="critical", log_config=None,
        ))
        await server.serve()
        if not server.started:
            raise _StartupError


async def _run_worker(
    vertical: BrowserVerticalComponents | BrowserVerticalCollection,
) -> None:
    """Only the existing PG worker owns browser resources and TTL cleanup."""
    supervisor = BrowserWorkerSupervisor(
        vertical.run_business_ready, cleanup_ready=vertical.cleanup_ready,
        start_resources=vertical.start_resources, stop_resources=vertical.stop_resources,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)}
    installed: list[tuple[signal.Signals, bool]] = []
    try:
        for item in previous:
            native = True
            try:
                loop.add_signal_handler(item, stopped.set)
            except NotImplementedError:
                native = False
                signal.signal(item, lambda _signum, _frame: loop.call_soon_threadsafe(stopped.set))
            installed.append((item, native))
        await supervisor.start()
        await stopped.wait()
    finally:
        try:
            await supervisor.stop()
        finally:
            for item, native in installed:
                if native:
                    loop.remove_signal_handler(item)
                signal.signal(item, previous[item])


def _reject_component_mismatch() -> NoReturn:
    raise _StartupError


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--factory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--role", choices=("api", "worker"), default="api")
    try:
        args = parser.parse_args(argv)
        if (args.enable is not True or args.factory is None
                or args.host not in {"127.0.0.1", "0.0.0.0"}
                or not 1 <= args.port <= 65535):
            raise _StartupError
        asyncio.run(_serve(_load_factory(args.factory), args.port, args.host, role=args.role))
    except BaseException:
        # Never print a configured Site, origin, credential or exception detail.
        print("browser_registered_server_unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
