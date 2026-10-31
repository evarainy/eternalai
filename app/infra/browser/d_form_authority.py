"""Current PG grants and exact, explicitly handed-over D resources."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.d_form_configuration import (
    Digest,
    Identity,
    LocalConfiguration,
    read_local_model,
)
from app.infra.browser.d_form_execution import DFormTarget
from app.infra.browser.local_resource_lifecycle import registered_subject_digest
from app.infra.persistence.browser.authorization import PostgreSQLBrowserBindingReader
from app.ports.auth import Principal, VerifiedSessionToken, authenticated_session
from app.ports.browser_fill_stop import DFormHandoffView
from app.ports.browser_publication_store import PublicationOperation
from app.ports.browser_run_store import RunSnapshot
from app.ports.browser_store import BrowserLeaseClaim, BrowserLeaseError
from app.ports.credential_vault import BrowserBindingFact


@dataclass(frozen=True, slots=True, repr=False)
class DFormActor:
    verified: VerifiedSessionToken
    bound_session: str
    required_role: str

    @property
    def owner(self) -> BrowserOwner:
        principal = self.verified.principal
        return BrowserOwner(
            tenant_id=principal.org_ctx.tenant_id,
            user_id=principal.ai_user_id,
            session_id=self.bound_session,
        )


@dataclass(frozen=True, slots=True, repr=False)
class DFormRequestActor:
    """Metadata from the verified HTTP session, scoped to one bound client session."""

    principal: Principal
    fingerprint: bytes
    expires_at: datetime
    bound_session: str

    @property
    def owner(self) -> BrowserOwner:
        return BrowserOwner(
            tenant_id=self.principal.org_ctx.tenant_id,
            user_id=self.principal.ai_user_id,
            session_id=self.bound_session,
        )


class OwnedDFormTargetAuthority:
    """Binding resolution has no browser creation, resource file or DOM identity."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession],
                 binding: BrowserBindingFact) -> None:
        self._sessions, self._binding = sessions, binding
        self._reader = PostgreSQLBrowserBindingReader(sessions)

    async def resolve_binding(self, run: RunSnapshot) -> BrowserBindingFact:
        binding = self._binding
        if (run.owner.tenant_id != binding.tenant_id or run.owner.user_id != binding.ai_user_id
                or run.admission.binding_id != binding.binding_id
                or run.admission.binding_revision != binding.binding_revision
                or run.admission.target_system != binding.target_system):
            raise BrowserLeaseError("browser_fill_stop_scope_denied")
        await self._reader.check_binding(binding)
        return binding


async def current_actor(
    session: AsyncSession,
    binder: PrincipalSessionBinder,
    actor: DFormActor,
) -> None:
    verified = actor.verified
    if (
        verified.expires_at <= datetime.now(UTC)
        or actor.required_role not in verified.principal.roles
        or binder.bind(verified.principal, actor.bound_session) != actor.bound_session
    ):
        raise BrowserLeaseError("d_form_authorization_denied")
    present = (
        await session.execute(
            text(
                "SELECT 1 WHERE :expires > clock_timestamp()"
                " AND EXISTS (SELECT 1 FROM principal_roles WHERE tenant_id=:tenant"
                " AND ai_user_id=:actor AND role=:role)"
                " AND EXISTS (SELECT 1 FROM sessions"
                " WHERE tenant_id=:tenant AND session_id=:session)"
                " AND NOT EXISTS (SELECT 1 FROM auth_session_revocations"
                " WHERE token_fingerprint=:fingerprint)"
            ),
            {
                "expires": verified.expires_at,
                "tenant": actor.owner.tenant_id,
                "actor": actor.owner.user_id,
                "role": actor.required_role,
                "session": actor.bound_session,
                "fingerprint": verified.fingerprint,
            },
        )
    ).scalar_one_or_none()
    if present != 1:
        raise BrowserLeaseError("d_form_authorization_denied")


