from __future__ import annotations

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
    replacement = "A" if signature[-1] != "A" else "B"
    tampered = f"{version}.{payload}.{signature[:-1]}{replacement}"
    with pytest.raises(SessionTokenError):
        tokens.verify(tampered)

    current_time[0] += 61
    with pytest.raises(SessionTokenError):
        tokens.verify(token)


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
