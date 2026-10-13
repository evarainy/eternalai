"""Serve the production app with an operator-installed registered Site or D form."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import keyword
import signal
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Literal, NoReturn, cast

import uvicorn

from app.browser_skill.supervisor import BrowserWorkerSupervisor
from app.composition import ProductionComponents, build_production_components
from app.config import ProductionSettings
from app.event_loop import make_event_loop
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.d_form_bootstrap import (
    DFormPreparedInstallation,
    bind_task_configuration,
    check_configuration,
    open_installation,
)
from app.infra.browser.d_form_configuration import (
    DFormConfigurationError,
    LoadedDFormConfiguration,
    load_configuration,
)
from app.infra.browser.d_form_installation import (
    DFormInstallationDependencies,
    DFormProductionInputs,
    build_d_form_production_installation,
)
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    RegisteredProductionInputs,
    build_production_browser_vertical,
    build_registered_production_installation,
)
from app.infra.browser.multi_installation import BrowserVerticalCollection

LocalOperatorInput = LocalBrowserInstallationDependencies | RegisteredProductionInputs
OperatorInput = (
    LocalOperatorInput | tuple[LocalOperatorInput, ...]
    | DFormProductionInputs | DFormInstallationDependencies
    | DFormPreparedInstallation
)
OperatorFactory = Callable[
    [ProductionComponents], AbstractAsyncContextManager[OperatorInput],
]


class _StartupError(ValueError):
    pass


class _DWorkerRoleError(_StartupError):
    pass


class _DMixedInstallationError(_StartupError):
    pass


class _BackgroundSyncInstallationError(_StartupError):
    pass


class _DFormExecutionWithoutSyncError(_StartupError):
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
    settings: ProductionSettings | None = None,
    background_sync_enabled: bool = True,
    d_form_execution_without_sync: bool = False,
) -> None:
    if d_form_execution_without_sync and (
        background_sync_enabled or role != "api" or host != "127.0.0.1"
        or settings is None or factory is not open_installation
    ):
        raise _DFormExecutionWithoutSyncError
    settings = ProductionSettings.from_environment() if settings is None else settings
    if settings.browser.enabled is not True:
        raise _StartupError
    components = build_production_components(settings)
    manager = factory(components)
    if not isinstance(manager, AbstractAsyncContextManager):
        raise _StartupError
    async with manager as supplied:
        if d_form_execution_without_sync:
            if (type(supplied) is not DFormPreparedInstallation
                    or type(supplied.installation) is not DFormInstallationDependencies):
                raise _DFormExecutionWithoutSyncError
        elif not background_sync_enabled and (
            role != "api" or type(supplied) is not DFormPreparedInstallation
            or supplied.installation is not None
        ):
            raise _BackgroundSyncInstallationError
        # Validate the entire shape before any installation or artifact builder.
        # The D app lifespan already owns its sole worker supervisor.
        if isinstance(supplied, tuple):
            if any(isinstance(item, (DFormProductionInputs, DFormInstallationDependencies,
                                     DFormPreparedInstallation))
                   for item in supplied):
                raise _DMixedInstallationError
            if any(type(item) not in (
                RegisteredProductionInputs, LocalBrowserInstallationDependencies,
            )
                   for item in supplied):
                raise _StartupError
        if (isinstance(supplied, (DFormProductionInputs, DFormInstallationDependencies,
                                 DFormPreparedInstallation))
                and role != "api"):
            raise _DWorkerRoleError

        def bind(
            item: LocalOperatorInput | DFormProductionInputs | DFormInstallationDependencies
            | DFormPreparedInstallation,
        ) -> LocalBrowserInstallationDependencies:
            if type(item) is RegisteredProductionInputs:
                return build_registered_production_installation(
                    components, item, artifact_scope_create=role == "worker",
                )
            if type(item) is LocalBrowserInstallationDependencies:
                return item
            raise _StartupError

        management = None
        installation: (
            LocalBrowserInstallationDependencies | tuple[LocalBrowserInstallationDependencies, ...]
            | DFormInstallationDependencies
            | None
        )
        if type(supplied) is DFormPreparedInstallation:
            installation, management = supplied.installation, supplied.management
        elif type(supplied) is DFormProductionInputs:
            installation = build_d_form_production_installation(components, supplied)
        elif type(supplied) is DFormInstallationDependencies:
            installation = supplied
        else:
            local_installation = (
                tuple(bind(item) for item in supplied)
                if isinstance(supplied, tuple) else bind(supplied)
            )
            if role == "worker":
                vertical = build_production_browser_vertical(
                    components, settings, local_installation,
                )
                await _run_worker(vertical)
                return
            if role != "api":
                raise _StartupError
            installation = local_installation
        from app.application import create_production_app

        installed = installation

        application = create_production_app(
            settings,
            browser_installation=(lambda current: installed
            if current is components else _reject_component_mismatch())
            if installed is not None else None,
            production_components=components,
            browser_management=management,
            background_sync_enabled=background_sync_enabled,
            d_form_execution_without_sync=d_form_execution_without_sync,
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


async def _serve_task(
    factory: OperatorFactory, loaded: LoadedDFormConfiguration, port: int, host: str,
    *, background_sync_enabled: bool = True,
    d_form_execution_without_sync: bool = False,
) -> None:
    async with bind_task_configuration(loaded):
        await _serve(
            factory, port, host, settings=loaded.settings,
            background_sync_enabled=background_sync_enabled,
            d_form_execution_without_sync=d_form_execution_without_sync,
        )


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--factory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--role", choices=("api", "worker"), default="api")
    parser.add_argument("--task-config", type=Path)
    parser.add_argument("--check-config", action="store_true")
    sync_modes = parser.add_mutually_exclusive_group()
    sync_modes.add_argument("--disable-background-sync", action="store_true")
    sync_modes.add_argument("--d-form-execution-without-sync", action="store_true", default=False)
    try:
        args = parser.parse_args(argv)
        if args.disable_background_sync and args.role != "api":
            raise _BackgroundSyncInstallationError
        if args.d_form_execution_without_sync and (
            args.factory != "app.infra.browser.d_form_bootstrap:open_installation"
            or args.task_config is None or args.host != "127.0.0.1" or args.role != "api"
        ):
            raise _DFormExecutionWithoutSyncError
        if (args.enable is not True or args.factory is None
                or args.host not in {"127.0.0.1", "0.0.0.0"}
                or not 1 <= args.port <= 65535):
            raise _StartupError
        if (args.task_config is None and (
            args.check_config
            or args.factory == "app.infra.browser.d_form_bootstrap:open_installation"
        )):
            raise DFormConfigurationError("d_form_config_missing")
        if args.task_config is not None:
            if (args.factory != "app.infra.browser.d_form_bootstrap:open_installation"
                    or args.role != "api" or args.host != "127.0.0.1"):
                raise _StartupError
            windows_browser_subprocesses = (
                sys.platform == "win32" and args.d_form_execution_without_sync
            )
            loaded = (
                load_configuration(args.task_config, windows_browser_subprocesses=True)
                if windows_browser_subprocesses else load_configuration(args.task_config)
            )
            gaps = check_configuration(loaded)
            if args.check_config:
                print(json.dumps({"structure_valid": True, "execution_ready": False,
                                  "gaps": gaps, "runtime_checks": "not_run"}))
                return 2 if gaps else 0
            task = _serve_task(
                _load_factory(args.factory), loaded, args.port, args.host,
                background_sync_enabled=not (
                    args.disable_background_sync or args.d_form_execution_without_sync
                ),
                d_form_execution_without_sync=args.d_form_execution_without_sync,
            )
            if windows_browser_subprocesses:
                def loop_factory() -> asyncio.AbstractEventLoop:
                    loop = make_event_loop(database_url=loaded.settings.database_url)
                    asyncio.set_event_loop(loop)
                    return loop

                asyncio.run(task, loop_factory=loop_factory)
            else:
                asyncio.run(task)
        elif args.check_config:
            raise DFormConfigurationError("d_form_config_missing")
        else:
            asyncio.run(_serve(
                _load_factory(args.factory), args.port, args.host, role=args.role,
                background_sync_enabled=not (
                    args.disable_background_sync or args.d_form_execution_without_sync
                ),
                d_form_execution_without_sync=args.d_form_execution_without_sync,
            ))
    except DFormConfigurationError as error:
        print(error.code, file=sys.stderr)
        return 2
    except _DWorkerRoleError:
        print("browser_fill_stop_api_role_required", file=sys.stderr)
        return 2
    except _DMixedInstallationError:
        print("browser_fill_stop_single_installation_required", file=sys.stderr)
        return 2
    except _BackgroundSyncInstallationError:
        print("browser_background_sync_requires_no_browser_installation", file=sys.stderr)
        return 2
    except _DFormExecutionWithoutSyncError:
        print("browser_d_form_execution_without_sync_invalid", file=sys.stderr)
        return 2
    except BaseException:
        # Never print a configured Site, origin, credential or exception detail.
        print("browser_registered_server_unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
