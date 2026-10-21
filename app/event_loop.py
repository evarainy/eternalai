"""Cross-platform event-loop factory for the production ASGI server."""

from __future__ import annotations

import asyncio
import logging
import selectors
import sys

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.db.config import get_database_url, normalize_database_url

# Uvicorn configures this logger before calling the loop factory.
_LOGGER = logging.getLogger("uvicorn.error")


def make_event_loop(
    *, use_subprocess: bool = False, database_url: str | None = None
) -> asyncio.AbstractEventLoop:
    """Match the Windows loop to an explicit URL or the process database driver."""

    del use_subprocess
    if sys.platform == "win32":
        try:
            url = (
                get_database_url()
                if database_url is None
                else normalize_database_url(database_url)
            )
            driver = make_url(url).drivername
        except (ArgumentError, RuntimeError, ValueError):
            _LOGGER.error("stage=event_loop code=database_driver_configuration_invalid")
            raise RuntimeError("database_driver_configuration_invalid") from None
        if driver == "postgresql+asyncpg":
            _LOGGER.info("stage=event_loop driver=asyncpg loop=proactor")
            return asyncio.ProactorEventLoop()
        driver_label = "psycopg" if driver == "postgresql+psycopg" else "other"
        _LOGGER.info("stage=event_loop driver=%s loop=selector", driver_label)
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


__all__ = ("make_event_loop",)
