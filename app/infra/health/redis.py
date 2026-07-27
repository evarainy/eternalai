"""Bounded Redis PING health check without a runtime client dependency."""

from __future__ import annotations

import asyncio
import ssl
from urllib.parse import unquote, urlsplit

_MAX_REDIS_RESPONSE_BYTES = 512


class RedisHealthCheck:
    """Authenticate/select when configured, then require an exact PONG."""

    def __init__(self, *, redis_url: str, timeout_seconds: float) -> None:
        parsed = urlsplit(redis_url)
        if (
            parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("REDIS_URL must be a redis:// or rediss:// URL")
        if timeout_seconds <= 0:
            raise ValueError("Redis health timeout must be positive")
        path = parsed.path.lstrip("/")
        if path and (not path.isascii() or not path.isdigit()):
            raise ValueError("REDIS_URL database must be a non-negative integer")
        self._host = parsed.hostname
        self._port = parsed.port or 6379
        self._username = unquote(parsed.username) if parsed.username is not None else None
        self._password = unquote(parsed.password) if parsed.password is not None else None
        if self._username is not None and self._password is None:
            raise ValueError("REDIS_URL username requires a password")
        self._database = int(path) if path else 0
        self._timeout_seconds = timeout_seconds
        self._ssl: ssl.SSLContext | None = (
            ssl.create_default_context() if parsed.scheme == "rediss" else None
        )

    async def __call__(self) -> bool:
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=self._ssl,
                )
                if self._password is not None:
                    auth_arguments = (
                        ("AUTH", self._password)
                        if self._username is None
                        else ("AUTH", self._username, self._password)
                    )
                    if await _execute(reader, writer, auth_arguments) != "OK":
                        return False
                if self._database:
                    if await _execute(
                        reader,
                        writer,
                        ("SELECT", str(self._database)),
                    ) != "OK":
                        return False
                return await _execute(reader, writer, ("PING",)) == "PONG"
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
            return False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass


async def _execute(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    arguments: tuple[str, ...],
) -> str:
    writer.write(_encode_command(arguments))
    await writer.drain()
    raw = await reader.readline()
    if not raw or len(raw) > _MAX_REDIS_RESPONSE_BYTES or not raw.endswith(b"\r\n"):
        raise ValueError("Invalid Redis health response")
    if raw.startswith(b"-"):
        return ""
    if not raw.startswith(b"+"):
        raise ValueError("Unexpected Redis health response")
    return raw[1:-2].decode("ascii")


def _encode_command(arguments: tuple[str, ...]) -> bytes:
    encoded = [argument.encode("utf-8") for argument in arguments]
    chunks = [f"*{len(encoded)}\r\n".encode("ascii")]
    for argument in encoded:
        chunks.extend(
            (
                f"${len(argument)}\r\n".encode("ascii"),
                argument,
                b"\r\n",
            )
        )
    return b"".join(chunks)


__all__ = ("RedisHealthCheck",)
