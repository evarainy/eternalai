"""Authenticated identity, token, and conversation-session cryptography."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.ports.auth import Principal, SessionBindingError, SessionTokenError

_MIN_HMAC_KEY_BYTES = 32
_BOUND_SESSION_RE = re.compile(
    r"^sid_v1\.(?P<payload>[A-Za-z0-9_-]{43})\.(?P<signature>[A-Za-z0-9_-]{43})$"
)
_MAX_CLIENT_SESSION_ID_LENGTH = 512


def require_hmac_key(key: bytes, *, purpose: str) -> bytes:
    """Reject absent or undersized HMAC keys without exposing key material."""

    if not isinstance(key, bytes) or len(key) < _MIN_HMAC_KEY_BYTES:
        raise ValueError(f"{purpose} key must contain at least 32 bytes")
    return key


def identity_surrogate(loginid: str, *, key: bytes) -> str:
    """Return a stable non-reversible identifier for a normalized OA login ID."""

    validated_key = require_hmac_key(key, purpose="identity surrogate")
    normalized = loginid.strip().upper()
    if not normalized:
        raise ValueError("loginid must not be blank")
    digest = hmac.new(validated_key, normalized.encode("utf-8"), hashlib.sha256).digest()
    return f"usr_v1_{_base64url_encode(digest)}"


class _TokenClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int
    principal: Principal
    iat: int
    exp: int


class HMACSessionToken:
    """Compact server-issued token bound to a validated Principal."""

    def __init__(
        self,
        *,
        signing_key: bytes,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._signing_key = require_hmac_key(signing_key, purpose="session token")
        if ttl_seconds <= 0:
            raise ValueError("session token TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def issue(self, principal: Principal) -> str:
        issued_at = int(self._clock())
        claims = _TokenClaims(
            v=1,
            principal=principal,
            iat=issued_at,
            exp=issued_at + self._ttl_seconds,
        )
        payload = json.dumps(
            claims.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded_payload = _base64url_encode(payload)
        signed = f"v1.{encoded_payload}".encode("ascii")
        signature = hmac.new(self._signing_key, signed, hashlib.sha256).digest()
        return f"v1.{encoded_payload}.{_base64url_encode(signature)}"

    def verify(self, token: str) -> Principal:
        try:
            version, encoded_payload, encoded_signature = token.split(".")
            if version != "v1":
                raise ValueError
            signed = f"{version}.{encoded_payload}".encode("ascii")
            supplied_signature = _base64url_decode(encoded_signature)
            expected_signature = hmac.new(
                self._signing_key,
                signed,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError
            payload = json.loads(_base64url_decode(encoded_payload))
            claims = _TokenClaims.model_validate(payload)
            now = int(self._clock())
            if claims.v != 1 or claims.iat > now or claims.exp <= now:
                raise ValueError
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionTokenError("session token is invalid") from exc
        return claims.principal


class PrincipalSessionBinder:
    """Convert an untrusted client conversation ID into a Principal-bound ID."""

    def __init__(self, *, binding_key: bytes) -> None:
        self._binding_key = require_hmac_key(binding_key, purpose="session binding")

    def bind(self, principal: Principal, client_session_id: str) -> str:
        if (
            not client_session_id
            or len(client_session_id) > _MAX_CLIENT_SESSION_ID_LENGTH
            or client_session_id != client_session_id.strip()
        ):
            raise SessionBindingError("session identifier is invalid")

        if client_session_id.startswith("sid_v1."):
            return self._verify_bound(principal, client_session_id)

        payload_digest = hmac.new(
            self._binding_key,
            b"client-session-v1\x00" + client_session_id.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        payload = _base64url_encode(payload_digest)
        signature = self._signature(principal.ai_user_id, payload)
        return f"sid_v1.{payload}.{signature}"

    def _verify_bound(self, principal: Principal, bound_session_id: str) -> str:
        match = _BOUND_SESSION_RE.fullmatch(bound_session_id)
        if match is None:
            raise SessionBindingError("session identifier is invalid")
        payload = match.group("payload")
        supplied_signature = match.group("signature")
        expected_signature = self._signature(principal.ai_user_id, payload)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise SessionBindingError("session identifier is invalid")
        return bound_session_id

    def _signature(self, ai_user_id: str, payload: str) -> str:
        digest = hmac.new(
            self._binding_key,
            b"principal-session-v1\x00"
            + ai_user_id.encode("utf-8")
            + b"\x00"
            + payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return _base64url_encode(digest)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def ensure_json_object(value: Any) -> dict[str, Any]:
    """Return a JSON object or reject the upstream payload."""

    if not isinstance(value, dict):
        raise ValueError("JSON payload must be an object")
    return {str(key): item for key, item in value.items()}


__all__ = (
    "HMACSessionToken",
    "PrincipalSessionBinder",
    "SessionBindingError",
    "ensure_json_object",
    "identity_surrogate",
    "require_hmac_key",
)
