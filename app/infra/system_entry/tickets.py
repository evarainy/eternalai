"""Encrypted, short-lived Redis handoff between the API and the OA bridge."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.config import RedisConnectionURL
from app.ports.system_entry import SystemEntryError

_PREFIX = "eternalai:system-entry:oa:v1:"


class RedisOAEntryTickets:
    def __init__(self, *, redis_url: RedisConnectionURL, encryption_key: bytes) -> None:
        self._redis = Redis(
            host=redis_url.host, port=redis_url.port, db=redis_url.database,
            username=redis_url.username, password=redis_url.password_for_connection(),
            ssl=redis_url.scheme == "rediss", socket_connect_timeout=5, socket_timeout=5,
            retry=Retry(NoBackoff(), 0),
        )
        key = hmac.digest(encryption_key, b"eternalai-oa-entry-ticket-v1", "sha256")
        self._cipher = AESGCM(key)

    @staticmethod
    def _key(ticket: str) -> str:
        return _PREFIX + hashlib.sha256(ticket.encode()).hexdigest()

    async def issue(self, cookies: dict[str, SecretStr]) -> str:
        ticket = secrets.token_urlsafe(32)
        key = self._key(ticket)
        nonce = secrets.token_bytes(12)
        payload = json.dumps({name: value.get_secret_value() for name, value in cookies.items()})
        encrypted = nonce + self._cipher.encrypt(nonce, payload.encode(), key.encode())
        if not await self._redis.set(key, encrypted, nx=True, ex=60):
            raise SystemEntryError("entry_unavailable")
        return ticket

    async def consume(self, ticket: str) -> dict[str, str] | None:
        key = self._key(ticket)
        payload = await self._redis.getdel(key)
        if payload is None:
            return None
        decoded: dict[str, str] = json.loads(self._cipher.decrypt(
            payload[:12], payload[12:], key.encode(),
        ))
        return decoded

    async def aclose(self) -> None:
        await self._redis.aclose()