class DFormPublicationGrants:
    """No implicit admin grant; independently verified actor and explicit operations."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        binder: PrincipalSessionBinder,
        business: DFormActor,
        publisher: DFormActor,
        binding: BrowserBindingFact,
        skill_id: str,
        publication_digest: str,
        operations: frozenset[PublicationOperation],
    ) -> None:
        if (
            publisher.owner.tenant_id != binding.tenant_id
            or publisher.owner.user_id == business.owner.user_id
            or business.owner.tenant_id != binding.tenant_id
            or business.owner.user_id != binding.ai_user_id
            or not operations
        ):
            raise ValueError("d_form_identity_invalid")
        self.sessions, self.binder = sessions, binder
        self.business, self.publisher, self.binding = business, publisher, binding
        self.skill_id, self.digest, self.operations = skill_id, publication_digest, operations
        self.reader = PostgreSQLBrowserBindingReader(sessions)

    async def check(
        self,
        owner: BrowserOwner,
        skill_id: str,
        operation: PublicationOperation,
        publication_digest: str,
    ) -> bool:
        async with self.sessions() as session:
            return await self.check_in_session(
                session, owner, skill_id, operation, publication_digest
            )

    async def check_in_session(
        self,
        session: AsyncSession,
        owner: BrowserOwner,
        skill_id: str,
        operation: PublicationOperation,
        publication_digest: str,
    ) -> bool:
        if (
            owner != self.business.owner
            or skill_id != self.skill_id
            or publication_digest != self.digest
            or operation not in self.operations
        ):
            return False
        await current_actor(session, self.binder, self.business)
        await current_actor(session, self.binder, self.publisher)
        await self.reader.check_binding_in_session(session, self.binding)
        return True


class FixedDFormPublicationGrants:
    """One installed owner and binding, authorized by real request or Run evidence.

    This is an explicit deployment grant for the one fixed D capability. It never
    manufactures a Principal or a service token. Background checks require a
    current, exact authenticated Run, not merely an owner string.
    """

    # Replacing the active D publication retires its previous head atomically.
    _operations = frozenset({"prepare", "activate", "deactivate", "read", "execute"})

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession],
        binder: PrincipalSessionBinder, binding: BrowserBindingFact,
        skill_id: str, publication_digest: str,
    ) -> None:
        self.sessions, self.binder, self.binding = sessions, binder, binding
        self.skill_id, self.digest = skill_id, publication_digest
        self.reader = PostgreSQLBrowserBindingReader(sessions)

    async def check(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
        publication_digest: str,
    ) -> bool:
        async with self.sessions() as session:
            return await self.check_in_session(
                session, owner, skill_id, operation, publication_digest,
            )

    async def check_in_session(
        self, session: AsyncSession, owner: BrowserOwner, skill_id: str,
        operation: PublicationOperation, publication_digest: str,
    ) -> bool:
        binding = self.binding
        if (
            owner.tenant_id != binding.tenant_id
            or owner.user_id != binding.ai_user_id
            or skill_id != self.skill_id
            or publication_digest != self.digest
            or operation not in self._operations
        ):
            return False
        await self.reader.check_binding_in_session(session, binding)
        captured = authenticated_session.get()
        if captured is not None:
            principal = captured.principal
            if (
                principal.org_ctx.tenant_id != binding.tenant_id
                or principal.ai_user_id != binding.ai_user_id
                or principal.org_ctx.directory_user_id is None
                or registered_subject_digest(
                    binding.target_system, principal.org_ctx.directory_user_id,
                ) != binding.subject_digest
            ):
                return False
            try:
                if self.binder.bind(principal, owner.session_id) != owner.session_id:
                    return False
            except Exception:
                return False
            return bool((await session.execute(text(
                "SELECT 1 WHERE :expires > clock_timestamp()"
                " AND NOT EXISTS (SELECT 1 FROM auth_session_revocations"
                " WHERE token_fingerprint=:fingerprint)"
            ), {
                "expires": captured.expires_at,
                "fingerprint": captured.fingerprint,
            })).scalar_one_or_none())
        if operation not in {"read", "execute"}:
            return False
        return bool((await session.execute(text(
            "SELECT 1 FROM browser_runs r WHERE r.tenant_id=:tenant"
            " AND r.ai_user_id=:user AND r.session_id=:session"
            " AND r.publication_digest=:digest AND r.target_system='oa'"
            " AND r.binding_id=:binding AND r.binding_revision=:revision"
            " AND r.auth_evidence_version='verified-session-v1'"
            " AND r.auth_expires_at > clock_timestamp()"
            " AND r.status IN ('running','waiting_user')"
            " AND EXISTS (SELECT 1 FROM sessions s WHERE s.tenant_id=r.tenant_id"
            " AND s.session_id=r.session_id)"
            " AND NOT EXISTS (SELECT 1 FROM auth_session_revocations x"
            " WHERE x.token_fingerprint=r.auth_fingerprint) LIMIT 1"
        ), {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "digest": bytes.fromhex(self.digest),
            "binding": binding.binding_id, "revision": binding.binding_revision,
        })).scalar_one_or_none())


class FixedDFormCleanupGrant:
    """Release only a resource whose persisted authorization is this exact Run."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], binding: BrowserBindingFact,
        publication_digest: str,
    ) -> None:
        self.sessions, self.binding = sessions, binding
        self.publication = bytes.fromhex(publication_digest)

    async def check_recovery(self, key: object) -> None:
        # A binding key alone cannot identify the authorized Run's resource.
        raise BrowserLeaseError("d_form_resource_scope_denied")

    async def _exact_run(
        self, session: AsyncSession, *, owner: BrowserOwner, run_id: str,
        fingerprint: bytes, expires_at: datetime, revision: int,
        task_id: str | None = None, historical: bool = False,
    ) -> None:
        binding = self.binding
        if (
            owner.tenant_id != binding.tenant_id
            or owner.user_id != binding.ai_user_id
            or (revision > binding.binding_revision if historical
                else revision != binding.binding_revision)
        ):
            raise BrowserLeaseError("d_form_resource_scope_denied")
        found = (await session.execute(text(
            "SELECT 1 FROM browser_runs WHERE tenant_id=:tenant AND ai_user_id=:user"
            " AND session_id=:session AND run_id=:run"
            " AND (CAST(:task AS TEXT) IS NULL OR task_id=CAST(:task AS TEXT))"
            " AND publication_digest=:digest AND target_system='oa'"
            " AND binding_id=:binding AND binding_revision=:revision"
            " AND auth_fingerprint=:fingerprint AND auth_expires_at=:expires"
            " AND auth_evidence_version='verified-session-v1'"
        ), {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "run": run_id, "task": task_id,
            "digest": self.publication, "binding": binding.binding_id,
            "revision": revision, "fingerprint": fingerprint,
            "expires": expires_at,
        })).scalar_one_or_none()
        if found != 1:
            raise BrowserLeaseError("d_form_resource_scope_denied")

    async def check_cleanup(self, claim: BrowserLeaseClaim) -> None:
        if claim.auth.authorization_run_id is None or claim.binding != self.binding:
            raise BrowserLeaseError("d_form_resource_scope_denied")
        async with self.sessions() as session:
            await self._exact_run(
                session, owner=claim.auth.owner,
                run_id=claim.auth.authorization_run_id,
                fingerprint=claim.auth.fingerprint,
                expires_at=claim.auth.expires_at,
                revision=self.binding.binding_revision,
            )

    async def authorize_run(self, session: AsyncSession, run: RunSnapshot) -> None:
        await self._exact_run(
            session, owner=run.owner, run_id=run.run_id,
            fingerprint=run.admission.auth_fingerprint,
            expires_at=run.admission.auth_expires_at,
            revision=run.admission.binding_revision, task_id=run.task_id,
        )

    async def authorize_historical_run(self, session: AsyncSession, run: RunSnapshot) -> None:
        """Exact persisted Run at this or an older binding revision, nothing more.

        Only the bounded handoff closure uses it. It grants no business action,
        generic cleanup, lease release or decryption of input/resource references.
        """
        await self._exact_run(
            session, owner=run.owner, run_id=run.run_id,
            fingerprint=run.admission.auth_fingerprint,
            expires_at=run.admission.auth_expires_at,
            revision=run.admission.binding_revision, task_id=run.task_id,
            historical=True,
        )


