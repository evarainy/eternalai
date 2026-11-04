"""Current-reader authorization and bounded, process-local IM source references."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.ports.auth import (
    Principal,
    SessionRevocationStoreError,
    SessionRevocationStorePort,
    authenticated_session,
)
from app.ports.communication import (
    CommunicationLoginPort,
    CommunicationReaderPort,
    IMAccountBinding,
    IMLoginForm,
    IMMessage,
    IMReadContext,
    IMSelection,
)
from app.ports.im_context import (
    AuthorizedIMContext,
    IMAccountBindingStorePort,
    IMContextError,
    IMSourceReference,
)

_LOG = logging.getLogger("uvicorn.error.im_context")


def _digest(domain: str, value: bytes) -> str:
    return hashlib.sha256(domain.encode() + b"\0" + value).hexdigest()


def _manifest(messages: tuple[IMMessage, ...]) -> tuple[tuple[str, str, str], ...]:
    return tuple((m.message_id, m.version, _digest("im-text", m.text.encode())) for m in messages)


@dataclass(frozen=True, slots=True)
class _Reference:
    tenant: str
    user: str
    login: str = field(repr=False)
    session: str = field(repr=False)
    binding_id: str
    mapping_version: int
    selection: IMSelection = field(repr=False)
    manifest: tuple[tuple[str, str, str], ...]
    expires_at: datetime
    purpose: str = "self_read"


class IMContextService:
    def __init__(
        self,
        store: IMAccountBindingStorePort,
        reader: CommunicationReaderPort,
        login: CommunicationLoginPort,
        revocations: SessionRevocationStorePort,
        *,
        provider: str,
        im_instance_id: str,
        realm_id: str,
        im_origin: str,
        helper_url: str,
        host_origin: str,
    ) -> None:
        self._store, self._reader, self._login, self._revocations = (
            store,
            reader,
            login,
            revocations,
        )
        self.provider, self.im_instance_id, self.realm_id = provider, im_instance_id, realm_id
        self.im_origin, self.helper_url = im_origin, helper_url
        self.host_origin = host_origin
        self._refs: dict[str, _Reference] = {}

    async def _session(self, principal: Principal, bound_session: str) -> tuple[str, str]:
        context = authenticated_session.get()
        if (
            context is None
            or context.principal != principal
            or not bound_session
            or context.expires_at <= datetime.now(UTC)
        ):
            raise IMContextError("im_authentication_required", 401)
        try:
            revoked = await self._revocations.is_revoked(context.fingerprint)
        except SessionRevocationStoreError:
            raise IMContextError("im_revocation_unavailable", 503) from None
        if revoked or context.expires_at <= datetime.now(UTC):
            raise IMContextError("im_session_revoked", 401)
        return (
            _digest("im-login", context.fingerprint),
            _digest("im-session", bound_session.encode()),
        )

    async def _binding(self, principal: Principal) -> IMAccountBinding:
        binding = await self._store.current(
            principal,
            provider=self.provider,
            im_instance_id=self.im_instance_id,
            realm_id=self.realm_id,
        )
        if binding is None or binding.status != "active":
            raise IMContextError("im_mapping_required")
        if (
            binding.tenant_id != "default"
            or binding.tenant_id != principal.org_ctx.tenant_id
            or binding.ai_user_id != principal.ai_user_id
            or binding.directory_user_id != principal.org_ctx.directory_user_id
            or (binding.provider, binding.im_instance_id, binding.realm_id)
            != (self.provider, self.im_instance_id, self.realm_id)
        ):
            raise IMContextError("im_identity_mismatch")
        return binding

    async def issue_login(self, principal: Principal) -> tuple[IMAccountBinding, IMLoginForm]:
        await self._session(principal, "im-login-form")
        binding = await self._binding(principal)
        # Establish that the login subject still identifies the verified remote account.
        async with self._reader.current_identity(binding) as identity:
            self._check_identity(
                binding,
                identity.provider,
                identity.im_instance_id,
                identity.realm_id,
                identity.im_user_id,
            )
        if await self._binding(principal) != binding:
            raise IMContextError("im_mapping_changed")
        await self._session(principal, "im-login-form")
        return binding, self._login.build_login_form(binding)

    @staticmethod
    def _check_identity(binding: IMAccountBinding, *identity: str) -> None:
        if identity != (
            binding.provider,
            binding.im_instance_id,
            binding.realm_id,
            binding.im_user_id,
        ):
            _LOG.info("im_stage stage=identity result=mismatch body_reads=0")
            raise IMContextError("im_identity_mismatch")

    async def _read(self, binding: IMAccountBinding, selection: IMSelection) -> IMReadContext:
        if (
            selection.provider,
            selection.im_instance_id,
            selection.realm_id,
            selection.im_user_id,
        ) != (binding.provider, binding.im_instance_id, binding.realm_id, binding.im_user_id):
            raise IMContextError("im_identity_mismatch")
        async with self._reader.current_identity(binding) as identity:
            self._check_identity(
                binding,
                identity.provider,
                identity.im_instance_id,
                identity.realm_id,
                identity.im_user_id,
            )
            _LOG.info("im_stage stage=identity result=verified")
            anchor = await self._reader.read_anchor(identity, selection)
            if (
                anchor.message_id != selection.message_id
                or anchor.conversation_ref != selection.conversation_ref
            ):
                raise IMContextError("im_source_not_found", 404)
            result = await self._reader.read_context(identity, selection)
        ids = [m.message_id for m in result.messages]
        if (
            not result.found_anchor
            or not 1 <= len(ids) <= 4
            or len(set(ids)) != len(ids)
            or ids[-1] != selection.message_id
            or any(m.conversation_ref != selection.conversation_ref for m in result.messages)
            or _manifest((anchor,)) != _manifest((result.messages[-1],))
        ):
            raise IMContextError("im_source_changed")
        if len(json.dumps([m.text for m in result.messages], ensure_ascii=False).encode()) > 12288:
            raise IMContextError("im_source_too_large")
        _LOG.info("im_stage stage=context result=read count=%s", len(ids))
        return result

    def _sweep(self) -> None:
        now = datetime.now(UTC)
        for key in [key for key, value in self._refs.items() if value.expires_at <= now]:
            del self._refs[key]

    async def select(
        self,
        principal: Principal,
        bound_session: str,
        selection: IMSelection,
    ) -> IMSourceReference:
        login, session = await self._session(principal, bound_session)
        binding = await self._binding(principal)
        result = await self._read(binding, selection)
        if await self._binding(principal) != binding:
            raise IMContextError("im_mapping_changed")
        await self._session(principal, bound_session)
        self._sweep()
        if len(self._refs) >= 1024 or sum(r.login == login for r in self._refs.values()) >= 8:
            raise IMContextError("im_source_capacity", 503)
        expires = datetime.now(UTC) + timedelta(minutes=10)
        reference = _Reference(
            principal.org_ctx.tenant_id,
            principal.ai_user_id,
            login,
            session,
            binding.binding_id,
            binding.mapping_version,
            selection,
            _manifest(result.messages),
            expires,
        )
        metadata = [
            selection.model_dump(),
            reference.manifest,
            binding.binding_id,
            binding.mapping_version,
            login,
            session,
            reference.tenant,
            reference.user,
        ]
        if len(json.dumps(metadata, ensure_ascii=False).encode()) > 8192:
            raise IMContextError("im_source_too_large")
        ref_id = secrets.token_urlsafe(32)
        self._refs[ref_id] = reference
        _LOG.info("im_stage stage=select result=issued")
        return IMSourceReference(ref_id, expires, result.messages, result.deep_link)

    async def revalidate(
        self,
        principal: Principal,
        bound_session: str,
        ref_ids: tuple[str, ...],
    ) -> AuthorizedIMContext:
        login, session = await self._session(principal, bound_session)
        self._sweep()
        if len(ref_ids) != 1:
            raise IMContextError("im_source_invalid")
        ref = self._refs.get(ref_ids[0])
        if ref is None:
            raise IMContextError("im_source_expired")
        if (ref.tenant, ref.user) != (principal.org_ctx.tenant_id, principal.ai_user_id):
            raise IMContextError("im_source_not_found", 404)
        if (ref.login, ref.session, ref.purpose) != (login, session, "self_read"):
            raise IMContextError("im_source_expired")
        binding = await self._binding(principal)
        if (binding.binding_id, binding.mapping_version) != (ref.binding_id, ref.mapping_version):
            raise IMContextError("im_mapping_changed")
        result = await self._read(binding, ref.selection)
        if _manifest(result.messages) != ref.manifest:
            raise IMContextError("im_source_changed")
        if await self._binding(principal) != binding:
            raise IMContextError("im_mapping_changed")
        await self._session(principal, bound_session)
        if ref.expires_at <= datetime.now(UTC):
            raise IMContextError("im_source_expired")
        _LOG.info("im_stage stage=revalidate result=verified")
        return AuthorizedIMContext(ref_ids, result.messages, self)
