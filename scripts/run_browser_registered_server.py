"""Serve the production app with an operator-installed registered browser Site."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import keyword
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import NoReturn, cast

import uvicorn

from app.composition import ProductionComponents, build_production_components
from app.config import ProductionSettings
from app.infra.browser.local_installation import LocalBrowserInstallationDependencies
from app.main import create_production_app

OperatorFactory = Callable[
    [ProductionComponents], AbstractAsyncContextManager[LocalBrowserInstallationDependencies]
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


async def _serve(factory: OperatorFactory, port: int) -> None:
    settings = ProductionSettings.from_environment()
    if settings.browser.enabled is not True:
        raise _StartupError
    components = build_production_components(settings)
    manager = factory(components)
    if not isinstance(manager, AbstractAsyncContextManager):
        raise _StartupError
    async with manager as installation:
        application = create_production_app(
            settings,
            browser_installation=lambda current: installation
            if current is components else _reject_component_mismatch(),
            production_components=components,
        )
        server = uvicorn.Server(uvicorn.Config(
            application, host="127.0.0.1", port=port,
            access_log=False, log_level="critical", log_config=None,
        ))
        await server.serve()
        if not server.started:
            raise _StartupError


def _reject_component_mismatch() -> NoReturn:
    raise _StartupError


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--factory")
    parser.add_argument("--port", type=int, default=8000)
    try:
        args = parser.parse_args(argv)
        if (args.enable is not True or args.factory is None
                or not 1 <= args.port <= 65535):
            raise _StartupError
        asyncio.run(_serve(_load_factory(args.factory), args.port))
    except BaseException:
        # Never print a configured Site, origin, credential or exception detail.
        print("browser_registered_server_unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
