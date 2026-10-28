"""Authenticated identity, token, and conversation-session cryptography."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.ports.auth import (
    Principal,
    SessionBindingError,
    SessionTokenError,
    VerifiedSessionToken,
)

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
    model_config = ConfigDict(extra="forbid", strict=True)

    v: int
    principal: Principal
    iat: int
    exp: int


class _TokenClaimsV2(_TokenClaims):
    nonce: str


class _TokenClaimsV3(_TokenClaims):
    sid: str
    auth_time: int
    abs_exp: int | None


_AnyClaims = _TokenClaims | _TokenClaimsV2 | _TokenClaimsV3


class HMACSessionToken:
    """Compact server-issued token bound to a validated Principal.

    Only v3 is issued: one login instance (``sid``) keeps a stable fingerprint
    across renewals, ``exp`` is the idle deadline and ``abs_exp`` an optional
    absolute instance deadline. v1/v2 stay verifiable and are never renewed.
    """

    def __init__(
        self,
        *,
        signing_key: bytes,
        ttl_seconds: int,
        tenant_id: str,
        absolute_ttl_seconds: int | None = None,
        renew_min_interval_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._signing_key = require_hmac_key(signing_key, purpose="session token")
        if ttl_seconds <= 0:
            raise ValueError("session token TTL must be positive")
        if absolute_ttl_seconds is not None and absolute_ttl_seconds <= ttl_seconds:
            raise ValueError("session absolute TTL must exceed the idle TTL")
        if renew_min_interval_seconds <= 0:
            raise ValueError("session renew interval must be positive")
        if not tenant_id.strip():
            raise ValueError("source profile tenant must not be blank")
        self._tenant_id = tenant_id
        self._ttl_seconds = ttl_seconds
        self._absolute_ttl_seconds = absolute_ttl_seconds
        self._renew_min_interval_seconds = renew_min_interval_seconds
        self._clock = clock

    def issue(self, principal: Principal) -> str:
        if principal.org_ctx.tenant_id != self._tenant_id:
            raise SessionTokenError("session token is invalid")
        issued_at = int(self._clock())
        absolute = (
            None
            if self._absolute_ttl_seconds is None
            else issued_at + self._absolute_ttl_seconds
        )
        idle = issued_at + self._ttl_seconds
        claims = _TokenClaimsV3(
            v=3,
            principal=principal,
            iat=issued_at,
            exp=idle if absolute is None else min(idle, absolute),
            sid=_base64url_encode(secrets.token_bytes(32)),
            auth_time=issued_at,
            abs_exp=absolute,
        )
        return self._sign_parts(claims)[0]

    def verify(self, token: str) -> Principal:
        return self.inspect(token).principal

    def inspect(self, token: str) -> VerifiedSessionToken:
        # Evaluate the injected clock outside the untrusted-input exception boundary.
        now = int(self._clock())
        return self._metadata(*self._decode(token, now, allow_expired=False))

    def inspect_for_revocation(self, token: str) -> VerifiedSessionToken | None:
        now = int(self._clock())
        claims, signed = self._decode(token, now, allow_expired=True)
        if claims.exp <= now and not isinstance(claims, _TokenClaimsV3):
            return None
        return self._metadata(claims, signed)

    def renew(
        self, token: str
    ) -> tuple[str, VerifiedSessionToken] | Literal["legacy", "throttled", "capped"]:
        now = int(self._clock())
        claims, _signed = self._decode(token, now, allow_expired=False)
        if not isinstance(claims, _TokenClaimsV3):
            return "legacy"
        if now - claims.iat < self._renew_min_interval_seconds:
            return "throttled"
        cap = claims.abs_exp
        if self._absolute_ttl_seconds is not None:
            # A cap enabled after issue bounds exp only; abs_exp is never rewritten.
            configured = claims.auth_time + self._absolute_ttl_seconds
            cap = configured if cap is None else min(cap, configured)
        idle = now + self._ttl_seconds
        expires = idle if cap is None else min(idle, cap)
        if expires <= claims.exp:
            return "capped"
        renewed = claims.model_copy(update={"iat": now, "exp": expires})
        renewed_token, signed = self._sign_parts(renewed)
        return renewed_token, self._metadata(renewed, signed)

    def _sign_parts(self, claims: _TokenClaimsV3) -> tuple[str, bytes]:
        payload = json.dumps(
            claims.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signed = f"v3.{_base64url_encode(payload)}".encode("ascii")
        signature = hmac.new(self._signing_key, signed, hashlib.sha256).digest()
        return f"{signed.decode('ascii')}.{_base64url_encode(signature)}", signed

    def _decode(
        self, token: str, now: int, *, allow_expired: bool
    ) -> tuple[_AnyClaims, bytes]:
        try:
            version, encoded_payload, encoded_signature = token.split(".")
            if version not in {"v1", "v2", "v3"}:
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
            claims: _AnyClaims = (
                _TokenClaims.model_validate(payload)
                if version == "v1"
                else _TokenClaimsV2.model_validate(payload)
                if version == "v2"
                else _TokenClaimsV3.model_validate(payload)
            )
            if isinstance(claims, _TokenClaimsV2):
                nonce = _base64url_decode(claims.nonce)
                if len(nonce) != 32 or _base64url_encode(nonce) != claims.nonce:
                    raise ValueError
            if isinstance(claims, _TokenClaimsV3):
                sid = _base64url_decode(claims.sid)
                if (
                    len(sid) != 32
                    or _base64url_encode(sid) != claims.sid
                    or claims.auth_time > claims.iat
                    or (claims.abs_exp is not None and claims.exp > claims.abs_exp)
                ):
                    raise ValueError
                if claims.abs_exp is not None:
                    datetime.fromtimestamp(claims.abs_exp, tz=UTC)
            if (
                claims.principal.org_ctx.tenant_id != self._tenant_id
                or claims.v != int(version[1:])
                or claims.iat > now
                or (not allow_expired and claims.exp <= now)
                or claims.exp <= claims.iat
            ):
                raise ValueError
            datetime.fromtimestamp(claims.exp, tz=UTC)
        except (TypeError, ValueError, UnicodeError, OverflowError, OSError):
            pass
        else:
            return claims, signed
        raise SessionTokenError("session token is invalid")

    @staticmethod
    def _metadata(claims: _AnyClaims, signed: bytes) -> VerifiedSessionToken:
        expires_at = datetime.fromtimestamp(claims.exp, tz=UTC)
        if isinstance(claims, _TokenClaimsV3):
            return VerifiedSessionToken(
                principal=claims.principal,
                fingerprint=_instance_fingerprint(claims),
                expires_at=expires_at,
                version=3,
                absolute_expires_at=(
                    None
                    if claims.abs_exp is None
                    else datetime.fromtimestamp(claims.abs_exp, tz=UTC)
                ),
            )
        return VerifiedSessionToken(
            principal=claims.principal,
            fingerprint=hashlib.sha256(b"eternalai-auth-revocation-v1\x00" + signed).digest(),
            expires_at=expires_at,
            version=1 if claims.v == 1 else 2,
        )


def _instance_fingerprint(claims: _TokenClaimsV3) -> bytes:
    """Stable across every renewal of one login instance; the revocation key."""

    return hashlib.sha256(
        b"eternalai-auth-instance-v1\x00"
        + claims.principal.org_ctx.tenant_id.encode("utf-8")
        + b"\x00"
        + claims.principal.ai_user_id.encode("utf-8")
        + b"\x00"
        + _base64url_decode(claims.sid)
    ).digest()


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

    def restore_scope(self, principal: Principal) -> str:
        """Opaque tenant + user + roles alias keying the browser re-login snapshot."""

        canonical = json.dumps(
            [
                "eternalai-reauth-scope-v1",
                principal.org_ctx.tenant_id,
                principal.ai_user_id,
                sorted(principal.roles),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(self._binding_key, canonical, hashlib.sha256).hexdigest()

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
