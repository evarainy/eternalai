"""Deterministic authenticated-principal fakes for API tests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.infra.auth.crypto import PrincipalSessionBinder
from app.ports.auth import (
    Principal,
    PrincipalOrgContext,
    SessionTokenError,
    VerifiedSessionToken,
)

AUTH_COOKIE_NAME = "eternalai_session"
AUTH_TOKEN = "synthetic-auth-token"
TEST_ORIGIN = "https://app.example.gov.cn"
TEST_CSRF_ALLOWED_ORIGINS = frozenset({TEST_ORIGIN})
TEST_CSRF_HEADERS = {
    "Origin": TEST_ORIGIN,
    "X-EternalAI-CSRF": "1",
}


class StaticSessionTokens:
    def __init__(self, *, roles: tuple[str, ...] = ("admin",)) -> None:
        self.principal = Principal(
            ai_user_id="usr_v1_synthetic",
            display_name="Synthetic User",
            roles=roles,
            org_ctx=PrincipalOrgContext(tenant_id="default"),
        )

    def issue(self, principal: Principal) -> str:
        self.principal = principal
        return AUTH_TOKEN

    def verify(self, token: str) -> Principal:
        if token != AUTH_TOKEN:
            raise SessionTokenError("session token is invalid")
        return self.principal

    def inspect(self, token: str) -> VerifiedSessionToken:
        return VerifiedSessionToken(
            principal=self.verify(token),
            fingerprint=hashlib.sha256(token.encode()).digest(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            version=2,
        )


class MemorySessionRevocations:
    """Explicit test-only store; production never defaults to this adapter."""

    def __init__(self) -> None:
        self.revoked: set[bytes] = set()

    async def is_revoked(self, fingerprint: bytes) -> bool:
        return fingerprint in self.revoked

    async def revoke(self, fingerprint: bytes, *, expires_at: datetime) -> None:
        self.revoked.add(fingerprint)


def make_session_binder() -> Callable[[Principal, str], str]:
    binder = PrincipalSessionBinder(binding_key=bytes(range(32)))
    return binder.bind


def auth_cookies() -> dict[str, str]:
    return {AUTH_COOKIE_NAME: AUTH_TOKEN}


__all__ = (
    "AUTH_TOKEN",
    "StaticSessionTokens",
    "TEST_CSRF_ALLOWED_ORIGINS",
    "TEST_CSRF_HEADERS",
    "TEST_ORIGIN",
    "auth_cookies",
    "make_session_binder",
)
