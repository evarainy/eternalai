"""Explicit, default-off wiring for the single registered READ_ONLY vertical.

Construction performs no database/provider IO, publication or catalog mutation.
Dependencies are trusted server installations, never request DTOs or environment
labels. Persisted keys and real proof implementations are mandatory when enabled.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.chat import BrowserChatService
from app.browser_skill.models import BrowserOwner, OpaqueId
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
    canonical_json,
)
from app.browser_skill.runtime import BrowserReadWorker
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.read_execution import (
    BrowserReadExecutionFactory,
    BrowserReadLifecycle,
    VerifiedBrowserReadExecution,
)
from app.infra.persistence.browser.authorization import (
    CleanupAuthorize,
    CleanupCheck,
    PostgreSQLBrowserBindingReader,
    PostgreSQLBrowserCurrentAuth,
    PostgreSQLBrowserRunAuthority,
    RunProofCheck,
)
from app.infra.persistence.browser.payload_crypto import (
    BrowserPayloadCipher,
    BrowserRunCryptoIdentity,
)
from app.infra.persistence.browser.profiles import PostgreSQLBrowserProfileStore
from app.infra.persistence.browser.publications import PostgreSQLBrowserPublicationStore
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.ports.browser_chat import (
    BrowserChatBindingResolverPort,
    BrowserChatInputIdentity,
    BrowserChatParserPort,
)
from app.ports.browser_profile_store import (
    BrowserProfileCaptureProofPort,
    BrowserProfileCleanupAuthorityPort,
    BrowserProfileCleanupProofPort,
)
from app.ports.browser_publication_store import BrowserPublicationError, PublicationOperation
from app.ports.browser_run_store import BrowserRunStoreError, ProtectedRunEnvelope, RunSnapshot
from app.ports.capability_registry import CapabilitySpec
from app.ports.policy_guard import PolicyGuardPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort
from app.version_binding import capability_version_bindings


class BrowserPublicationGrants(Protocol):
    async def check(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
        publication_digest: str,
    ) -> bool:
        """Verify current server-bound actor/service grant for this exact tenant.

        Read/execute require a current user/service grant too. Owner strings,
        client flags, a digest and a matching request ContextVar are not grants.
        This implementation must consult trusted current local/DB facts only.
        """
        ...


class BrowserRegisteredSourceVerifier(Protocol):
    async def verify(self, manifest: BrowserPublicationManifest) -> bool:
        """Match the installed source, DOM/projector/verifier and effect evidence.

        Verify all frozen definitions against the actual registered execution
        implementation; a declared source kind/origin or digest alone is no proof.
        Bounded local/DB check only; no provider or model IO in transactions.
        """
        ...


class SingleSeedManifestAuthority:
    """One tenant and one complete immutable audited seed, with live grant checks."""

    def __init__(
        self, *, tenant_id: str, seed: BrowserPublicationManifest,
        grants: BrowserPublicationGrants, source_verifier: BrowserRegisteredSourceVerifier,
    ) -> None:
        TypeAdapter(OpaqueId).validate_python(tenant_id, strict=True)
        self._tenant_id = tenant_id
        self._seed = BrowserPublicationManifest.model_validate_json(seed.model_dump_json())
        if not callable(getattr(grants, "check", None)) or not callable(
            getattr(source_verifier, "verify", None)
        ):
            raise ValueError("browser_publication_authority_required")
        self._grants, self._source = grants, source_verifier

    async def authorize(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
    ) -> bool:
        if (owner.tenant_id != self._tenant_id or skill_id != self._seed.skill.skill_id
                or operation not in {"prepare", "activate", "deactivate", "read", "execute"}):
            return False
        detached_owner = BrowserOwner(tenant_id=owner.tenant_id, user_id=owner.user_id,
                                      session_id=owner.session_id)
        return await self._grants.check(
            detached_owner, skill_id, operation, self._seed.digest,
        ) is True

    async def verify_manifest(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> bool:
        checked = BrowserPublicationManifest.model_validate_json(manifest.model_dump_json())
        if owner.tenant_id != self._tenant_id or checked != self._seed:
            return False
        return await self._source.verify(checked) is True


class ActiveBrowserPublications(PostgreSQLBrowserPublicationStore):
    """Frozen reads retain history; execution additionally requires its active head."""

    async def assert_current(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> None:
        await super().assert_current(owner, manifest)
        active = await self.get_active(owner, manifest.skill.skill_id)
        if active is None or active.manifest != manifest:
            raise BrowserPublicationError("browser_publication_inactive")


class BrowserChatPayloadCipher:
    """Infrastructure conversion keeps durable AEAD types out of Chat ports."""

    def __init__(self, cipher: BrowserPayloadCipher) -> None:
        self._cipher = cipher

    def encrypt_input(
        self, identity: BrowserChatInputIdentity, value: Mapping[str, object],
    ) -> ProtectedRunEnvelope:
        return self._cipher.encrypt_input(BrowserRunCryptoIdentity(
            owner=identity.owner, task_id=identity.task_id, run_id=identity.run_id,
            target_system=identity.target_system, binding_id=identity.binding_id,
            binding_revision=identity.binding_revision,
            auth_fingerprint=identity.auth_fingerprint, auth_expires_at=identity.auth_expires_at,
            publication_digest=identity.publication_digest, input_revision=identity.input_revision,
            input_digest=identity.input_digest,
            auth_evidence_version=identity.auth_evidence_version,
        ), value)

    def decrypt_result(self, run: RunSnapshot) -> dict[str, Any]:
        return self._cipher.decrypt_result(run)


@dataclass(frozen=True, slots=True, repr=False)
class BrowserVerticalDependencies:
    """Trusted explicit installation. Every provider/proof dependency is mandatory.

    Keyrings must be durable, retain historical IDs and be supplied by the existing
    secret authority. No startup key generation, environment loading or rotation.
    Lifecycle cancellation/cleanup callbacks must verify exact provider facts;
    never inject an empty coroutine or infer proof from requested outcome enums.
    """

    session_factory: async_sessionmaker[AsyncSession]
    capability_registry: PostgreSQLCapabilityRegistry
    session_binder: PrincipalSessionBinder
    policy: PolicyGuardPort
    tenant_id: str
    seed: BrowserPublicationManifest
    publication_grants: BrowserPublicationGrants
    source_verifier: BrowserRegisteredSourceVerifier
    payload_keys: Mapping[str, bytes] = field(repr=False)
    active_payload_key_id: str
    request_digest_keys: Mapping[str, bytes] = field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: bytes = field(repr=False)
    result_digest_key: bytes = field(repr=False)
    execution_factory: BrowserReadExecutionFactory
    lifecycle: BrowserReadLifecycle
    profile_capture_proof: BrowserProfileCaptureProofPort
    profile_cleanup_proof: BrowserProfileCleanupProofPort
    profile_cleanup_authority: BrowserProfileCleanupAuthorityPort
    cancel_check: RunProofCheck
    cleanup_authorize: CleanupAuthorize
    cleanup_check: CleanupCheck
    chat_parser: BrowserChatParserPort
    chat_bindings: BrowserChatBindingResolverPort
    trace: TracePort
    sessions: SessionStorePort
    worker_id: str
    enabled: bool = False
    worker_ttl_seconds: int = 60


@dataclass(slots=True, repr=False)
class _OwnerScan:
    """Finite scheduling cycle, never an owner authorization cache."""

    upper: tuple[str, str] | None = None
    after: tuple[str, str] | None = None
    cutoff: datetime | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True, slots=True, repr=False)
class BrowserVerticalComponents:
    """Explicit service references; installing routes/scheduling is the caller's job."""

    publications: ActiveBrowserPublications
    cipher: BrowserPayloadCipher
    current_auth: PostgreSQLBrowserCurrentAuth
    binding_reader: PostgreSQLBrowserBindingReader
    profiles: PostgreSQLBrowserProfileStore
    runs: PostgreSQLBrowserRunStore
    execution: VerifiedBrowserReadExecution
    worker: BrowserReadWorker
    chat: BrowserChatService
    _sessions: async_sessionmaker[AsyncSession]
    _tenant_id: str
    _seed: BrowserPublicationManifest
    _owner_scan: _OwnerScan = field(default_factory=_OwnerScan, compare=False)

    async def prepare_seed(self, owner: BrowserOwner) -> BrowserPublicationRecord:
        """Explicit service operation; constructing components never calls this."""
        return await self.publications.prepare(owner, self._seed)

    async def activate_seed(self, owner: BrowserOwner) -> BrowserPublicationRecord:
        """Explicit fixed-digest activation after trusted operator authorization."""
        return await self.publications.activate(
            owner, self._seed.skill.skill_id, self._seed.digest, expected_revision=0,
        )

    async def run_ready(self, *, maximum_owners: int = 16) -> tuple[RunSnapshot, ...]:
        """One bounded supervisor pass over durable owner identities, no implicit loop.

        The database supplies owners, never a client owner list. Worker claim/current
        checks still verify each exact protected Run and its current authorization.
        Closing this discovery transaction precedes all worker/provider operations.
        """
        if type(maximum_owners) is not int or not 1 <= maximum_owners <= 64:
            raise ValueError("browser_worker_batch_invalid")
        eligible = (
            "tenant_id=:tenant AND publication_digest=:publication"
            " AND status IN ('running','waiting_user')"
            " AND (worker_deadline IS NULL OR worker_deadline<=clock_timestamp())"
            " AND created_at<=:cycle_time"
        )
        params: dict[str, object] = {
            "tenant": self._tenant_id, "publication": bytes.fromhex(self._seed.digest),
            "limit": maximum_owners,
        }
        scan = self._owner_scan
        # Serialize only discovery/cursor movement, never provider execution. The
        # upper key is fixed for the cycle so arriving owners cannot defer wrap.
        async with scan.lock, self._sessions() as session:
            if scan.upper is None:
                scan.cutoff = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
                params["cycle_time"] = scan.cutoff
                last = (await session.execute(text(
                    "SELECT ai_user_id,session_id FROM browser_runs WHERE " + eligible
                    + " GROUP BY ai_user_id,session_id"
                    " ORDER BY ai_user_id DESC,session_id DESC LIMIT 1",
                ), params)).mappings().one_or_none()
                if last is not None:
                    scan.upper = (last["ai_user_id"], last["session_id"])
            params["cycle_time"] = scan.cutoff
            rows = []
            if scan.upper is not None:
                params.update(upper_user=scan.upper[0], upper_session=scan.upper[1])
                after_clause = ""
                if scan.after is not None:
                    params.update(after_user=scan.after[0], after_session=scan.after[1])
                    after_clause = " AND (ai_user_id,session_id)>(:after_user,:after_session)"
                rows = list((await session.execute(text(
                    "SELECT tenant_id,ai_user_id,session_id FROM browser_runs WHERE " + eligible
                    + " AND (ai_user_id,session_id)<=(:upper_user,:upper_session)" + after_clause
                    + " GROUP BY tenant_id,ai_user_id,session_id"
                    " ORDER BY ai_user_id,session_id LIMIT :limit",
                ), params)).mappings().all())
                if rows:
                    scan.after = (rows[-1]["ai_user_id"], rows[-1]["session_id"])
                if not rows or scan.after == scan.upper:
                    scan.after = scan.upper = None
                    scan.cutoff = None
        completed: list[RunSnapshot] = []
        failed_owners = 0
        for row in rows:
            owner = BrowserOwner(tenant_id=row["tenant_id"], user_id=row["ai_user_id"],
                                 session_id=row["session_id"])
            try:
                result = await self.worker.run_next(owner)
            except Exception:
                # One revoked/stale owner must not block unrelated authorized
                # owners. Report the pass failure without retaining identity,
                # exception details, protected payloads or claiming success.
                failed_owners += 1
                continue
            if result is not None:
                completed.append(result)
        # Historical cleanup has independent service authority. A revoked or
        # expired business session must not prevent exact resource reconciliation.
        async with self._sessions() as session:
            cleanup_rows = (await session.execute(text(
                "SELECT br.tenant_id,br.ai_user_id,br.session_id,br.task_id,br.run_id"
                " FROM browser_runs br WHERE br.tenant_id=:tenant"
                " AND br.publication_digest=:publication"
                " AND br.cleanup NOT IN ('released','terminated')"
                " AND (br.status IN ('completed','failed','cancelled') OR"
                " ((br.worker_deadline IS NULL OR br.worker_deadline<=clock_timestamp())"
                " AND (br.auth_expires_at<=clock_timestamp() OR EXISTS"
                " (SELECT 1 FROM auth_session_revocations rv"
                " WHERE rv.token_fingerprint=br.auth_fingerprint) OR NOT EXISTS"
                " (SELECT 1 FROM oa_session_credentials c WHERE c.tenant_id=br.tenant_id"
                " AND c.ai_user_id=br.ai_user_id AND c.target_system=br.target_system"
                " AND c.binding_id=br.binding_id AND c.binding_revision=br.binding_revision"
                " AND c.binding_state='active' AND c.revoked_at IS NULL))))"
                " ORDER BY br.updated_at,br.run_id LIMIT :limit"
            ), {"tenant": self._tenant_id, "publication": bytes.fromhex(self._seed.digest),
                "limit": maximum_owners})).mappings().all()
        for row in cleanup_rows:
            owner = BrowserOwner(tenant_id=row["tenant_id"], user_id=row["ai_user_id"],
                                 session_id=row["session_id"])
            try:
                historical = await self.runs.get_for_cleanup(owner, row["task_id"], row["run_id"])
                await self.worker.cleanup(historical)
            except Exception:
                failed_owners += 1
        if failed_owners:
            raise BrowserRunStoreError("browser_worker_pass_failed")
        return tuple(completed)


