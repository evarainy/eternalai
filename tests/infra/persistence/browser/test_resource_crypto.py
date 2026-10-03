"""Synthetic references only; cryptographic separation is exercised with actual AESGCM."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.browser_skill.models import BrowserOwner, ScopeBinding
from app.browser_skill.trajectory import BindingRevisions
from app.infra.persistence.browser.crypto import (
    BrowserClaimProofContext,
    BrowserResourceCipher,
    ResourceEnvelope,
)
from app.ports.browser_store import BrowserLeaseClaim, BrowserLeaseError
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact


def claim() -> BrowserLeaseClaim:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    owner = BrowserOwner(tenant_id="tenant", user_id="user", session_id="sid_v1.synthetic")
    auth = BrowserAuthFact(owner, 7, b"a" * 32, now + timedelta(hours=1))
    binding = BrowserBindingFact("tenant", "user", "oa", "binding", 2, b"b" * 32)
    return BrowserLeaseClaim(auth, binding, 1, 1, "holder", "operation", "pool", now)


def test_renew_and_cleanup_decrypt_original_immutable_aad() -> None:
    cipher = BrowserResourceCipher({"synthetic": b"k" * 32}, active_key_id="synthetic")
    original = claim()
    envelope = cipher.encrypt(original, b"synthetic-resource")
    renewed = replace(original, lease_revision=4, deadline=original.deadline + timedelta(minutes=2))
    assert cipher.decrypt(renewed, envelope) == b"synthetic-resource"
    assert cipher.decrypt(original, envelope) == b"synthetic-resource"
    assert envelope.encrypted_payload != b"synthetic-resource"
    assert cipher.encrypt(original, b"synthetic-resource").nonce != envelope.nonce


@pytest.mark.parametrize("field", ["lease_epoch", "holder_id", "operation_id", "provider_key",
                                  "tenant", "user", "session", "binding", "binding_revision",
                                  "auth_revision", "fingerprint", "target"])
def test_every_immutable_identity_dimension_rejects_ciphertext_swap(field: str) -> None:
    cipher = BrowserResourceCipher({"synthetic": b"k" * 32}, active_key_id="synthetic")
    original = claim()
    envelope = cipher.encrypt(original, b"synthetic-resource")
    changed = original
    if field in {"lease_epoch", "holder_id", "operation_id", "provider_key"}:
        changed = replace(original, **{field: 2 if field == "lease_epoch" else "different"})
    elif field in {"tenant", "user", "session"}:
        owner = original.auth.owner.model_copy(update={
            {"tenant": "tenant_id", "user": "user_id", "session": "session_id"}[field]: "other"})
        binding = original.binding
        if field != "session":
            binding = replace(binding, **{{"tenant": "tenant_id", "user": "ai_user_id"}[field]:
                                          "other"})
        changed = replace(original, auth=replace(original.auth, owner=owner), binding=binding)
    elif field in {"binding", "binding_revision", "target"}:
        values = {"binding": ("binding_id", "other"), "binding_revision": ("binding_revision", 3),
                  "target": ("target_system", "u8")}
        key, value = values[field]
        changed = replace(original, binding=replace(original.binding, **{key: value}))
    else:
        changed = replace(original, auth=replace(original.auth, **(
            {"authorization_revision": 8} if field == "auth_revision"
            else {"fingerprint": b"z" * 32})))
    with pytest.raises(BrowserLeaseError) as error:
        cipher.decrypt(changed, envelope)
    assert error.value.code == "browser_resource_integrity_failed"


def test_manifest_challenge_and_key_context_are_separated_and_stable_on_renew() -> None:
    context = BrowserClaimProofContext(b"p" * 32)
    original = claim()
    renewed = replace(original, lease_revision=2, deadline=original.deadline + timedelta(seconds=5))
    challenge = context.challenge(original, b"m" * 32)
    assert context.challenge(renewed, b"m" * 32) == challenge
    assert context.challenge(original, b"n" * 32) != challenge
    assert BrowserClaimProofContext(b"q" * 32).challenge(original, b"m" * 32) != challenge
    assert context.digest("release-proof.v1", challenge) != challenge


def test_envelope_coherent_version_nonce_and_tag_lengths() -> None:
    with pytest.raises(ValueError, match="browser_resource_envelope_invalid"):
        ResourceEnvelope("unexpected", "synthetic", b"n" * 12, b"c" * 16)
    with pytest.raises(ValueError, match="browser_resource_envelope_invalid"):
        ResourceEnvelope("aes256gcm-browser-resource-v1", "synthetic", b"n", b"c" * 16)


def test_verified_session_mode_binds_run_and_cannot_decrypt_legacy_resource() -> None:
    original = claim()
    auth = replace(
        original.auth, authorization_revision=None,
        authorization_run_id="run_one", evidence_version="verified-session-v1",
    )
    evidence_claim = replace(original, auth=auth)
    cipher = BrowserResourceCipher({"synthetic": b"k" * 32}, active_key_id="synthetic")
    envelope = cipher.encrypt(evidence_claim, b"synthetic-resource")
    assert cipher.decrypt(evidence_claim, envelope) == b"synthetic-resource"
    for changed in (
        original,
        replace(evidence_claim, auth=replace(auth, authorization_run_id="run_two")),
    ):
        with pytest.raises(BrowserLeaseError) as error:
            cipher.decrypt(changed, envelope)
        assert error.value.code == "browser_resource_integrity_failed"
    proof = BrowserClaimProofContext(b"p" * 32)
    assert proof.challenge(evidence_claim, b"m" * 32) != proof.challenge(original, b"m" * 32)


@pytest.mark.parametrize("revision,run_id,version", [
    (None, None, None), (7, "run_one", "verified-session-v1"),
    (None, "run_one", None), (7, None, "verified-session-v1"),
])
def test_authorization_mode_xor_is_enforced_at_each_contract(
    revision: int | None, run_id: str | None, version: str | None,
) -> None:
    original = claim()
    with pytest.raises(ValueError, match="browser_authorization_mode_invalid"):
        replace(original.auth, authorization_revision=revision,
                authorization_run_id=run_id, evidence_version=version)
    fields = dict(
        binding_id="binding", binding_revision=2, lease_epoch=1,
        authorization_revision=revision, authorization_run_id=run_id, evidence_version=version,
    )
    with pytest.raises(ValueError, match="browser_authorization_mode_invalid"):
        ScopeBinding(owner=original.auth.owner, **fields)
    with pytest.raises(ValueError, match="browser_authorization_mode_invalid"):
        BindingRevisions(**fields)
