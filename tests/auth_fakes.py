"""Deterministic authenticated-principal fakes for API tests."""

from __future__ import annotations

from collections.abc import Callable

from app.infra.auth.crypto import PrincipalSessionBinder
from app.ports.auth import (
    Principal,
    PrincipalOrgContext,
    SessionTokenError,
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
            org_ctx=PrincipalOrgContext(),
        )

    def issue(self, principal: Principal) -> str:
        self.principal = principal
        return AUTH_TOKEN

    def verify(self, token: str) -> Principal:
        if token != AUTH_TOKEN:
            raise SessionTokenError("session token is invalid")
        return self.principal


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