def _validate_dependencies(deps: BrowserVerticalDependencies) -> None:
    if (not isinstance(deps.capability_registry, PostgreSQLCapabilityRegistry)
            or deps.capability_registry._session_factory is not deps.session_factory
            or not isinstance(deps.session_binder, PrincipalSessionBinder)):
        raise ValueError("browser_vertical_authority_configuration_invalid")
    TypeAdapter(OpaqueId).validate_python(deps.tenant_id, strict=True)
    for key in (deps.input_digest_key, deps.result_digest_key):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("browser_vertical_digest_key_invalid")
    required = (
        (deps.publication_grants, "check"), (deps.source_verifier, "verify"),
        (deps.policy, "decide"), (deps.execution_factory, "open"),
        (deps.lifecycle, "lookup_capture"), (deps.lifecycle, "send_capture"),
        (deps.lifecycle, "stop"), (deps.lifecycle, "cleanup"),
        (deps.profile_capture_proof, "verify_capture"),
        (deps.profile_capture_proof, "check_live_subject"),
        (deps.profile_cleanup_proof, "verify_cleanup"),
        (deps.profile_cleanup_authority, "check_cleanup"),
        (deps.chat_parser, "parse"), (deps.chat_bindings, "resolve"),
        (deps.sessions, "get_session"), (deps.sessions, "create_session"),
    )
    if (any(not callable(getattr(dependency, method, None)) for dependency, method in required)
            or not all(callable(callback) for callback in (
                deps.cancel_check, deps.cleanup_authorize, deps.cleanup_check,
            )) or deps.trace is None):
        raise ValueError("browser_vertical_trusted_dependency_required")


