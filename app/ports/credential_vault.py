"""Purpose-bound credential reference contract, without a credential implementation."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.browser_skill.models import BrowserOwner, Contract, OpaqueId, ScopeBinding


class BrowserCredentialGrant(Contract):
    grant_ref: OpaqueId
    binding: ScopeBinding
    purpose: Literal["browser_login", "browser_restore"]


class CredentialVaultPort(Protocol):
    async def authorize(
        self,
        binding: ScopeBinding,
        purpose: Literal["browser_login", "browser_restore"],
    ) -> BrowserCredentialGrant:
        """Validate current ownership and revision; no plaintext enters this contract."""
        ...


@dataclass(frozen=True, slots=True)
class BrowserAuthFact:
    """Trusted authorization capture; a version must come from its authority."""

    owner: BrowserOwner
    authorization_revision: int
    fingerprint: bytes = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.owner, BrowserOwner)
            or type(self.fingerprint) is not bytes
            or not isinstance(self.expires_at, datetime)
            or type(self.authorization_revision) is not int
            or not 0 <= self.authorization_revision <= 9007199254740991
            or len(self.fingerprint) != 32
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("browser_auth_fact_invalid")


@dataclass(frozen=True, slots=True)
class BrowserBindingFact:
    tenant_id: str
    ai_user_id: str
    target_system: str
    binding_id: str
    binding_revision: int
    subject_digest: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, str) and value.strip()
                for value in (self.tenant_id, self.ai_user_id, self.target_system, self.binding_id)
            )
            or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.binding_id) is None
            or type(self.subject_digest) is not bytes
            or type(self.binding_revision) is not int
            or not 1 <= self.binding_revision <= 9007199254740991
            or len(self.subject_digest) != 32
        ):
            raise ValueError("browser_binding_fact_invalid")


class BrowserAuthorizationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("browser authorization unavailable")
        self.code = code


class BrowserCurrentAuthPort(Protocol):
    async def check_current(self, fact: BrowserAuthFact) -> None:
        """Recheck trusted session binding, expiry, revocation, roles and Policy.

        This is a bounded trusted local/DB authority check, safe inside a short
        lease transaction. It must never perform provider/upstream network IO.
        """
        ...


class BrowserBindingReaderPort(Protocol):
    async def check_binding(self, fact: BrowserBindingFact) -> None:
        """Bounded local/DB current check; no provider/upstream network IO."""
        ...
