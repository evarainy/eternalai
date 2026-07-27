"""Cross-platform event-loop factory for the production ASGI server."""

from __future__ import annotations

import asyncio
import selectors
import sys


def make_event_loop(*, use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    """Use a psycopg-compatible Selector loop on Windows."""

    del use_subprocess
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


__all__ = ("make_event_loop",)