class DFormNativeRecord(LocalConfiguration):
    pid: int = Field(gt=0, strict=True)
    created: float = Field(gt=0, allow_inf_nan=False)
    executable: str = Field(min_length=1, max_length=2048, repr=False)
    profile: str = Field(min_length=1, max_length=2048, repr=False)
    profile_root: str = Field(min_length=1, max_length=2048, repr=False)
    port: int = Field(ge=1, le=65535, strict=True)
    context_id: str = Field(min_length=1, max_length=128, repr=False)
    target_id: str = Field(min_length=1, max_length=128, repr=False)
    host: str = Field(min_length=1, max_length=253, repr=False)
    scheme: Literal["http", "https"]

    @field_validator("executable", "profile", "profile_root")
    @classmethod
    def absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("d_form_config_invalid")
        return value

    def native(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class DFormIdentityProjection(LocalConfiguration):
    """An approved current-account marker, never reporter text or display name.

    Its semantics must be established locally before installing this record.
    Only a read-only server-rendered account ID attribute is supported here.
    """

    kind: Literal["current_account_identifier"]
    frame_selectors: tuple[str, ...] = Field(default=(), max_length=3)
    selector: str = Field(pattern=r"^#[A-Za-z][A-Za-z0-9_-]{0,63}$")
    attribute: Literal["data-current-account-id"]

    @field_validator("frame_selectors")
    @classmethod
    def frames(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        import re

        if any(re.fullmatch(r"iframe#[A-Za-z][A-Za-z0-9_-]{0,63}", v) is None for v in values):
            raise ValueError("d_form_config_invalid")
        return values


class DFormHandoffRecord(LocalConfiguration):
    version: Literal["d-form-handoff-v1"]
    resource_version: int = Field(ge=1, strict=True)
    expires_at: datetime
    tenant_id: Identity = Field(repr=False)
    user_id: Identity = Field(repr=False)
    bound_session: str = Field(min_length=1, max_length=512, repr=False)
    binding_id: Identity = Field(repr=False)
    binding_revision: int = Field(ge=1, strict=True)
    publication_digest: Digest
    task_id: Identity = Field(repr=False)
    run_id: Identity = Field(repr=False)
    native: DFormNativeRecord
    identity_projection: DFormIdentityProjection | None = None

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("d_form_config_invalid")
        return value


class RegisteredDFormTargetAuthority:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        binder: PrincipalSessionBinder,
        binding: BrowserBindingFact,
        business: DFormActor,
        handoff_actor: DFormActor,
        record_path: Path | None,
        publication_digest: str,
        origin: str,
    ) -> None:
        self.sessions, self.binder, self.binding = sessions, binder, binding
        self.business, self.handoff_actor = business, handoff_actor
        self.path, self.publication_digest, self.origin = record_path, publication_digest, origin
        self.reader = PostgreSQLBrowserBindingReader(sessions)
        self._confirmed_digest: bytes | None = None
        self._viewed: tuple[bytes, DFormHandoffView] | None = None

    def _record(self) -> tuple[DFormHandoffRecord, bytes]:
        if self.path is None:
            raise BrowserLeaseError("d_form_subject_proof_unavailable")
        try:
            record, raw = read_local_model(self.path, DFormHandoffRecord)
        except Exception:
            raise BrowserLeaseError("d_form_subject_proof_unavailable") from None
        owner, binding = self.business.owner, self.binding
        location = urlsplit(self.origin)
        if (
            record.expires_at <= datetime.now(UTC)
            or (record.tenant_id, record.user_id, record.bound_session)
            != (owner.tenant_id, owner.user_id, owner.session_id)
            or record.binding_id != binding.binding_id
            or record.binding_revision != binding.binding_revision
            or record.publication_digest != self.publication_digest
            or record.native.host != location.hostname
            or record.native.scheme != location.scheme
            or self.handoff_actor.owner.tenant_id != binding.tenant_id
        ):
            raise BrowserLeaseError("d_form_resource_scope_denied")
        return record, hashlib.sha256(raw).digest()

    async def _current(self) -> None:
        # Short transactions finish before any browser observation.
        async with self.sessions() as session:
            await current_actor(session, self.binder, self.business)
            await current_actor(session, self.binder, self.handoff_actor)
            await self.reader.check_binding_in_session(session, self.binding)

    async def _canonical_record(self) -> tuple[DFormHandoffRecord, bytes]:
        await self._current()
        record, digest = self._record()
        # Read only the exact recorded, server-admitted Run under this handoff's
        # current actor/binding scope. Never impersonate its business session.
        async with self.sessions() as session:
            present = (
                await session.execute(
                    text(
                        "SELECT 1 FROM browser_runs WHERE tenant_id=:tenant"
                        " AND ai_user_id=:user AND session_id=:session"
                        " AND task_id=:task AND run_id=:run AND target_system='oa'"
                        " AND binding_id=:binding AND binding_revision=:revision"
                        " AND publication_digest=:publication"
                        " AND auth_evidence_version='verified-session-v1'"
                        " AND auth_fingerprint=:fingerprint AND auth_expires_at=:expires"
                        " AND auth_expires_at > clock_timestamp()"
                    ),
                    {
                        "tenant": self.business.owner.tenant_id,
                        "user": self.business.owner.user_id,
                        "session": self.business.owner.session_id,
                        "task": record.task_id,
                        "run": record.run_id,
                        "binding": self.binding.binding_id,
                        "revision": self.binding.binding_revision,
                        "publication": bytes.fromhex(self.publication_digest),
                        "fingerprint": self.business.verified.fingerprint,
                        "expires": self.business.verified.expires_at,
                    },
                )
            ).scalar_one_or_none()
        if present != 1 or not hmac.compare_digest(digest, self._record()[1]):
            raise BrowserLeaseError("d_form_resource_scope_denied")
        return record, digest

    def _check_handoff_actor(self, actor: VerifiedSessionToken) -> None:
        if (
            actor.fingerprint != self.handoff_actor.verified.fingerprint
            or actor.principal != self.handoff_actor.verified.principal
            or actor.expires_at != self.handoff_actor.verified.expires_at
        ):
            raise BrowserLeaseError("d_form_resource_scope_denied")

    async def handoff_preview(self, actor: VerifiedSessionToken) -> DFormHandoffView:
        self._check_handoff_actor(actor)
        record, digest = await self._canonical_record()
        if record.identity_projection is None:
            raise BrowserLeaseError("d_form_subject_proof_unavailable")
        if self._viewed is None or not hmac.compare_digest(self._viewed[0], digest):
            self._viewed = (
                digest,
                DFormHandoffView(
                    version_token=secrets.token_hex(32),
                    resource_version=record.resource_version,
                    task_id=record.task_id,
                    run_id=record.run_id,
                ),
            )
        return self._viewed[1]

    async def confirm_handoff(
        self, actor: VerifiedSessionToken, confirmation: DFormHandoffView,
    ) -> None:
        self._check_handoff_actor(actor)
        record, digest = await self._canonical_record()
        viewed = self._viewed
        if (
            viewed is None
            or not hmac.compare_digest(viewed[0], digest)
            or not hmac.compare_digest(viewed[1].version_token, confirmation.version_token)
            or confirmation.resource_version != record.resource_version
            or confirmation.task_id != record.task_id
            or confirmation.run_id != record.run_id
            or record.identity_projection is None
        ):
            raise BrowserLeaseError("d_form_resource_scope_denied")
        # This accepts only the explicit recorded scope. It is NOT a subject proof.
        self._confirmed_digest = digest

    async def ready(self) -> bool:
        await self._current()
        record, digest = self._record()
        return record.identity_projection is not None and self._confirmed_digest == digest

    async def resolve(self, run: RunSnapshot) -> DFormTarget:
        await self._current()
        record, digest = self._record()
        if self._confirmed_digest != digest or record.identity_projection is None:
            raise BrowserLeaseError("d_form_subject_proof_unavailable")
        if (
            run.owner != self.business.owner
            or run.task_id != record.task_id
            or run.run_id != record.run_id
            or run.admission.binding_id != self.binding.binding_id
            or run.admission.binding_revision != self.binding.binding_revision
            or run.admission.publication_digest.hex() != self.publication_digest
            or run.admission.auth_fingerprint != self.business.verified.fingerprint
            or run.admission.auth_expires_at != self.business.verified.expires_at
        ):
            raise BrowserLeaseError("d_form_resource_scope_denied")
        return DFormTarget(run.owner, run.task_id, run.run_id, self.binding, record.native.native())

    @staticmethod
    def _native_fence(record: DFormHandoffRecord) -> None:
        import psutil

        native = record.native
        process = psutil.Process(native.pid)
        if (
            process.create_time() != native.created
            or Path(process.exe()).resolve() != Path(native.executable).resolve()
            or Path(native.profile).resolve().parent != Path(native.profile_root).resolve()
            or "--user-data-dir=" + native.profile not in process.cmdline()
            or not any(
                c.status == psutil.CONN_LISTEN
                and c.laddr.ip == "127.0.0.1"
                and c.laddr.port == native.port
                for c in process.net_connections(kind="tcp")
            )
        ):
            raise BrowserLeaseError("d_form_resource_scope_denied")

    async def _observe(self, record: DFormHandoffRecord) -> str:
        from playwright.async_api import async_playwright

        projection = record.identity_projection
        if projection is None:
            raise BrowserLeaseError("d_form_subject_proof_unavailable")
        self._native_fence(record)
        sessions = []
        playwright = None
        try:
            async with asyncio.timeout(10):
                playwright = await async_playwright().start()
                browser = await playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{record.native.port}",
                    timeout=5000,
                )
                matched = []
                for context in browser.contexts:
                    for page in context.pages:
                        cdp = await context.new_cdp_session(page)
                        sessions.append(cdp)
                        info = (await cdp.send("Target.getTargetInfo"))["targetInfo"]
                        if (
                            info.get("browserContextId") == record.native.context_id
                            and info.get("targetId") == record.native.target_id
                        ):
                            matched.append(page)
                if len(matched) != 1:
                    raise BrowserLeaseError("d_form_resource_scope_denied")
                page = matched[0]
                initial_frames = tuple((frame, frame.url) for frame in page.frames)
                page_set = tuple(page.context.pages)

                def fence() -> None:
                    self._native_fence(record)
                    if (
                        page.is_closed()
                        or not browser.is_connected()
                        or tuple(page.context.pages) != page_set
                        or tuple((frame, frame.url) for frame in page.frames) != initial_frames
                        or any(
                            urlsplit(url).username is not None
                            or urlsplit(url).password is not None
                            or (urlsplit(url).scheme, urlsplit(url).netloc)
                            != (urlsplit(self.origin).scheme, urlsplit(self.origin).netloc)
                            for _, url in initial_frames
                        )
                    ):
                        raise BrowserLeaseError("d_form_resource_scope_denied")

                fence()
                frame = page.main_frame
                for selector in projection.frame_selectors:
                    locator = frame.locator(selector)
                    if await locator.count() != 1 or not await locator.is_visible():
                        raise BrowserLeaseError("d_form_subject_proof_unavailable")
                    handle = await locator.element_handle()
                    nested = await handle.content_frame() if handle is not None else None
                    if nested is None:
                        raise BrowserLeaseError("d_form_subject_proof_unavailable")
                    frame = nested
                locator = frame.locator(projection.selector)
                if await locator.count() != 1 or not await locator.is_visible():
                    raise BrowserLeaseError("d_form_subject_proof_unavailable")
                subject = await locator.evaluate(
                    """(node, attribute) => {
                  if (!node.isConnected || node.isContentEditable ||
                      node.closest('input,textarea,select,[contenteditable]') ||
                      node.querySelector('input,textarea,select,[contenteditable]')) return null;
                  const value = node.getAttribute(attribute);
                  return typeof value === 'string' && value.length > 0 && value.length <= 256
                    ? value : null;
                }""",
                    projection.attribute,
                )
                fence()
                if type(subject) is not str or not subject or len(subject) > 256:
                    raise BrowserLeaseError("d_form_subject_proof_unavailable")
                return subject
        finally:
            # Detach ONLY our sessions/driver connection; never browser.close().
            try:
                outcomes = await asyncio.gather(
                    *(asyncio.wait_for(cdp.detach(), 2) for cdp in sessions),
                    return_exceptions=True,
                )
            finally:
                if playwright is not None:
                    await asyncio.wait_for(playwright.stop(), 3)
            if any(isinstance(outcome, BaseException) for outcome in outcomes):
                raise BrowserLeaseError("d_form_subject_proof_unavailable")

    async def _observe_on_driver_loop(self, record: DFormHandoffRecord) -> str:
        if sys.platform != "win32":
            return await self._observe(record)

        def observe() -> str:
            with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
                return runner.run(self._observe(record))

        operation = asyncio.create_task(asyncio.to_thread(observe))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # The bounded observer must detach before cancellation leaves this fence.
            await asyncio.shield(operation)
            raise

    async def check_subject(self, claim: BrowserLeaseClaim, target: DFormTarget) -> None:
        try:
            await self._current()
            record, digest = self._record()
            if (
                self._confirmed_digest != digest
                or target.owner != self.business.owner
                or target.task_id != record.task_id
                or target.run_id != record.run_id
                or target.binding != self.binding
                or target.native != record.native.native()
                or claim.binding != self.binding
                or claim.auth.owner != target.owner
                or claim.auth.authorization_run_id != target.run_id
                or claim.auth.evidence_version != "verified-session-v1"
                or claim.auth.fingerprint != self.business.verified.fingerprint
                or claim.auth.expires_at != self.business.verified.expires_at
            ):
                raise BrowserLeaseError("d_form_resource_scope_denied")
            expected = self.business.verified.principal.org_ctx.directory_user_id
            if (
                expected is None
                or not expected
                or len(expected) > 256
                or registered_subject_digest(self.binding.target_system, expected)
                != self.binding.subject_digest
            ):
                raise BrowserLeaseError("d_form_subject_proof_unavailable")
            subject = await self._observe_on_driver_loop(record)
            if (
                not hmac.compare_digest(subject.encode(), expected.encode())
                or registered_subject_digest(self.binding.target_system, subject)
                != self.binding.subject_digest
            ):
                raise BrowserLeaseError("d_form_subject_mismatch")
            del subject
            if self._record()[1] != digest:
                raise BrowserLeaseError("d_form_resource_scope_denied")
            await self._current()
        except BrowserLeaseError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BrowserLeaseError("d_form_subject_proof_unavailable") from None
