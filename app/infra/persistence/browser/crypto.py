"""AEAD for private provider references, bound to an immutable original claim."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import UTC
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.ports.browser_store import BrowserLeaseClaim, BrowserLeaseError

RESOURCE_VERSION = "aes256gcm-browser-resource-v1"


@dataclass(frozen=True, slots=True)
class ResourceEnvelope:
    cipher_version: str
    key_id: str
    nonce: bytes = field(repr=False)
    encrypted_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (self.cipher_version != RESOURCE_VERSION or not self.key_id
                or len(self.nonce) != 12 or len(self.encrypted_payload) < 16):
            raise ValueError("browser_resource_envelope_invalid")


def resource_identity(claim: BrowserLeaseClaim) -> list[str | int]:
    """Never add mutable deadline/revision, state or present-day authority here."""
    owner, binding = claim.auth.owner, claim.binding
    return [owner.tenant_id, owner.user_id, owner.session_id,
            binding.target_system, binding.binding_id, binding.binding_revision,
            claim.lease_epoch, claim.holder_id, claim.auth.authorization_revision,
            claim.auth.fingerprint.hex(), claim.auth.expires_at.astimezone(UTC).isoformat(),
            claim.operation_id, claim.provider_key]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class BrowserResourceCipher:
    def __init__(self, keys: Mapping[str, bytes], *, active_key_id: str) -> None:
        if (active_key_id not in keys or not keys
                or any(not k or not isinstance(v, bytes) or len(v) != 32
                       for k, v in keys.items())):
            raise ValueError("browser_resource_key_invalid")
        self._keys = dict(keys)
        self._active_key_id = active_key_id

    def encrypt(self, claim: BrowserLeaseClaim, resource_ref: bytes) -> ResourceEnvelope:
        if not isinstance(resource_ref, bytes) or not resource_ref:
            raise BrowserLeaseError("browser_resource_invalid")
        nonce = os.urandom(12)
        key_id = self._active_key_id
        aad = _canonical(["browser-resource-ref.v1", RESOURCE_VERSION, key_id,
                          resource_identity(claim)])
        payload = AESGCM(self._keys[key_id]).encrypt(nonce, resource_ref, aad)
        return ResourceEnvelope(RESOURCE_VERSION, key_id, nonce, payload)

    def decrypt(self, claim: BrowserLeaseClaim, envelope: ResourceEnvelope) -> bytes:
        key = self._keys.get(envelope.key_id)
        if key is None:
            raise BrowserLeaseError("browser_resource_key_unavailable")
        aad = _canonical(["browser-resource-ref.v1", envelope.cipher_version, envelope.key_id,
                          resource_identity(claim)])
        try:
            return AESGCM(key).decrypt(envelope.nonce, envelope.encrypted_payload, aad)
        except InvalidTag:
            raise BrowserLeaseError("browser_resource_integrity_failed") from None


class BrowserClaimProofContext:
    """A stable private key binds challenge/manifest to persisted immutable facts.

    This key must survive worker restarts. Rotation requires keeping the old
    context until all its claims are reconciled; changing it fails closed.
    """

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("browser_proof_key_invalid")
        self._key = key

    def challenge(self, claim: BrowserLeaseClaim, manifest_digest: bytes) -> bytes:
        return self.digest("provider-challenge.v1", _canonical(
            [resource_identity(claim), manifest_digest.hex()]))

    def digest(self, purpose: str, value: bytes) -> bytes:
        return hmac.new(self._key, _canonical([purpose, value.hex()]), hashlib.sha256).digest()