def build_browser_vertical(
    dependencies: BrowserVerticalDependencies | None = None,
) -> BrowserVerticalComponents | None:
    """Return configured services only after explicit enablement; default is absent.

    Does not register/advertise a Capability, prepare/activate a seed, launch a
    worker or contact any provider. Missing real dependencies fail construction.
    """
    if dependencies is None:
        return None
    if type(dependencies.enabled) is not bool:
        raise ValueError("browser_vertical_enablement_invalid")
    if not dependencies.enabled:
        return None
    deps = dependencies
    _validate_dependencies(deps)
    seed = BrowserPublicationManifest.model_validate_json(deps.seed.model_dump_json())
    authority = SingleSeedManifestAuthority(
        tenant_id=deps.tenant_id, seed=seed, grants=deps.publication_grants,
        source_verifier=deps.source_verifier,
    )
    publications = ActiveBrowserPublications(
        deps.session_factory, deps.capability_registry, authority,
    )
    cipher = BrowserPayloadCipher(dict(deps.payload_keys), active_key_id=deps.active_payload_key_id)

    async def publication_check(
        owner: BrowserOwner, digest: bytes, capability_id: str, require_active: bool,
    ) -> CapabilitySpec:
        if (type(digest) is not bytes or len(digest) != 32
                or not hmac.compare_digest(digest, bytes.fromhex(seed.digest))
                or capability_id != seed.capability.capability_id
                or type(require_active) is not bool):
            raise BrowserPublicationError("browser_publication_reference_invalid")
        if require_active:
            await publications.assert_current(owner, seed)
        else:
            historical = await publications.get_frozen(owner, seed.digest)
            if historical is None or historical.manifest != seed:
                raise BrowserPublicationError("browser_publication_reference_invalid")
        current = await deps.capability_registry.get(capability_id)
        if (current is None or current.status != "active"
                or canonical_json(current.model_dump(mode="json")) != seed.capability_snapshot_json
                or capability_version_bindings(current) != seed.capability_bindings):
            raise BrowserPublicationError("browser_publication_capability_changed")
        # CurrentAuth evaluates current roles and Policy with protected arguments
        # immediately after this check; the snapshot itself never grants access.
        return current

    current_auth = PostgreSQLBrowserCurrentAuth(
        session_factory=deps.session_factory, cipher=cipher, session_binder=deps.session_binder,
        policy=deps.policy, publication_check=publication_check,
    )
    binding_reader = PostgreSQLBrowserBindingReader(deps.session_factory)
    profiles = PostgreSQLBrowserProfileStore(
        session_factory=deps.session_factory, session_binder=deps.session_binder,
        current_auth=current_auth, binding_reader=binding_reader,
        capture_proof=deps.profile_capture_proof, cleanup_proof=deps.profile_cleanup_proof,
        cleanup_authority=deps.profile_cleanup_authority,
    )
    execution = VerifiedBrowserReadExecution(
        deps.execution_factory, deps.lifecycle, publications, cipher,
        result_digest_key=deps.result_digest_key,
    )
    run_authority = PostgreSQLBrowserRunAuthority(
        current_auth=current_auth, verification_check=execution.check_verified_candidate,
        cancel_check=deps.cancel_check, cleanup_authorize=deps.cleanup_authorize,
        cleanup_check=deps.cleanup_check,
    )
    runs = PostgreSQLBrowserRunStore(
        session_factory=deps.session_factory, authority=run_authority,
        digest_keys=dict(deps.request_digest_keys),
        active_digest_key_id=deps.active_request_digest_key_id,
    )
    worker = BrowserReadWorker(
        runs, execution, profiles, worker_id=deps.worker_id, enabled=True,
        ttl_seconds=deps.worker_ttl_seconds,
    )
    chat = BrowserChatService(
        runs, publications, deps.chat_parser, deps.chat_bindings, BrowserChatPayloadCipher(cipher),
        deps.session_binder.bind, deps.trace, sessions=deps.sessions,
        skill_id=seed.skill.skill_id, input_digest_key=deps.input_digest_key, enabled=True,
    )
    return BrowserVerticalComponents(
        publications, cipher, current_auth, binding_reader, profiles, runs, execution,
        worker, chat, deps.session_factory, deps.tenant_id, seed,
    )
