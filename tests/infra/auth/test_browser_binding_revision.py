"""Offline crypto/consumer evidence; PostgreSQL CAS is tested separately on real PG."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.infra.auth.postgresql import (
    _associated_data,
    _decode_credential_row,
    _decode_password_row,
    _password_associated_data,
    _v2_aad,
)
from app.ports.auth import AuthenticationError, CredentialSnapshot, StaleCredentialWrite
from tests.infra.auth.test_oa_credential_verifier import _fixture

SNAPSHOT = CredentialSnapshot("synthetic-tenant", "synthetic-user", "oa", "binding", 1, 2, 3)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "other-tenant"),
        ("ai_user_id", "other-user"),
        ("target_system", "u8"),
        ("binding_id", "other-binding"),
    ],
)
@pytest.mark.parametrize("purpose", ["session", "password"])
def test_v2_ciphertext_rejects_cross_owner_and_binding(field, value, purpose):
    cipher = AESGCM(bytes(range(32)))
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, b"synthetic-payload", _v2_aad(SNAPSHOT, purpose))
    with pytest.raises(InvalidTag):
        cipher.decrypt(nonce, encrypted, _v2_aad(replace(SNAPSHOT, **{field: value}), purpose))


def test_v2_purpose_is_bound_but_mutable_revisions_are_not():
    cipher = AESGCM(bytes(range(32)))
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, b"synthetic-payload", _v2_aad(SNAPSHOT, "password"))
    with pytest.raises(InvalidTag):
        cipher.decrypt(nonce, encrypted, _v2_aad(SNAPSHOT, "session"))
    advanced = replace(
        SNAPSHOT, binding_revision=20, credential_write_revision=41, refresh_epoch=51
    )
    assert cipher.decrypt(nonce, encrypted, _v2_aad(advanced, "password")) == b"synthetic-payload"


@pytest.mark.parametrize("purpose", ["session", "password"])
def test_legacy_decoder_keeps_v1_bytes_and_aad(purpose):
    cipher = AESGCM(bytes(range(32)))
    nonce = os.urandom(12)
    payload = (
        {"oa_user_id": "synthetic-subject", "cookies": {}}
        if purpose == "session"
        else {"login_id": "synthetic-login", "password": "synthetic-password"}
    )
    aad = (
        _associated_data(SNAPSHOT.ai_user_id)
        if purpose == "session"
        else _password_associated_data(SNAPSHOT.ai_user_id, "oa")
    )
    encrypted = cipher.encrypt(nonce, json.dumps(payload).encode(), aad)
    if purpose == "session":
        row = {
            "cipher_version": "aes256gcm-v1",
            "nonce": nonce,
            "encrypted_payload": encrypted,
            "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
        }
        decoded = _decode_credential_row(
            cipher=cipher, ai_user_id=SNAPSHOT.ai_user_id, row=cast(Any, row)
        )
        assert decoded.oa_user_id.get_secret_value() == payload["oa_user_id"]
    else:
        row = {
            "password_cipher_version": "aes256gcm-password-v1",
            "password_nonce": nonce,
            "encrypted_password_payload": encrypted,
        }
        decoded = _decode_password_row(
            cipher=cipher, ai_user_id=SNAPSHOT.ai_user_id, target_system="oa", row=cast(Any, row)
        )
        assert decoded.password.get_secret_value() == payload["password"]
    assert cipher.decrypt(nonce, encrypted, aad) == json.dumps(payload).encode()


def test_rejected_anonymous_login_only_reads_snapshot_and_never_claims():
    verifier, store, _, credential = _fixture(login_succeeds=False)
    store.snapshot = AsyncMock(wraps=store.snapshot)
    store.claim_write = AsyncMock(wraps=store.claim_write)
    with pytest.raises(AuthenticationError, match="authentication failed"):
        asyncio.run(verifier.authenticate(credential))
    store.snapshot.assert_awaited_once()
    store.claim_write.assert_not_awaited()
    assert store.records == []


def test_late_anonymous_login_cannot_overwrite_an_intervening_tombstone():
    verifier, store, _, credential = _fixture(login_succeeds=True)
    store.claim_write = AsyncMock(side_effect=StaleCredentialWrite("credential write fenced"))
    with pytest.raises(StaleCredentialWrite, match="credential write fenced"):
        asyncio.run(verifier.authenticate(credential))
    store.claim_write.assert_awaited_once()
    assert store.records == []


def test_principal_validation_precedes_any_credential_write():
    verifier, store, _, credential = _fixture(login_succeeds=True)

    async def invalid_roles(*args, **kwargs):
        return (object(),)

    verifier._role_reader.list_roles = invalid_roles
    store.claim_write = AsyncMock(wraps=store.claim_write)
    with pytest.raises(AuthenticationError, match="authentication failed"):
        asyncio.run(verifier.authenticate(credential))
    store.claim_write.assert_not_awaited()
    assert store.records == []


def test_v2_envelope_format_and_fixed_key_slot_are_authenticated():
    aad = _v2_aad(SNAPSHOT, "session")
    fields = json.loads(aad)
    assert fields[1:3] == ["aes256gcm-session-v2", "runtime-single-injected-key-v1"]
    cipher = AESGCM(bytes(range(32)))
    nonce = os.urandom(12)
    encrypted = cipher.encrypt(nonce, b"synthetic-payload", aad)
    for index in (1, 2):
        changed = fields.copy()
        changed[index] = "different-envelope-or-key-slot"
        with pytest.raises(InvalidTag):
            cipher.decrypt(nonce, encrypted, json.dumps(changed, separators=(",", ":")).encode())
