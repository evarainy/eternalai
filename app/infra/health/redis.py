"""Bounded Redis PING health check without a runtime client dependency."""

from __future__ import annotations

import asyncio
import ssl

from app.config import RedisConnectionURL

_MAX_REDIS_RESPONSE_BYTES = 512


class RedisHealthCheck:
    """Authenticate/select when configured, then require an exact PONG."""

    def __init__(
        self,
        *,
        redis_url: RedisConnectionURL | str,
        timeout_seconds: float,
    ) -> None:
        parsed = (
            RedisConnectionURL.parse(redis_url)
            if isinstance(redis_url, str)
            else redis_url
        )
        if timeout_seconds <= 0:
            raise ValueError("Redis health timeout must be positive")
        self._redis_url = parsed
        self._timeout_seconds = timeout_seconds
        self._ssl: ssl.SSLContext | None = (
            ssl.create_default_context() if parsed.scheme == "rediss" else None
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"redis_url={self._redis_url!r}, "
            f"timeout_seconds={self._timeout_seconds!r})"
        )

    async def __call__(self) -> bool:
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    self._redis_url.host,
                    self._redis_url.port,
                    ssl=self._ssl,
                )
                password = self._redis_url.password_for_connection()
                if password is not None:
                    auth_arguments = (
                        ("AUTH", password)
                        if self._redis_url.username is None
                        else ("AUTH", self._redis_url.username, password)
                    )
                    if await _execute(reader, writer, auth_arguments) != "OK":
                        return False
                if self._redis_url.database:
                    if await _execute(
                        reader,
                        writer,
                        ("SELECT", str(self._redis_url.database)),
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
