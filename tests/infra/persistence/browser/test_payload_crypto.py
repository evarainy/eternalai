"""Synthetic facts exercise real AEAD without browser or business calls."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.browser_skill.models import BrowserOwner
from app.infra.persistence.browser import payload_crypto
from app.infra.persistence.browser.payload_crypto import (
    BrowserPayloadCipher,
    BrowserRunCryptoIdentity,
)
from app.ports.browser_profile_store import (
    BrowserProfileCaptureFact,
    BrowserProfileError,
)
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    ProtectedRunEnvelope,
    RunAdmission,
    RunSnapshot,
)
from app.ports.credential_vault import BrowserBindingFact

_OLD_KEY = b"o" * 32
_NEW_KEY = b"n" * 32


def _owner() -> BrowserOwner:
    return BrowserOwner(tenant_id="tenant", user_id="user", session_id="sid_v1.synthetic")


def _identity() -> BrowserRunCryptoIdentity:
    return BrowserRunCryptoIdentity(
        owner=_owner(), task_id="task", run_id="run", target_system="oa",
        binding_id="binding", binding_revision=2, auth_fingerprint=b"a" * 32,
        auth_expires_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        publication_digest=b"p" * 32, input_revision=3, input_digest=b"i" * 32,
    )


def _admission(cipher: BrowserPayloadCipher) -> RunAdmission:
    identity = _identity()
    return RunAdmission(
        owner=identity.owner, task_id=identity.task_id, run_id=identity.run_id,
        target_system=identity.target_system, binding_id=identity.binding_id,
        binding_revision=identity.binding_revision, auth_fingerprint=identity.auth_fingerprint,
        auth_expires_at=identity.auth_expires_at,
        publication_digest=identity.publication_digest, input_revision=identity.input_revision,
        input_digest=identity.input_digest,
        protected_input=cipher.encrypt_input(identity, {"operation": "read", "selector": ["a", 1]}),
    )


def _verified_run(
    admission: RunAdmission, result: ProtectedRunEnvelope, *, evidence: bytes = b"e" * 32
) -> RunSnapshot:
    return RunSnapshot(
        admission=admission, state_revision=4, status="running", phase="verifying",
        worker_id="worker", worker_epoch=1, worker_deadline=None,
        provider_key="provider", provider_manifest_digest=b"m" * 32, lease_epoch=2,
        profile_generation_id=None, capture_operation_id=None, capture_status="not_requested",
        cancel_requested=False, cancel_acknowledged=False, effect="acknowledged",
        verification="verified", verification_evidence_digest=evidence,
        cleanup="pending", error_code=None, dispatch_failure_code=None,
        terminal_revision=None, terminal_event_id=None,
        result_digest=b"r" * 32, protected_result=result,
    )


def _profile_fact() -> BrowserProfileCaptureFact:
    return BrowserProfileCaptureFact(
        generation_id="generation", binding_revision=2, profile_revision=3,
        lease_epoch=4, provider_key="provider", capture_operation_id="capture",
        manifest_digest=b"m" * 32, generation_ref_digest=b"g" * 32,
        subject_digest=b"s" * 32, origin_digest=b"o" * 32,
        projection_digest=b"p" * 32, captured_bytes=42,
    )


def test_input_round_trip_binds_full_owner_and_frozen_admission() -> None:
    cipher = BrowserPayloadCipher({"old": _OLD_KEY}, active_key_id="old")
    admission = _admission(cipher)
    assert cipher.decrypt_input(admission) == {"operation": "read", "selector": ["a", 1]}
    assert BrowserRunCryptoIdentity.from_admission(admission) == _identity()
    assert admission.protected_input.ciphertext != b'{"operation":"read"}'
    assert (
        cipher.encrypt_input(_identity(), {"operation": "read"}).nonce
        != admission.protected_input.nonce
    )

    changed_owner = _owner().model_copy(update={"session_id": "sid_v1.other"})
    for changed in (
        replace(admission, owner=changed_owner),
        replace(admission, binding_revision=3),
        replace(admission, publication_digest=b"q" * 32),
        replace(admission, auth_expires_at=admission.auth_expires_at + timedelta(seconds=1)),
        replace(admission, input_digest=b"j" * 32),
    ):
        with pytest.raises(BrowserRunStoreError) as error:
            cipher.decrypt_input(changed)
        assert error.value.code == "browser_run_payload_invalid"


def test_result_purpose_and_proof_are_bound_but_later_capture_state_is_not() -> None:
    cipher = BrowserPayloadCipher({"old": _OLD_KEY}, active_key_id="old")
    admission = _admission(cipher)
    result = cipher.encrypt_result(
        admission, {"status": "verified", "count": 2},
        evidence_digest=b"e" * 32, result_digest=b"r" * 32,
    )
    run = _verified_run(admission, result)
    after_capture = replace(
        run, state_revision=8, profile_generation_id="generation", capture_status="promoted",
    )
    assert cipher.decrypt_result(after_capture) == {"status": "verified", "count": 2}
    for changed in (
        replace(run, verification="incomplete"),
        replace(run, verification_evidence_digest=b"x" * 32),
        replace(run, result_digest=b"x" * 32),
        replace(run, protected_result=admission.protected_input),
        replace(run, protected_result=None),
    ):
        with pytest.raises(BrowserRunStoreError) as error:
            cipher.decrypt_result(changed)
        assert error.value.code == "browser_run_payload_invalid"


def test_profile_binds_owner_binding_generation_proof_and_version() -> None:
    cipher = BrowserPayloadCipher({"old": _OLD_KEY}, active_key_id="old")
    owner = _owner()
    binding = BrowserBindingFact("tenant", "user", "oa", "binding", 2, b"s" * 32)
    fact = _profile_fact()
    generation = cipher.encrypt_profile(owner, binding, fact, {"tabs": ["synthetic"]})
    assert cipher.decrypt_profile(owner, binding, generation) == {"tabs": ["synthetic"]}
    for changed_owner, changed_binding, changed_generation in (
        (owner.model_copy(update={"session_id": "sid_v1.other"}), binding, generation),
        (owner, replace(binding, binding_id="other"), generation),
        (owner, binding, replace(generation, fact=replace(fact, profile_revision=4))),
        (owner, binding, replace(generation, fact=replace(fact, manifest_digest=b"x" * 32))),
        (owner, binding, replace(generation, fact=replace(fact, lease_epoch=5))),
        (owner, binding, replace(generation, fact=replace(fact, subject_digest=b"x" * 32))),
    ):
        with pytest.raises(BrowserProfileError) as error:
            cipher.decrypt_profile(changed_owner, changed_binding, changed_generation)
        assert error.value.code == "browser_profile_payload_invalid"


def test_retained_historical_key_decrypts_and_envelope_tampering_fails() -> None:
    old = BrowserPayloadCipher({"old": _OLD_KEY}, active_key_id="old")
    admission = _admission(old)
    rotated = BrowserPayloadCipher({"old": _OLD_KEY, "new": _NEW_KEY}, active_key_id="new")
    assert rotated.decrypt_input(admission)["operation"] == "read"
    assert rotated.encrypt_input(_identity(), {"operation": "read"}).key_id == "new"
    for envelope in (
        replace(admission.protected_input, key_id="new"),
        replace(admission.protected_input, nonce=b"z" * 12),
        replace(admission.protected_input, ciphertext=admission.protected_input.ciphertext[:-1]
                + bytes([admission.protected_input.ciphertext[-1] ^ 1])),
    ):
        with pytest.raises(BrowserRunStoreError) as error:
            rotated.decrypt_input(replace(admission, protected_input=envelope))
        assert error.value.code == "browser_run_payload_invalid"
    with pytest.raises(BrowserRunStoreError) as error:
        BrowserPayloadCipher({"new": _NEW_KEY}, active_key_id="new").decrypt_input(admission)
    assert error.value.code == "browser_run_payload_invalid"


def test_keyring_json_limits_and_duplicated_header_fail_closed() -> None:
    with pytest.raises(ValueError, match="browser_payload_keyring_invalid"):
        BrowserPayloadCipher({"bad": b"short"}, active_key_id="bad")
    cipher = BrowserPayloadCipher({"old": _OLD_KEY}, active_key_id="old")
    assert _OLD_KEY.hex() not in repr(cipher)
    identity = _identity()
    for invalid in (["not-an-object"], {"number": float("nan")}, {"huge": "x" * 1_048_576}):
        with pytest.raises(BrowserRunStoreError) as error:
            cipher.encrypt_input(identity, invalid)  # type: ignore[arg-type]
        assert error.value.code == "browser_run_payload_invalid"
        assert "not-an-object" not in str(error.value)

    admission = _admission(cipher)
    envelope = admission.protected_input
    aad = payload_crypto._canonical(payload_crypto._run_header(
        identity, cipher_version=envelope.cipher_version, key_id=envelope.key_id,
        purpose="run-input",
    ))
    plaintext = AESGCM(_OLD_KEY).decrypt(envelope.nonce, envelope.ciphertext, aad)
    payload = json.loads(plaintext)
    payload["header"][7] = "other-task"
    forged_plaintext = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    nonce = b"f" * 12
    forged = replace(
        envelope, nonce=nonce,
        ciphertext=AESGCM(_OLD_KEY).encrypt(nonce, forged_plaintext, aad),
    )
    with pytest.raises(BrowserRunStoreError) as error:
        cipher.decrypt_input(replace(admission, protected_input=forged))
    assert error.value.code == "browser_run_payload_invalid"
