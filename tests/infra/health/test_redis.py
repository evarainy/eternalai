from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.infra.health.redis import RedisHealthCheck


class FakeReader:
    def __init__(self, *responses: bytes) -> None:
        self._responses = list(responses)

    async def readline(self) -> bytes:
        return self._responses.pop(0)


class FakeWriter:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.payloads.append(payload)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def test_redis_health_authenticates_selects_and_pings_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = FakeReader(b"+OK\r\n", b"+OK\r\n", b"+PONG\r\n")
    writer = FakeWriter()

    async def fake_open_connection(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, Any]:
        return reader, writer

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    check = RedisHealthCheck(
        redis_url="redis://pilot:redis-test-secret@redis.invalid:6379/2",
        timeout_seconds=1,
    )

    assert asyncio.run(check()) is True
    assert writer.payloads == [
        b"*3\r\n$4\r\nAUTH\r\n$5\r\npilot\r\n$17\r\nredis-test-secret\r\n",
        b"*2\r\n$6\r\nSELECT\r\n$1\r\n2\r\n",
        b"*1\r\n$4\r\nPING\r\n",
    ]
    assert writer.closed is True
    assert "redis-test-secret" not in repr(check)
    assert "redis.invalid" in repr(check)
    assert "***" in repr(check)


def test_redis_health_returns_false_on_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = FakeReader(b"-ERR unavailable\r\n")
    writer = FakeWriter()

    async def fake_open_connection(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, Any]:
        return reader, writer

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    assert (
        asyncio.run(
            RedisHealthCheck(
                redis_url="redis://redis.invalid:6379/0",
                timeout_seconds=1,
            )()
        )
        is False
    )


def test_redis_health_connection_failure_does_not_expose_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password_marker = "connection-password-marker"

    async def failed_open_connection(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, Any]:
        raise OSError(f"connection failed with {password_marker}")

    monkeypatch.setattr(asyncio, "open_connection", failed_open_connection)
    check = RedisHealthCheck(
        redis_url=(
            f"redis://pilot:{password_marker}@redis.invalid:6379/0"
        ),
        timeout_seconds=1,
    )

    assert asyncio.run(check()) is False
    assert password_marker not in repr(check)
    assert "redis.invalid" in repr(check)
