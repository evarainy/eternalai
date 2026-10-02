"""Purpose-bound facade; absence of a trusted authority always closes admission."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from app.browser_skill.models import ScopeBinding
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.ports.auth import OASessionCredential
from app.ports.credential_binding import PasswordBindingCredential
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserAuthorizationError,
    BrowserBindingFact,
    BrowserBindingReaderPort,
    BrowserCredentialGrant,
    BrowserCurrentAuthPort,
)


class MissingBrowserCurrentAuthChecker:
    """No production authorization-version source has been approved or wired yet."""

    async def check_current(self, fact: BrowserAuthFact) -> None:
        raise BrowserAuthorizationError("browser_authorization_revision_unavailable")


@dataclass(frozen=True, slots=True)
class _Grant:
    public: BrowserCredentialGrant
    auth: BrowserAuthFact
    binding: BrowserBindingFact


class BrowserCredentialVault:
    """The private consumer rereads/decrypts only after current facts are verified.

    Fact readers and consumers are trusted infrastructure dependencies, never client
    DTOs. A resource SUBJECT producer and version authority are deliberately required.
    """

    def __init__(
        self,
        *,
        binding_reader: BrowserBindingReaderPort,
        current_auth: BrowserCurrentAuthPort | None = None,
        fact_reader: Callable[[ScopeBinding], Awaitable[tuple[BrowserAuthFact, BrowserBindingFact]]]
        | None = None,
        credential_store: PostgreSQLCredentialStore | None = None,
        consumer: Callable[[PasswordBindingCredential | OASessionCredential], Awaitable[None]]
        | None = None,
    ) -> None:
        self._binding_reader = binding_reader
        self._current_auth = current_auth or MissingBrowserCurrentAuthChecker()
        self._fact_reader = fact_reader
        self._consumer = consumer
        self._credential_store = credential_store
        self._grants: dict[str, _Grant] = {}

    async def _check(
        self, binding: ScopeBinding, auth: BrowserAuthFact, fact: BrowserBindingFact
    ) -> None:
        if (
            auth.owner != binding.owner
            or auth.authorization_revision != binding.authorization_revision
            or auth.expires_at <= datetime.now(UTC)
            or (fact.tenant_id, fact.ai_user_id, fact.binding_id, fact.binding_revision)
            != (
                binding.owner.tenant_id,
                binding.owner.user_id,
                binding.binding_id,
                binding.binding_revision,
            )
        ):
            raise BrowserAuthorizationError("browser_owner_or_binding_mismatch")
        await self._current_auth.check_current(auth)
        await self._binding_reader.check_binding(fact)

    async def authorize(
        self,
        binding: ScopeBinding,
        purpose: Literal["browser_login", "browser_restore"],
    ) -> BrowserCredentialGrant:
        if purpose not in {"browser_login", "browser_restore"}:
            raise BrowserAuthorizationError("browser_credential_purpose_invalid")
        if self._fact_reader is None:
            raise BrowserAuthorizationError("browser_authorization_revision_unavailable")
        auth, fact = await self._fact_reader(binding)
        await self._check(binding, auth, fact)
        grant = BrowserCredentialGrant(grant_ref=uuid4().hex, binding=binding, purpose=purpose)
        self._grants[grant.grant_ref] = _Grant(grant, auth, fact)
        return grant

    async def consume(self, grant: BrowserCredentialGrant) -> None:
        captured = self._grants.pop(grant.grant_ref, None)
        if captured is None or captured.public != grant:
            raise BrowserAuthorizationError("browser_credential_grant_invalid")
        await self._check(grant.binding, captured.auth, captured.binding)
        if self._consumer is None or self._credential_store is None:
            raise BrowserAuthorizationError("browser_credential_consumer_unavailable")
        credential = await self._credential_store.load_for_browser(captured.binding, grant.purpose)
        # Recheck after the DB await, before releasing credential material to the private adapter.
        await self._check(grant.binding, captured.auth, captured.binding)
        await self._consumer(credential)
