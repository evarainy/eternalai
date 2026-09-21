from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app.infra.auth.crypto import (
    HMACSessionToken,
    PrincipalSessionBinder,
    identity_surrogate,
)
from app.ports.auth import (
    Principal,
    PrincipalOrgContext,
    SessionBindingError,
    SessionTokenError,
)


def _principal(label: str = "a") -> Principal:
    return Principal(
        ai_user_id=f"usr_v1_{label}",
        display_name=f"Synthetic {label}",
        roles=("admin",),
        org_ctx=PrincipalOrgContext(),
    )


def test_identity_surrogate_is_stable_normalized_and_non_reversible() -> None:
    synthetic_loginid = "1" * 17 + "x"
    key = bytes(range(32))

    first = identity_surrogate(f" {synthetic_loginid} ", key=key)
    second = identity_surrogate(synthetic_loginid.upper(), key=key)

    assert first == second
    assert first.startswith("usr_v1_")
    assert synthetic_loginid.lower() not in first.lower()


@pytest.mark.parametrize("key", [b"", b"short"])
def test_all_hmac_boundaries_reject_undersized_keys(key: bytes) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        identity_surrogate("synthetic", key=key)
    with pytest.raises(ValueError, match="at least 32 bytes"):
        HMACSessionToken(signing_key=key, ttl_seconds=60)
    with pytest.raises(ValueError, match="at least 32 bytes"):
        PrincipalSessionBinder(binding_key=key)


def test_session_token_round_trip_rejects_tampering_and_expiry() -> None:
    current_time = [1_000.0]
    tokens = HMACSessionToken(
        signing_key=bytes(range(32)),
        ttl_seconds=60,
        clock=lambda: current_time[0],
    )
    token = tokens.issue(_principal())

    assert tokens.verify(token) == _principal()

    version, payload, signature = token.split(".")
    signature_bytes = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    changed = bytearray(signature_bytes)
    changed[0] ^= 0x01
    different = bytes(changed) != signature_bytes
    assert different
    tampered = f"{version}.{payload}.{encode(bytes(changed))}"
    with pytest.raises(SessionTokenError):
        tokens.verify(tampered)

    current_time[0] += 61
    with pytest.raises(SessionTokenError):
        tokens.verify(token)


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def signed_claims(version: str, claims: dict) -> str:
    signed = f"{version}.{encode(json.dumps(claims).encode())}".encode()
    signature = hmac.new(bytes(range(32)), signed, hashlib.sha256).digest()
    return signed.decode() + "." + encode(signature)


def legacy_ticket(principal: Principal, now: int) -> str:
    return signed_claims(
        "v1",
        {
            "v": 1,
            "principal": principal.model_dump(mode="json"),
            "iat": now,
            "exp": now + 3600,
        },
    )


def encoding_alias(token: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alias = token[:-1] + alphabet[alphabet.index(token[-1]) ^ 1]
    differs = alias != token
    equal_bytes = base64.urlsafe_b64decode(alias.split(".")[2] + "=") == base64.urlsafe_b64decode(
        token.split(".")[2] + "="
    )
    assert differs
    assert equal_bytes
    return alias


def test_new_sessions_are_unique_with_same_principal_and_clock() -> None:
    port = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=60, clock=lambda: 1000)
    a, b = port.issue(_principal()), port.issue(_principal())
    different = a != b and port.inspect(a).fingerprint != port.inspect(b).fingerprint
    assert different
    assert port.inspect(a).version == port.inspect(b).version == 2


@pytest.mark.parametrize("version", [1, 2])
def test_signature_encoding_alias_cannot_change_revocation_identity(version: int) -> None:
    port = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=60, clock=lambda: 1000)
    token = legacy_ticket(_principal(), 1000) if version == 1 else port.issue(_principal())
    alias = encoding_alias(token)
    same = port.inspect(token).fingerprint == port.inspect(alias).fingerprint
    assert same
    assert port.verify(token) == port.verify(alias) == _principal()


@pytest.mark.parametrize(
    "case",
    [
        "missing_nonce",
        "short_nonce",
        "alias_nonce",
        "version",
        "future",
        "expired",
        "reversed",
        "extra",
        "bad_time",
    ],
)
def test_inspect_accepts_legacy_and_rejects_invalid_version_nonce_and_time(case: str) -> None:
    port = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=60, clock=lambda: 1000)
    assert port.inspect(legacy_ticket(_principal(), 1000)).version == 1
    token = port.issue(_principal())
    claims = json.loads(
        base64.urlsafe_b64decode(token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4))
    )
    if case == "missing_nonce":
        del claims["nonce"]
    elif case == "short_nonce":
        claims["nonce"] = encode(b"short")
    elif case == "alias_nonce":
        claims["nonce"] += "="
    elif case == "version":
        claims["v"] = 1
    elif case == "future":
        claims["iat"] = 1001
    elif case == "expired":
        claims.update(iat=999, exp=1000)
    elif case == "reversed":
        claims.update(iat=1000, exp=999)
    elif case == "extra":
        claims["extra"] = True
    elif case == "bad_time":
        claims["exp"] = 10**100
    with pytest.raises(SessionTokenError) as error:
        port.inspect(signed_claims("v2", claims))
    assert str(error.value) == "session token is invalid"
    assert error.value.__context__ is None
    assert error.value.__cause__ is None


def test_invalid_ticket_error_has_no_parser_exception_chain() -> None:
    port = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=60)
    for invalid in ("synthetic-invalid", signed_claims("v1", {"principal": "synthetic-invalid"})):
        with pytest.raises(SessionTokenError) as error:
            port.inspect(invalid)
        assert error.value.__context__ is None
        assert error.value.__cause__ is None
        assert str(error.value) == "session token is invalid"


def test_directory_join_key_survives_session_without_mutable_authorization_fields() -> None:
    principal = _principal().model_copy(update={
        "org_ctx": PrincipalOrgContext(directory_user_id="synthetic-directory-user"),
    })
    tokens = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=60)
    restored = tokens.verify(tokens.issue(principal))
    assert restored.org_ctx.directory_user_id == "synthetic-directory-user"
    assert restored.org_ctx.department_id is None
    assert restored.org_ctx.org_id is None
    assert "job_title" not in PrincipalOrgContext.model_fields
    assert "synthetic-directory-user" not in repr(restored)


def test_principal_session_binding_is_continuous_and_cross_user_fail_closed() -> None:
    binder = PrincipalSessionBinder(binding_key=bytes(reversed(range(32))))

    bound_a = binder.bind(_principal("a"), "client-conversation")
    bound_b = binder.bind(_principal("b"), "client-conversation")

    assert bound_a.startswith("sid_v1.")
    assert bound_b.startswith("sid_v1.")
    assert bound_a != bound_b
    assert binder.bind(_principal("a"), bound_a) == bound_a
    with pytest.raises(SessionBindingError):
        binder.bind(_principal("a"), bound_b)
    with pytest.raises(SessionBindingError):
        binder.bind(_principal("a"), "sid_v1.invalid")
