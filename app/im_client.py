"""Process-local credential reuse never substitutes for current authorization."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    SessionRevocationStoreError,
    SessionRevocationStorePort,
    authenticated_session,
)
from app.ports.communication import IMReadIdentity
from app.ports.im_client import (
    CommunicationClientPort,
    IMClientBindingSnapshot,
    IMClientBindingStorePort,
    IMClientError,
    IMClientRequest,
    IMClientResponse,
)

_LOG = logging.getLogger("uvicorn.error.im_client")
_HostUserKey = tuple[str, str, str | None, str, str, str]


@dataclass(slots=True)
class _CredentialEntry:
    snapshot: IMClientBindingSnapshot = field(repr=False)
    identity: IMReadIdentity = field(repr=False)
    scopes: dict[str, datetime] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class _CredentialSlot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    entry: _CredentialEntry | None = field(default=None, repr=False)


@dataclass(slots=True)
class _RequestLease:
    context: AuthenticatedSessionContext = field(repr=False)
    snapshot: IMClientBindingSnapshot = field(repr=False)
    slot: _CredentialSlot = field(repr=False)
    scope: str = field(repr=False)
    entry: _CredentialEntry | None = field(default=None, repr=False)


class IMClientService:
    def __init__(
        self, store: IMClientBindingStorePort, client: CommunicationClientPort,
        revocations: SessionRevocationStorePort, *, provider: str,
        im_instance_id: str, realm_id: str,
    ) -> None:
        self._store, self._client, self._revocations = store, client, revocations
        self.provider, self.im_instance_id, self.realm_id = provider, im_instance_id, realm_id
        self._slots: dict[_HostUserKey, _CredentialSlot] = {}

    def _release(self, entry: _CredentialEntry, scope: str) -> None:
        self._client.release_scope(entry.identity, scope)
        entry.scopes.pop(scope, None)

    def _invalidate(self, lease: _RequestLease) -> None:
        entry = lease.entry
        if entry is not None:
            for scope in tuple(entry.scopes):
                self._release(entry, scope)
            if lease.slot.entry is entry:
                lease.slot.entry = None

    def _deny_session(self, lease: _RequestLease) -> None:
        if lease.entry is not None:
            self._release(lease.entry, lease.scope)
            if not lease.entry.scopes and lease.slot.entry is lease.entry:
                lease.slot.entry = None

    async def _session(self, context: AuthenticatedSessionContext) -> None:
        if context.expires_at <= datetime.now(UTC):
            _LOG.info("im_client stage=lease result=denied reason=expired")
            raise IMClientError("authentication_required", 401, "Authentication required.")
        try:
            revoked = await self._revocations.is_revoked(context.fingerprint)
        except SessionRevocationStoreError:
            _LOG.info("im_client stage=lease result=denied reason=store_unavailable")
            raise IMClientError(
                "authentication_unavailable", 503, "Authentication is unavailable.",
            ) from None
        if revoked or context.expires_at <= datetime.now(UTC):
            _LOG.info("im_client stage=lease result=denied reason=session_revoked")
            raise IMClientError("authentication_required", 401, "Authentication required.")

    async def _current_binding(self, principal: Principal) -> IMClientBindingSnapshot:
        snapshot = await self._store.current_client(
            principal, provider=self.provider, im_instance_id=self.im_instance_id,
            realm_id=self.realm_id,
        )
        if snapshot is None or snapshot.binding.status != "active":
            raise IMClientError("im_mapping_required", 409, "IM account mapping is required.")
        binding = snapshot.binding
        if (
            binding.tenant_id != "default"
            or binding.tenant_id != principal.org_ctx.tenant_id
            or binding.ai_user_id != principal.ai_user_id
            or binding.directory_user_id != principal.org_ctx.directory_user_id
            or (binding.provider, binding.im_instance_id, binding.realm_id)
            != (self.provider, self.im_instance_id, self.realm_id)
        ):
            raise IMClientError("im_identity_mismatch", 409, "IM identity does not match.")
        return snapshot

    async def revalidate(self, lease: _RequestLease) -> None:
        try:
            await self._session(lease.context)
        except IMClientError as error:
            if error.http_status == 401:
                self._deny_session(lease)
            raise
        try:
            current = await self._current_binding(lease.context.principal)
        except IMClientError as error:
            if error.http_status == 409:
                self._invalidate(lease)
            raise
        if current != lease.snapshot:
            self._invalidate(lease)
            _LOG.info("im_client stage=lease result=denied reason=mapping_changed")
            raise IMClientError("im_mapping_required", 409, "IM account mapping changed.")

    def _sweep(self) -> None:
        now = datetime.now(UTC)
        for slot in self._slots.values():
            entry = slot.entry
            if entry is None:
                continue
            for scope, expires in tuple(entry.scopes.items()):
                if expires <= now:
                    self._release(entry, scope)
            if not entry.scopes:
                slot.entry = None

    async def acquire(self, principal: Principal) -> _RequestLease:
        context = authenticated_session.get()
        if context is None or context.principal != principal:
            raise IMClientError("authentication_required", 401, "Authentication required.")
        await self._session(context)
        self._sweep()
        key = (
            principal.org_ctx.tenant_id, principal.ai_user_id, principal.org_ctx.directory_user_id,
            self.provider, self.im_instance_id, self.realm_id,
        )
        slot = self._slots.setdefault(key, _CredentialSlot())
        snapshot = await self._current_binding(principal)
        scope = hashlib.sha256(b"im-client-session\0" + context.fingerprint).hexdigest()
        lease = _RequestLease(context, snapshot, slot, scope, slot.entry)
        async with slot.lock:
            # The lock covers only the credential handshake, never API calls or streams.
            await self.revalidate(lease)
            if slot.entry is not None and slot.entry.snapshot != snapshot:
                lease.entry = slot.entry
                self._invalidate(lease)
            entry = slot.entry
            if entry is None:
                lease.entry = None
                try:
                    identity = await self._client.client_identity(snapshot.binding)
                except IMClientError:
                    await self.revalidate(lease)
                    _LOG.info("im_client stage=credential result=failed cache=miss")
                    raise
                expected = snapshot.binding
                if (identity.provider, identity.im_instance_id,
                    identity.realm_id, identity.im_user_id) != (
                    expected.provider, expected.im_instance_id,
                    expected.realm_id, expected.im_user_id,
                ):
                    raise IMClientError("im_identity_mismatch", 409, "IM identity does not match.")
                await self.revalidate(lease)
                entry = _CredentialEntry(snapshot, identity)
                slot.entry = entry
                _LOG.info("im_client stage=credential result=verified cache=miss")
            else:
                _LOG.debug("im_client stage=credential result=hit cache=hit")
            lease.entry = entry
            entry.scopes[scope] = max(
                context.expires_at, entry.scopes.get(scope, context.expires_at),
            )
        return lease

    async def identity(self, principal: Principal) -> dict[str, str]:
        lease = await self.acquire(principal)
        await self.revalidate(lease)
        binding = lease.snapshot.binding
        return {
            "provider": binding.provider, "im_instance_id": binding.im_instance_id,
            "realm_id": binding.realm_id, "im_user_id": binding.im_user_id,
        }

    @asynccontextmanager
    async def request(
        self, lease: _RequestLease, request: IMClientRequest,
    ) -> AsyncIterator[IMClientResponse]:
        async def revalidate() -> None:
            await self.revalidate(lease)

        await revalidate()
        assert lease.entry is not None
        try:
            async with self._client.request(
                lease.entry.identity, request, revalidate=revalidate,
            ) as response:
                await revalidate()
                yield response
        except IMClientError as error:
            # A late upstream 401 may retire only the entry that issued this request.
            if error.invalidate_credentials:
                self._invalidate(lease)
            await revalidate()
            raise

    async def aclose(self) -> None:
        self._slots.clear()
        await self._client.aclose()
