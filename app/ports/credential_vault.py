"""Purpose-bound credential reference contract, without a credential implementation."""

from typing import Literal, Protocol

from app.browser_skill.models import Contract, OpaqueId, ScopeBinding


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
