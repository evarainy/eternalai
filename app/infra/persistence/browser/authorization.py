"""Current browser authority from verified durable input and live local sources.

Run/lease identifiers only select evidence. Every business check validates the
captured session, signed conversation binding, current roles, binding and Policy.
Provider/network calls are deliberately absent from this adapter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.auth import Principal, authenticated_session
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    CanonicalRequest,
    ProtectedRunEnvelope,
    RunAction,
    RunAdmission,
    RunCleanup,
    RunSnapshot,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserAuthorizationError,
    BrowserBindingFact,
)
from app.ports.policy_guard import PolicyGuardPort
from app.ports.request_context import RequestOrgContext

PublicationCheck = Callable[[BrowserOwner, bytes, str, bool], Awaitable[CapabilitySpec]]
RunProofCheck = Callable[[RunSnapshot], Awaitable[None]]
CleanupAuthorize = Callable[[AsyncSession, RunSnapshot], Awaitable[None]]
CleanupCheck = Callable[[AsyncSession, RunSnapshot, RunCleanup], Awaitable[None]]


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedBrowserInput:
    principal: Principal = field(repr=False)
    capability_id: str
    arguments: dict[str, Any] = field(repr=False)
    channel: Literal["web", "cli", "api", "mock"]


def admission_from_row(row: RowMapping) -> RunAdmission:
    """Reconstruct exact stored cryptographic metadata, never a client DTO."""
    return RunAdmission(
        owner=BrowserOwner(
            tenant_id=row["tenant_id"], user_id=row["ai_user_id"], session_id=row["session_id"]
        ),
        task_id=row["task_id"],
        run_id=row["run_id"],
        target_system=row["target_system"],
        binding_id=row["binding_id"],
        binding_revision=row["binding_revision"],
        auth_fingerprint=bytes(row["auth_fingerprint"]),
        auth_expires_at=row["auth_expires_at"],
        publication_digest=bytes(row["publication_digest"]),
        input_revision=row["input_revision"],
        input_digest=bytes(row["input_digest"]),
        auth_evidence_version=row["auth_evidence_version"],
        protected_input=ProtectedRunEnvelope(
            row["input_cipher_version"],
            row["input_key_id"],
            bytes(row["input_nonce"]),
            bytes(row["input_ciphertext"]),
        ),
    )


class PostgreSQLBrowserBindingReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def check_binding(self, fact: BrowserBindingFact) -> None:
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT binding_revision,binding_state,binding_subject_digest,"
                            "revoked_at FROM oa_session_credentials WHERE tenant_id=:tenant"
                            " AND ai_user_id=:user"
                            " AND target_system=:target AND binding_id=:binding"
                        ),
                        {
                            "tenant": fact.tenant_id,
                            "user": fact.ai_user_id,
                            "target": fact.target_system,
                            "binding": fact.binding_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if (
            row is None
            or row["binding_state"] != "active"
            or row["revoked_at"] is not None
            or row["binding_revision"] != fact.binding_revision
            or row["binding_subject_digest"] != fact.subject_digest
        ):
            raise BrowserAuthorizationError("browser_binding_stale")


class PostgreSQLBrowserCurrentAuth:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: BrowserPayloadCipher,
        session_binder: PrincipalSessionBinder,
        policy: PolicyGuardPort,
        publication_check: PublicationCheck,
    ) -> None:
        self._sessions, self._cipher = session_factory, cipher
        self._binder, self._policy, self._publication = session_binder, policy, publication_check

    def input(self, admission: RunAdmission) -> VerifiedBrowserInput:
        payload = self._cipher.decrypt_input(admission)
        if (
            payload.get("schema_version") != "browser.request.input.v1"
            or not isinstance(payload.get("principal"), dict)
            or not isinstance(payload.get("capability_id"), str)
            or not isinstance(payload.get("arguments"), dict)
            or not isinstance(payload.get("channel"), str)
            or payload.get("channel") not in {"web", "cli", "api", "mock"}
        ):
            raise BrowserAuthorizationError("browser_authorization_evidence_invalid")
        principal = Principal.model_validate(payload["principal"])
        if (
            principal.ai_user_id != admission.owner.user_id
            or principal.org_ctx.tenant_id != admission.owner.tenant_id
            or self._binder.bind(principal, admission.owner.session_id)
            != admission.owner.session_id
        ):
            raise BrowserAuthorizationError("browser_owner_or_binding_mismatch")
        return VerifiedBrowserInput(
            principal, payload["capability_id"], payload["arguments"], payload["channel"],
        )

    async def validate(
        self, session: AsyncSession, admission: RunAdmission, *, require_active: bool = True,
    ) -> VerifiedBrowserInput:
        """Reads only; caller controls transaction/lock order and action fencing."""
        if (
            admission.auth_evidence_version != "verified-session-v1"
            or admission.auth_expires_at <= datetime.now(UTC)
        ):
            raise BrowserAuthorizationError("browser_auth_expired")
        decoded = self.input(admission)
        params = {
            "tenant": admission.owner.tenant_id,
            "user": admission.owner.user_id,
            "session": admission.owner.session_id,
            "fingerprint": admission.auth_fingerprint,
            "target": admission.target_system,
            "binding": admission.binding_id,
        }
        revoked = (
            await session.execute(
                text("SELECT 1 FROM auth_session_revocations WHERE token_fingerprint=:fingerprint"),
                params,
            )
        ).scalar_one_or_none()
        owned_session = (
            await session.execute(
                text("SELECT 1 FROM sessions WHERE tenant_id=:tenant AND session_id=:session"),
                params,
            )
        ).scalar_one_or_none()
        if revoked is not None or owned_session is None:
            raise BrowserAuthorizationError("browser_session_authorization_invalid")
        roles = (
            (
                await session.execute(
                    text(
                        "SELECT role FROM principal_roles WHERE tenant_id=:tenant"
                        " AND ai_user_id=:user"
                    ),
                    params,
                )
            )
            .scalars()
            .all()
        )
        current_roles = sorted(set(decoded.principal.roles).intersection(str(v) for v in roles))
        binding = (
            (
                await session.execute(
                    text(
                        "SELECT binding_revision,binding_state,revoked_at"
                        " FROM oa_session_credentials"
                        " WHERE tenant_id=:tenant AND ai_user_id=:user AND target_system=:target"
                        " AND binding_id=:binding"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            binding is None
            or binding["binding_state"] != "active"
            or binding["revoked_at"] is not None
            or binding["binding_revision"] != admission.binding_revision
        ):
            raise BrowserAuthorizationError("browser_binding_stale")
        capability = await self._publication(
            admission.owner, admission.publication_digest, decoded.capability_id, require_active,
        )
        if (
            capability.type != "query"
            or capability.status != "active"
            or capability.target_system != admission.target_system
            or not capability.binding_required
            or capability.execution_identity != "user_delegated"
        ):
            raise BrowserAuthorizationError("browser_capability_not_readonly")
        context = RequestOrgContext(
            request_id=admission.run_id,
            tenant_id=admission.owner.tenant_id,
            org_id=decoded.principal.org_ctx.org_id,
            department_id=decoded.principal.org_ctx.department_id,
            roles=current_roles,
            channel=decoded.channel,
        )
        decision = await self._policy.decide(
            admission.owner.user_id,
            decoded.capability_id,
            decoded.arguments,
            context,
        )
        if decision.decision != "allow":
            raise BrowserAuthorizationError("browser_policy_denied")
        return decoded

    async def check_current(self, fact: BrowserAuthFact) -> None:
        if fact.evidence_version != "verified-session-v1" or fact.authorization_run_id is None:
            raise BrowserAuthorizationError("browser_authorization_revision_unavailable")
        try:
            async with self._sessions() as session:
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM browser_runs WHERE run_id=:run AND tenant_id=:tenant"
                                " AND ai_user_id=:user AND session_id=:session"
                            ),
                            {
                                "run": fact.authorization_run_id,
                                "tenant": fact.owner.tenant_id,
                                "user": fact.owner.user_id,
                                "session": fact.owner.session_id,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    row is None
                    or row["auth_fingerprint"] != fact.fingerprint
                    or row["auth_expires_at"] != fact.expires_at
                ):
                    raise BrowserAuthorizationError("browser_authorization_evidence_invalid")
                await self.validate(session, admission_from_row(row))
        except BrowserAuthorizationError:
            raise
        except Exception:
            raise BrowserAuthorizationError("browser_authorization_unavailable") from None


class PostgreSQLBrowserRunAuthority:
    """Trusted API admission plus durable worker checks, with explicit cleanup authority."""

    def __init__(
        self,
        *,
        current_auth: PostgreSQLBrowserCurrentAuth,
        verification_check: RunProofCheck | None = None,
        cancel_check: RunProofCheck | None = None,
        cleanup_authorize: CleanupAuthorize | None = None,
        cleanup_check: CleanupCheck | None = None,
    ) -> None:
        self._auth, self._cleanup = current_auth, cleanup_check
        self._verify, self._cancel = verification_check, cancel_check
        self._cleanup_authorize = cleanup_authorize

    @staticmethod
    def _session(transaction: object) -> AsyncSession:
        if not isinstance(transaction, AsyncSession) or not transaction.in_transaction():
            raise BrowserRunStoreError("browser_authority_transaction_required")
        return transaction

    async def check_owner(self, transaction: object, owner: BrowserOwner) -> None:
        session = self._session(transaction)
        captured = authenticated_session.get()
        if captured is not None:
            if (
                captured.principal.ai_user_id != owner.user_id
                or captured.principal.org_ctx.tenant_id != owner.tenant_id
                or captured.expires_at <= datetime.now(UTC)
                or self._auth._binder.bind(captured.principal, owner.session_id) != owner.session_id
            ):
                raise BrowserRunStoreError("browser_owner_authorization_invalid")
            revoked = (
                await session.execute(
                    text(
                        "SELECT 1 FROM auth_session_revocations"
                        " WHERE token_fingerprint=:fingerprint"
                    ),
                    {"fingerprint": captured.fingerprint},
                )
            ).scalar_one_or_none()
            if revoked is not None:
                raise BrowserRunStoreError("browser_session_authorization_invalid")
            return
        # An owner string cannot choose evidence for an arbitrary worker Run.
        # Existing-Run operations use check_run with that exact stored admission.
        raise BrowserRunStoreError("browser_owner_authorization_invalid")

    async def check_admission(
        self,
        transaction: object,
        request: CanonicalRequest,
        admission: RunAdmission,
    ) -> None:
        session = self._session(transaction)
        captured = authenticated_session.get()
        if (
            captured is None
            or request.owner != admission.owner
            or request.task_id != admission.task_id
            or captured.principal.ai_user_id != admission.owner.user_id
            or captured.fingerprint != admission.auth_fingerprint
            or captured.expires_at != admission.auth_expires_at
        ):
            raise BrowserRunStoreError("browser_admission_evidence_invalid")
        decoded = await self._auth.validate(session, admission)
        if decoded.principal != captured.principal:
            raise BrowserRunStoreError("browser_admission_evidence_invalid")

    async def before_run_lock(
        self,
        transaction: object,
        run: RunSnapshot,
        action: RunAction,
    ) -> None:
        session = self._session(transaction)
        if action == "cleanup":
            if self._cleanup_authorize is None:
                raise BrowserRunStoreError("browser_cleanup_authority_unavailable")
            await self._cleanup_authorize(session, run)
        else:
            # Validate the actual candidate before locks. The later check_run
            # repeats current authorization against the locked persisted row.
            await self._check_run_authorization(session, run, action)
        params = {
            "tenant": run.owner.tenant_id,
            "user": run.owner.user_id,
            "session": run.owner.session_id,
            "target": run.admission.target_system,
            "binding": run.admission.binding_id,
        }
        statements = (
            "SELECT binding_id FROM oa_session_credentials WHERE tenant_id=:tenant"
            " AND ai_user_id=:user AND target_system=:target AND binding_id=:binding FOR UPDATE",
            "SELECT lease_epoch FROM browser_binding_leases WHERE tenant_id=:tenant"
            " AND ai_user_id=:user AND target_system=:target AND binding_id=:binding FOR UPDATE",
            "SELECT session_id FROM sessions WHERE tenant_id=:tenant"
            " AND session_id=:session FOR UPDATE",
        )
        try:
            for statement in statements:
                if action == "claim":
                    statement += " NOWAIT"
                await session.execute(text(statement), params)
        except DBAPIError as exc:
            if action == "claim" and getattr(exc.orig, "sqlstate", None) == "55P03":
                raise BrowserRunStoreError("browser_claim_candidate_busy") from None
            raise

    async def _check_run_authorization(
        self, session: AsyncSession, run: RunSnapshot, action: RunAction,
    ) -> None:
        if authenticated_session.get() is not None:
            await self.check_owner(session, run.owner)
        try:
            await self._auth.validate(
                session, run.admission,
                require_active=not (
                    action == "read" and run.status in {"completed", "failed", "cancelled"}
                ),
            )
        except BrowserAuthorizationError as exc:
            # Only explicit, candidate-local negative facts can be skipped.
            # Provider/Policy failures, unavailable keys, corrupt evidence and
            # unknown errors propagate and never become an empty queue.
            if action == "claim" and exc.code in {
                "browser_auth_expired", "browser_session_authorization_invalid",
                "browser_binding_stale", "browser_policy_denied",
            }:
                raise BrowserRunStoreError("browser_claim_candidate_ineligible") from None
            raise

    async def check_run(
        self,
        transaction: object,
        run: RunSnapshot,
        action: RunAction,
    ) -> None:
        session = self._session(transaction)
        if action == "cleanup":
            raise BrowserRunStoreError("browser_cleanup_proof_required")
        await self._check_run_authorization(session, run, action)
        if action == "verify":
            if self._verify is None:
                raise BrowserRunStoreError("browser_verification_proof_unavailable")
            await self._verify(run)
        if action == "cancel" and run.cancel_acknowledged:
            if self._cancel is None:
                raise BrowserRunStoreError("browser_cancellation_proof_unavailable")
            await self._cancel(run)
        # Durable verification and current business authority allow historical
        # projection/recovery; a released browser lease cannot invalidate the
        # already proven value. New browser IO remains fenced separately.
        if action in {
            "read", "claim", "renew", "finalize", "cancel", "capture_settle", "capture_prepare",
            "effect_settle",
        }:
            return
        if run.lease_epoch is None:
            if action in {"verify", "capture"}:
                raise BrowserRunStoreError("browser_lease_required")
            return
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM browser_binding_leases WHERE tenant_id=:tenant"
                        " AND ai_user_id=:user"
                        " AND target_system=:target AND binding_id=:binding"
                    ),
                    {
                        "tenant": run.owner.tenant_id,
                        "user": run.owner.user_id,
                        "target": run.admission.target_system,
                        "binding": run.admission.binding_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        now = cast(datetime, (await session.execute(text("SELECT clock_timestamp()"))).scalar_one())
        if (
            row is None
            or row["state"] != "held"
            or not row["capacity_held"]
            or row["holder_session_id"] != run.owner.session_id
            or row["authorization_revision"] is not None
            or row["authorization_run_id"] != run.run_id
            or row["auth_session_fingerprint"] != run.admission.auth_fingerprint
            or row["auth_expires_at"] != run.admission.auth_expires_at
            or row["binding_revision"] != run.admission.binding_revision
            or row["lease_epoch"] != run.lease_epoch
            or row["provider_key"] != run.provider_key
            or row["deadline"] <= now
        ):
            raise BrowserRunStoreError("browser_lease_stale")

    async def check_cleanup(
        self,
        transaction: object,
        run: RunSnapshot,
        outcome: RunCleanup,
    ) -> None:
        session = self._session(transaction)
        if self._cleanup is None or self._cleanup_authorize is None:
            raise BrowserRunStoreError("browser_cleanup_authority_unavailable")
        await self._cleanup_authorize(session, run)
        await self._cleanup(session, run, outcome)

    async def authorize_cleanup(self, transaction: object, run: RunSnapshot) -> None:
        session = self._session(transaction)
        if self._cleanup_authorize is None:
            raise BrowserRunStoreError("browser_cleanup_authority_unavailable")
        # No earlier-row locks here: the store already took them before Task/Run.
        await self._cleanup_authorize(session, run)
