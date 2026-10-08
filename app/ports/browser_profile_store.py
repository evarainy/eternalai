"""Private profile history contracts; envelopes and evidence never confer authority.

Callers supply authenticated principals and exact durable Run/lease identities.
Injected verifiers read authoritative provider facts, never accept caller flags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.ports.auth import Principal
from app.ports.browser_store import MAX_BROWSER_REVISION, BrowserLeaseClaim


class BrowserProfileError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("browser profile operation refused")
        self.code = code


def _id(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is not None


@dataclass(frozen=True, slots=True)
class BrowserProfileContext:
    """Principal comes from verified admission/protected Run, never request JSON."""

    principal: Principal = field(repr=False)
    claim: BrowserLeaseClaim = field(repr=False)
    task_id: str
    run_id: str
    worker_epoch: int

    def __post_init__(self) -> None:
        if (
            not _id(self.run_id)
            or not self.task_id.strip()
            or type(self.worker_epoch) is not int
            or not 1 <= self.worker_epoch <= MAX_BROWSER_REVISION
        ):
            raise ValueError("browser_profile_context_invalid")


@dataclass(frozen=True, slots=True)
class BrowserProfileCaptureFact:
    """Verifier-proven fresh generation identity and allowlisted capture metadata.

    verify_capture must bind ALL fields to the exact context owner and live
    resource, proving this provider operation allocated a fresh generation.
    Uniqueness in storage is an additional replay barrier, not that proof.
    """

    generation_id: str
    binding_revision: int
    profile_revision: int
    lease_epoch: int
    provider_key: str
    capture_operation_id: str
    manifest_digest: bytes = field(repr=False)
    generation_ref_digest: bytes = field(repr=False)
    subject_digest: bytes = field(repr=False)
    origin_digest: bytes = field(repr=False)
    projection_digest: bytes = field(repr=False)
    captured_bytes: int

    def __post_init__(self) -> None:
        if (
            any(
                not _id(v)
                for v in (self.generation_id, self.provider_key, self.capture_operation_id)
            )
            or any(
                type(v) is not int or not 1 <= v <= MAX_BROWSER_REVISION
                for v in (self.binding_revision, self.profile_revision, self.lease_epoch)
            )
            or any(
                type(v) is not bytes or len(v) != 32
                for v in (
                    self.manifest_digest,
                    self.generation_ref_digest,
                    self.subject_digest,
                    self.origin_digest,
                    self.projection_digest,
                )
            )
            or type(self.captured_bytes) is not int
            or not 0 <= self.captured_bytes <= MAX_BROWSER_REVISION
        ):
            raise ValueError("browser_profile_capture_invalid")


@dataclass(frozen=True, slots=True)
class BrowserProfileGeneration:
    """Opaque caller-encrypted envelope; AAD binds owner/purpose/fact metadata."""

    fact: BrowserProfileCaptureFact
    key_id: str = field(repr=False)
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)
    cipher_version: Literal["aes256gcm-browser-profile-v1"] = "aes256gcm-browser-profile-v1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.fact, BrowserProfileCaptureFact)
            or not self.key_id.strip()
            or type(self.nonce) is not bytes
            or len(self.nonce) != 12
            or type(self.ciphertext) is not bytes
            or len(self.ciphertext) < 16
            or self.cipher_version != "aes256gcm-browser-profile-v1"
        ):
            raise ValueError("browser_profile_envelope_invalid")


@dataclass(frozen=True, slots=True)
class BrowserProfileHead:
    revision: int
    active: BrowserProfileGeneration | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class BrowserProfileCleanupFact:
    provider_key: str
    capture_operation_id: str
    generation_id: str
    generation_ref_digest: bytes = field(repr=False)
    proof_digest: bytes = field(repr=False)


class BrowserProfileSessionBinder(Protocol):
    def bind(self, principal: Principal, client_session_id: str) -> str:
        """Trusted PrincipalSessionBinder implementation; verifies signed sid_v1."""
        ...


class BrowserProfileCaptureProofPort(Protocol):
    async def verify_capture(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
        evidence: bytes,
    ) -> BrowserProfileCaptureFact:
        """Provider IO outside transaction: authoritative operation lookup + SUBJECT."""
        ...

    async def check_live_subject(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
    ) -> None:
        """Independently verify exact live resource SUBJECT before restore/promotion."""
        ...


class BrowserProfileCleanupProofPort(Protocol):
    async def verify_cleanup(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
        evidence: bytes,
    ) -> BrowserProfileCleanupFact:
        """Prove provider destroyed this exact historical generation, not disconnect."""
        ...


class BrowserProfileCleanupAuthorityPort(Protocol):
    async def check_cleanup(self, context: BrowserProfileContext) -> None:
        """Bounded trusted local/DB authorization for recovery; no provider IO."""
        ...


class BrowserProfileStorePort(Protocol):
    async def load_active(self, context: BrowserProfileContext) -> BrowserProfileHead: ...

    async def record_validated(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
        evidence: bytes,
        *,
        expected_profile_revision: int,
    ) -> None: ...

    async def promote(
        self,
        context: BrowserProfileContext,
        generation_id: str,
        *,
        expected_profile_revision: int,
    ) -> BrowserProfileHead: ...

    async def invalidate(
        self,
        context: BrowserProfileContext,
        *,
        expected_profile_revision: int,
    ) -> BrowserProfileHead: ...

    async def mark_orphan(self, context: BrowserProfileContext, generation_id: str) -> None: ...

    async def cleanup(
        self,
        context: BrowserProfileContext,
        generation_id: str,
        evidence: bytes,
    ) -> None: ...
