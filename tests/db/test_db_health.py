from __future__ import annotations

import asyncio
import os
import selectors
import sys
from collections.abc import Coroutine
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL

from app.db.config import get_database_url, normalize_database_url
from app.db.health import check_database_health
from app.db.session import make_async_engine, make_async_session_factory


def _run_async(coroutine: Coroutine[Any, Any, None]) -> None:
    if sys.platform == "win32":
        asyncio.run(
            coroutine,
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
        return
    asyncio.run(coroutine)


def _database_url_from_environment() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None or not value.strip():
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return value


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_database_url()


def test_plain_postgresql_url_is_normalized_to_psycopg() -> None:
    plain_url = URL.create(
        "postgresql",
        username="example_user",
        host="localhost",
        port=5432,
        database="example_db",
    ).render_as_string()
    psycopg_url = URL.create(
        "postgresql+psycopg",
        username="example_user",
        host="localhost",
        port=5432,
        database="example_db",
    ).render_as_string()
    already_normalized_url = URL.create(
        "postgresql+psycopg",
        username="example_user",
        host="example_host",
        database="example_db",
    ).render_as_string()

    assert normalize_database_url(plain_url) == psycopg_url
    assert normalize_database_url(already_normalized_url) == already_normalized_url


def test_session_factory_supports_explicit_url_injection() -> None:
    async def run() -> None:
        engine = make_async_engine(_database_url_from_environment())
        try:
            session_factory = make_async_session_factory(engine)
            async with session_factory() as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
        finally:
            await engine.dispose()

    _run_async(run())


def test_db_health_helper_returns_true_for_select_one() -> None:
    async def run() -> None:
        assert await check_database_health(_database_url_from_environment()) is True

    _run_async(run())
