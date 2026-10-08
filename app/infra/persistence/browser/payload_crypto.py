"""Purpose-separated AEAD for private browser Run and profile payloads.

The caller supplies a durable keyring. Retained key IDs decrypt old envelopes;
rotation or acquisition of those keys is deliberately outside this adapter.
Only trusted callers may pass decrypted values to provider or verifier code.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NoReturn

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.browser_skill.models import BrowserOwner
from app.ports.browser_profile_store import (
    BrowserProfileCaptureFact,
    BrowserProfileError,
    BrowserProfileGeneration,
)
from app.ports.browser_run_store import (
    MAX_RUN_REVISION,
    BrowserRunStoreError,
    ProtectedRunEnvelope,
    RunAdmission,
    RunSnapshot,
)
from app.ports.credential_vault import BrowserBindingFact

INPUT_VERSION = "aes256gcm-browser-input-v1"
RESULT_VERSION = "aes256gcm-browser-result-v1"
PROFILE_VERSION = "aes256gcm-browser-profile-v1"
_AAD_DOMAIN = "browser-payload-aad-v1"
_MAX_PLAINTEXT_BYTES = 1_048_576
_MAX_CIPHERTEXT_BYTES = _MAX_PLAINTEXT_BYTES + 16
_MAX_JSON_DEPTH = 32
_MAX_JSON_INTEGER = 9_007_199_254_740_991
_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")


def _run_refused() -> NoReturn:
    raise BrowserRunStoreError("browser_run_payload_invalid")


def _profile_refused() -> NoReturn:
    raise BrowserProfileError("browser_profile_payload_invalid")


def _valid_id(value: object) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _valid_digest(value: object) -> bool:
    return type(value) is bytes and len(value) == 32


def _valid_owner(owner: object) -> bool:
    return (
        isinstance(owner, BrowserOwner)
        and _valid_id(owner.tenant_id)
        and _valid_id(owner.user_id)
        and type(owner.session_id) is str
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,192}", owner.session_id) is not None
    )


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid time")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _valid_string(value: str) -> bool:
    return not any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def _json_value(value: object, *, depth: int = 0, seen: set[int] | None = None) -> Any:
    """Copy only bounded JSON values; never invoke custom JSON encoders or repr."""
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("JSON nesting too deep")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if not _valid_string(value):
            raise ValueError("invalid JSON string")
        return value
    if type(value) is int:
        if not -_MAX_JSON_INTEGER <= value <= _MAX_JSON_INTEGER:
            raise ValueError("JSON integer out of range")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
        return value
    if type(value) is list or isinstance(value, Mapping):
        if seen is None:
            seen = set()
        identity = id(value)
        if identity in seen:
            raise ValueError("cyclic JSON value")
        seen.add(identity)
        try:
            if type(value) is list:
                return [_json_value(item, depth=depth + 1, seen=seen) for item in value]
            if not isinstance(value, Mapping):
                raise ValueError("non-JSON value")
            result: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str or not _valid_string(key):
                    raise ValueError("invalid JSON object key")
                result[key] = _json_value(item, depth=depth + 1, seen=seen)
            return result
        finally:
            seen.remove(identity)
    raise ValueError("non-JSON value")


def _encode(header: list[object], value: Mapping[str, object]) -> bytes:
    if not isinstance(value, Mapping):
        raise ValueError("payload must be an object")
    payload = _canonical({"header": header, "value": _json_value(value)})
    if len(payload) > _MAX_PLAINTEXT_BYTES:
        raise ValueError("payload too large")
    return payload


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("invalid JSON constant")


def _decode(header: list[object], plaintext: bytes) -> dict[str, Any]:
    if len(plaintext) > _MAX_PLAINTEXT_BYTES:
        raise ValueError("payload too large")
    decoded = json.loads(plaintext.decode("ascii"), parse_constant=_reject_constant)
    if type(decoded) is not dict or set(decoded) != {"header", "value"}:
        raise ValueError("invalid payload shape")
    if type(decoded["header"]) is not list or decoded["header"] != header:
        raise ValueError("payload header mismatch")
    value = decoded["value"]
    if type(value) is not dict:
        raise ValueError("payload must be an object")
    if _canonical({"header": header, "value": _json_value(value)}) != plaintext:
        raise ValueError("noncanonical payload")
    return value


@dataclass(frozen=True, slots=True)
class BrowserRunCryptoIdentity:
    """Immutable admission facts, captured before an input envelope is minted."""

    owner: BrowserOwner = field(repr=False)
    task_id: str = field(repr=False)
    run_id: str = field(repr=False)
    target_system: str = field(repr=False)
    binding_id: str = field(repr=False)
    binding_revision: int = field(repr=False)
    auth_fingerprint: bytes = field(repr=False)
    auth_expires_at: datetime = field(repr=False)
    publication_digest: bytes = field(repr=False)
    input_revision: int = field(repr=False)
    input_digest: bytes = field(repr=False)
    auth_evidence_version: str = field(default="verified-session-v1", repr=False)

    def __post_init__(self) -> None:
        try:
            if (
                not _valid_owner(self.owner)
                or not all(_valid_id(v) for v in (self.task_id, self.run_id, self.binding_id))
                or self.target_system not in {"oa", "u8", "hikvision_ivms"}
                or any(
                    type(v) is not int or not 1 <= v <= MAX_RUN_REVISION
                    for v in (self.binding_revision, self.input_revision)
                )
                or not all(
                    _valid_digest(v)
                    for v in (self.auth_fingerprint, self.publication_digest, self.input_digest)
                )
                or self.auth_evidence_version != "verified-session-v1"
            ):
                _run_refused()
            _utc_iso(self.auth_expires_at)
        except Exception:
            _run_refused()

    @classmethod
    def from_admission(cls, admission: RunAdmission) -> BrowserRunCryptoIdentity:
        try:
            if (
                not isinstance(admission, RunAdmission)
                or not isinstance(admission.protected_input, ProtectedRunEnvelope)
                or admission.protected_input.cipher_version != INPUT_VERSION
            ):
                _run_refused()
            return cls(
                owner=admission.owner,
                task_id=admission.task_id,
                run_id=admission.run_id,
                target_system=admission.target_system,
                binding_id=admission.binding_id,
                binding_revision=admission.binding_revision,
                auth_fingerprint=admission.auth_fingerprint,
                auth_expires_at=admission.auth_expires_at,
                publication_digest=admission.publication_digest,
                input_revision=admission.input_revision,
                input_digest=admission.input_digest,
                auth_evidence_version=admission.auth_evidence_version,
            )
        except Exception:
            _run_refused()


def _run_header(
    identity: BrowserRunCryptoIdentity,
    *,
    cipher_version: str,
    key_id: str,
    purpose: str,
    evidence_digest: bytes | None = None,
    result_digest: bytes | None = None,
) -> list[object]:
    # Fixed positions: domain/version/key/purpose, full owner, Run/task/binding,
    # verified auth, frozen publication/input, then purpose-specific proof.
    return [
        _AAD_DOMAIN,
        cipher_version,
        key_id,
        purpose,
        identity.owner.tenant_id,
        identity.owner.user_id,
        identity.owner.session_id,
        identity.task_id,
        identity.run_id,
        identity.target_system,
        identity.binding_id,
        identity.binding_revision,
        identity.auth_evidence_version,
        identity.auth_fingerprint.hex(),
        _utc_iso(identity.auth_expires_at),
        identity.publication_digest.hex(),
        identity.input_revision,
        identity.input_digest.hex(),
        evidence_digest.hex() if evidence_digest is not None else None,
        result_digest.hex() if result_digest is not None else None,
    ]


def _profile_header(
    owner: BrowserOwner,
    binding: BrowserBindingFact,
    fact: BrowserProfileCaptureFact,
    *,
    key_id: str,
) -> list[object]:
    # The owner/binding and capture fact are checked again at every decrypt.
    if (
        not _valid_owner(owner)
        or not isinstance(binding, BrowserBindingFact)
        or not isinstance(fact, BrowserProfileCaptureFact)
        or binding.tenant_id != owner.tenant_id
        or binding.ai_user_id != owner.user_id
        or binding.target_system not in {"oa", "u8", "hikvision_ivms"}
        or not _valid_id(binding.binding_id)
        or type(binding.binding_revision) is not int
        or not 1 <= binding.binding_revision <= MAX_RUN_REVISION
        or not _valid_digest(binding.subject_digest)
        or any(not _valid_id(v) for v in
               (fact.generation_id, fact.provider_key, fact.capture_operation_id))
        or fact.binding_revision != binding.binding_revision
        or fact.subject_digest != binding.subject_digest
        or any(
            type(v) is not int or not 1 <= v <= MAX_RUN_REVISION
            for v in (fact.binding_revision, fact.profile_revision, fact.lease_epoch)
        )
        or type(fact.captured_bytes) is not int
        or not 0 <= fact.captured_bytes <= MAX_RUN_REVISION
        or any(
            not _valid_digest(v)
            for v in (
                fact.manifest_digest,
                fact.generation_ref_digest,
                fact.subject_digest,
                fact.origin_digest,
                fact.projection_digest,
            )
        )
    ):
        _profile_refused()
    return [
        _AAD_DOMAIN,
        PROFILE_VERSION,
        key_id,
        "profile-generation",
        owner.tenant_id,
        owner.user_id,
        owner.session_id,
        binding.target_system,
        binding.binding_id,
        binding.binding_revision,
        binding.subject_digest.hex(),
        fact.generation_id,
        fact.binding_revision,
        fact.profile_revision,
        fact.lease_epoch,
        fact.provider_key,
        fact.capture_operation_id,
        fact.manifest_digest.hex(),
        fact.generation_ref_digest.hex(),
        fact.subject_digest.hex(),
        fact.origin_digest.hex(),
        fact.projection_digest.hex(),
        fact.captured_bytes,
    ]


class BrowserPayloadCipher:
    """Injected AES-256-GCM keyring with distinct Run/input/result/profile AAD."""

    def __init__(self, keys: Mapping[str, bytes], *, active_key_id: str) -> None:
        try:
            copied = dict(keys)
            if (
                not copied
                or not _valid_id(active_key_id)
                or active_key_id not in copied
                or any(not _valid_id(k) or type(v) is not bytes or len(v) != 32
                       for k, v in copied.items())
            ):
                raise ValueError("invalid keyring")
        except Exception:
            raise ValueError("browser_payload_keyring_invalid") from None
        self._keys = copied
        self._active_key_id = active_key_id

    def __repr__(self) -> str:
        return "BrowserPayloadCipher(<private keyring>)"

    def _seal(self, header: list[object], value: Mapping[str, object]) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keys[self._active_key_id]).encrypt(
            nonce, _encode(header, value), _canonical(header)
        )
        return nonce, ciphertext

    def _open(
        self, header: list[object], key_id: str, nonce: bytes, ciphertext: bytes
    ) -> dict[str, Any]:
        if (
            not _valid_id(key_id)
            or key_id not in self._keys
            or type(nonce) is not bytes
            or len(nonce) != 12
            or type(ciphertext) is not bytes
            or not 16 <= len(ciphertext) <= _MAX_CIPHERTEXT_BYTES
        ):
            raise ValueError("invalid envelope")
        plaintext = AESGCM(self._keys[key_id]).decrypt(nonce, ciphertext, _canonical(header))
        return _decode(header, plaintext)

    def encrypt_input(
        self, identity: BrowserRunCryptoIdentity, value: Mapping[str, object]
    ) -> ProtectedRunEnvelope:
        try:
            if not isinstance(identity, BrowserRunCryptoIdentity):
                _run_refused()
            identity.__post_init__()
            header = _run_header(
                identity, cipher_version=INPUT_VERSION, key_id=self._active_key_id,
                purpose="run-input",
            )
            nonce, ciphertext = self._seal(header, value)
            return ProtectedRunEnvelope(INPUT_VERSION, self._active_key_id, nonce, ciphertext)
        except Exception:
            _run_refused()

    def decrypt_input(self, admission: RunAdmission) -> dict[str, Any]:
        try:
            identity = BrowserRunCryptoIdentity.from_admission(admission)
            envelope = admission.protected_input
            if (
                not isinstance(envelope, ProtectedRunEnvelope)
                or envelope.cipher_version != INPUT_VERSION
            ):
                _run_refused()
            header = _run_header(
                identity, cipher_version=INPUT_VERSION, key_id=envelope.key_id,
                purpose="run-input",
            )
            return self._open(header, envelope.key_id, envelope.nonce, envelope.ciphertext)
        except Exception:
            _run_refused()

    def encrypt_result(
        self,
        admission: RunAdmission,
        value: Mapping[str, object],
        *,
        evidence_digest: bytes,
        result_digest: bytes,
    ) -> ProtectedRunEnvelope:
        try:
            identity = BrowserRunCryptoIdentity.from_admission(admission)
            if not _valid_digest(evidence_digest) or not _valid_digest(result_digest):
                _run_refused()
            header = _run_header(
                identity, cipher_version=RESULT_VERSION, key_id=self._active_key_id,
                purpose="run-result", evidence_digest=evidence_digest, result_digest=result_digest,
            )
            nonce, ciphertext = self._seal(header, value)
            return ProtectedRunEnvelope(RESULT_VERSION, self._active_key_id, nonce, ciphertext)
        except Exception:
            _run_refused()

    def decrypt_result(self, run: RunSnapshot) -> dict[str, Any]:
        try:
            if (
                not isinstance(run, RunSnapshot)
                or run.verification != "verified"
                or not _valid_digest(run.verification_evidence_digest)
                or not _valid_digest(run.result_digest)
                or not isinstance(run.protected_result, ProtectedRunEnvelope)
                or run.protected_result.cipher_version != RESULT_VERSION
            ):
                _run_refused()
            identity = BrowserRunCryptoIdentity.from_admission(run.admission)
            envelope = run.protected_result
            header = _run_header(
                identity, cipher_version=RESULT_VERSION, key_id=envelope.key_id,
                purpose="run-result", evidence_digest=run.verification_evidence_digest,
                result_digest=run.result_digest,
            )
            return self._open(header, envelope.key_id, envelope.nonce, envelope.ciphertext)
        except Exception:
            _run_refused()

    def encrypt_profile(
        self,
        owner: BrowserOwner,
        binding: BrowserBindingFact,
        fact: BrowserProfileCaptureFact,
        value: Mapping[str, object],
    ) -> BrowserProfileGeneration:
        try:
            header = _profile_header(owner, binding, fact, key_id=self._active_key_id)
            nonce, ciphertext = self._seal(header, value)
            return BrowserProfileGeneration(fact, self._active_key_id, nonce, ciphertext)
        except Exception:
            _profile_refused()

    def decrypt_profile(
        self,
        owner: BrowserOwner,
        binding: BrowserBindingFact,
        generation: BrowserProfileGeneration,
    ) -> dict[str, Any]:
        try:
            if (
                not isinstance(generation, BrowserProfileGeneration)
                or generation.cipher_version != PROFILE_VERSION
            ):
                _profile_refused()
            header = _profile_header(owner, binding, generation.fact, key_id=generation.key_id)
            return self._open(header, generation.key_id, generation.nonce, generation.ciphertext)
        except Exception:
            _profile_refused()
