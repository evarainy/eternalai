"""Operator-only entry point for the existing durable browser worker supervisor."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import keyword
import signal
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import NoReturn, cast

from app.browser_skill.supervisor import BrowserWorkerSupervisor
from app.infra.browser.composition import BrowserVerticalComponents

OperatorFactory = Callable[[], AbstractAsyncContextManager[BrowserVerticalComponents]]
_GENERIC_FAILURE = "browser_worker_unavailable"


class _StartupError(ValueError):
    pass


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _StartupError from None


def _load_factory(reference: str) -> OperatorFactory:
    """Only a local operator startup argument may select an installed Python module."""
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


async def run_worker(components: BrowserVerticalComponents, stop: asyncio.Event) -> None:
    """Reuse the application supervisor and always settle its background task."""
    supervisor = BrowserWorkerSupervisor(
        components.run_business_ready, cleanup_ready=components.cleanup_ready,
    )
    try:
        await supervisor.start()
        await stop.wait()
    finally:
        await supervisor.stop()
    if supervisor.last_pass_failed:
        raise _StartupError


async def _run_installed(factory: OperatorFactory) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered: list[signal.Signals] = []
    try:
        for interrupt in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(interrupt, stop.set)
            except (NotImplementedError, RuntimeError):
                # Windows has no asyncio signal handlers; asyncio.run still
                # cancels on Ctrl+C, so run_worker's finally stops the supervisor.
                continue
            registered.append(interrupt)
        manager = factory()
        if not isinstance(manager, AbstractAsyncContextManager):
            raise _StartupError
        async with manager as components:
            if not isinstance(components, BrowserVerticalComponents):
                raise _StartupError
            await run_worker(components, stop)
    finally:
        for interrupt in registered:
            loop.remove_signal_handler(interrupt)


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(description=__doc__, allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--factory")
    try:
        args = parser.parse_args(argv)
        if not args.enable or args.factory is None:
            raise _StartupError
        factory = _load_factory(args.factory)
        asyncio.run(_run_installed(factory))
    except BaseException:
        # No argument, exception, owner, credential or protected value is logged.
        print(_GENERIC_FAILURE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
