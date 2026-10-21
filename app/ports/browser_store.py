"""Private persistent browser claims; these facts do not themselves grant authority."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact

MAX_BROWSER_REVISION = 9007199254740991
ProviderOutcome = Literal["acquired", "released", "terminated"]


class BrowserLeaseError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("browser lease operation refused")
        self.code = code


@dataclass(frozen=True, slots=True)
class BrowserBindingKey:
    tenant_id: str
    ai_user_id: str
    target_system: str
    binding_id: str

    def __post_init__(self) -> None:
        if (any(re.fullmatch(r"[A-Za-z0-9_-]{1,96}", v) is None
                for v in (self.tenant_id, self.ai_user_id, self.binding_id))
                or self.target_system not in {"oa", "u8", "hikvision_ivms"}):
            raise ValueError("browser_binding_key_invalid")


@dataclass(frozen=True, slots=True)
class BrowserLeaseBindingSnapshot(BrowserBindingKey):
    """Historical persisted identity for cleanup; deliberately has no verified subject."""

    binding_revision: int

    def __post_init__(self) -> None:
        BrowserBindingKey.__post_init__(self)
        if (type(self.binding_revision) is not int
                or not 1 <= self.binding_revision <= MAX_BROWSER_REVISION):
            raise ValueError("browser_binding_snapshot_invalid")


@dataclass(frozen=True, slots=True)
class BrowserLeaseClaim:
    auth: BrowserAuthFact = field(repr=False)
    binding: BrowserBindingFact | BrowserLeaseBindingSnapshot = field(repr=False)
    lease_epoch: int
    lease_revision: int
    holder_id: str = field(repr=False)
    operation_id: str = field(repr=False)
    provider_key: str
    deadline: datetime

    def __post_init__(self) -> None:
        if (
            self.auth.owner.tenant_id != self.binding.tenant_id
            or self.auth.owner.user_id != self.binding.ai_user_id
            or any(type(v) is not int or not 1 <= v <= MAX_BROWSER_REVISION
                   for v in (self.lease_epoch, self.lease_revision))
            or any(re.fullmatch(r"[A-Za-z0-9_-]{1,96}", v) is None
                   for v in (self.holder_id, self.operation_id, self.provider_key))
            or self.deadline.tzinfo is None or self.deadline.utcoffset() is None
        ):
            raise ValueError("browser_claim_invalid")


@dataclass(frozen=True, slots=True)
class BrowserProviderExpectation:
    """Verifier must obtain actual provider facts matching every field here."""

    claim: BrowserLeaseClaim = field(repr=False)
    challenge: bytes = field(repr=False)
    manifest_digest: bytes = field(repr=False)
    resource_ref: bytes | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class BrowserProviderFact:
    """Only the injected trusted verifier may produce accepted provider facts."""

    provider_key: str
    operation_id: str = field(repr=False)
    holder_id: str = field(repr=False)
    lease_epoch: int
    challenge: bytes = field(repr=False)
    manifest_digest: bytes = field(repr=False)
    resource_ref: bytes = field(repr=False)
    outcome: ProviderOutcome


class BrowserProviderProofPort(Protocol):
    async def verify(
        self, expected: BrowserProviderExpectation, evidence: bytes,
    ) -> BrowserProviderFact:
        """Verify provider evidence, not a caller assertion/digest or a disconnect."""
        ...


class BrowserCleanupAuthorityPort(Protocol):
    async def check_recovery(self, key: BrowserBindingKey) -> None:
        """Authorize this full binding lookup before any persisted claim is read."""
        ...

    async def check_cleanup(self, claim: BrowserLeaseClaim) -> None:
        """Authorize the trusted reconciler for this original immutable claim."""
        ...


class BrowserResourceSubjectPort(Protocol):
    async def check_subject(self, claim: BrowserLeaseClaim, resource_ref: bytes) -> None:
        """Independently read this exact live resource's subject and compare binding."""
        ...


class BrowserLeaseStorePort(Protocol):
    async def recover_cleanup(self, key: BrowserBindingKey) -> BrowserLeaseClaim:
        """Recover a historical snapshot for independently authorized cleanup only."""
        ...

    async def reserve(
        self, auth: BrowserAuthFact, binding: BrowserBindingFact,
        provider_alias: str, *, ttl_seconds: int,
    ) -> BrowserLeaseClaim: ...

    async def start_acquisition(self, claim: BrowserLeaseClaim) -> BrowserLeaseClaim: ...

    async def record_acquired(
        self, claim: BrowserLeaseClaim, evidence: bytes,
    ) -> BrowserLeaseClaim: ...

    async def renew(
        self, claim: BrowserLeaseClaim, *, ttl_seconds: int,
    ) -> BrowserLeaseClaim: ...

    async def authorize_resource(self, claim: BrowserLeaseClaim) -> bytes: ...

    async def quarantine(self, claim: BrowserLeaseClaim) -> BrowserLeaseClaim: ...

    async def cleanup(
        self, claim: BrowserLeaseClaim, evidence: bytes | None = None,
    ) -> bool:
        """True only after exact proof or atomic never-sent release; false keeps quota."""
        ...
