"""Concrete operator installation for the one isolated synthetic browser route.

Explicit console invocation supplies existing secrets and already issued tokens.
Importing this module performs no IO. Nothing issues tokens, grants roles, writes
registration data, publishes a seed or discovers credentials from files or env.
The API and worker use the same fixed PostgreSQL queue and persisted key material.
"""

from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import importlib.util
import sys
import warnings
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.request import ProxyHandler, build_opener

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import BrowserPublicationManifest, canonical_json
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.auth.session_revocations import PostgreSQLSessionRevocationStore
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_DETAIL_CAPABILITY_ID,
    SYNTHETIC_TENANT,
    SYNTHETIC_USER,
    FixedSyntheticSource,
    build_fixed_synthetic_query_source,
)
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    PostgreSQLLocalCleanupGrant,
    build_local_browser_vertical,
)
from app.infra.browser.local_resource_lifecycle import LocalChromiumDeployment, local_subject_digest
from app.infra.browser.openrouter_jev import open_openrouter_jev, prompt_openrouter_key
from app.infra.browser.synthetic_configuration import (
    BINDING_ID,
    CLEANUP_ACTOR,
    CLEANUP_ROLE,
    DATABASE_URL,
    PROVIDER_ID,
    PUBLICATION_ACTOR,
    PUBLICATION_ROLE,
    SyntheticDeactivationBundle,
    SyntheticOperatorBundle,
    prompt_database_password,
    synthetic_jev_manifest,
)
from app.infra.browser.systemone_http import DecisionDeployment
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.infra.persistence.browser.leases import BrowserProviderPool, BrowserProviderPoolRegistry
from app.infra.persistence.browser.publications import PostgreSQLBrowserPublicationStore
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.persistence.task_store.postgresql import PostgreSQLSessionStore
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.auth import VerifiedSessionToken
from app.ports.browser_publication_store import BrowserPublicationError, PublicationOperation
from app.ports.browser_store import BrowserBindingKey
from app.ports.credential_vault import BrowserBindingFact

_BROWSERS = Path("/ms-playwright")
_SYNTHETIC_SKILL = "fixed_synthetic_message_detail"


def prompt_deactivation_bundle(
    encrypted_path: Path | None = None,
) -> SyntheticDeactivationBundle:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("browser_operator_private_console_required")
    if encrypted_path is not None:
        from app.infra.browser.synthetic_vault import DEACTIVATION_FILE, read_encrypted

        try:
            return SyntheticDeactivationBundle.model_validate(
                read_encrypted(encrypted_path, expected_name=DEACTIVATION_FILE)
            )
        except Exception:
            raise ValueError("browser_operator_bundle_invalid") from None
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass("Existing publication deactivation authority JSON (hidden): ")
            if len(value) > 32768:
                raise ValueError
            return SyntheticDeactivationBundle.model_validate_json(value)
        except Exception:
            raise ValueError("browser_operator_bundle_invalid") from None


def prompt_operator_bundle(encrypted_path: Path | None = None) -> SyntheticOperatorBundle:
    """Only a user's own TTY; never call from assistant tools or web requests."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("browser_operator_private_console_required")
    if encrypted_path is not None:
        from app.infra.browser.synthetic_vault import OPERATOR_FILE, read_encrypted

        try:
            return SyntheticOperatorBundle.model_validate(
                read_encrypted(encrypted_path, expected_name=OPERATOR_FILE)
            )
        except Exception:
            raise ValueError("browser_operator_bundle_invalid") from None
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass("Existing synthetic operator secret bundle JSON (hidden): ")
            if len(value) > 131072:
                raise ValueError
            return SyntheticOperatorBundle.model_validate_json(value)
        except Exception:
            raise ValueError("browser_operator_bundle_invalid") from None


def _key(value: SecretStr, *, exact: bool = True) -> bytes:
    try:
        decoded = base64.b64decode(value.get_secret_value(), validate=True)
        if (exact and len(decoded) != 32) or (not exact and len(decoded) < 32):
            raise ValueError
        return decoded
    except Exception:
        raise ValueError("browser_operator_key_invalid") from None


def _keyring(values: dict[str, SecretStr], active: str) -> dict[str, bytes]:
    if not values or len(values) > 16 or active not in values:
        raise ValueError("browser_operator_keyring_invalid")
    return {key: _key(value) for key, value in values.items()}


def _file_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


async def image_deployment(source: FixedSyntheticSource) -> LocalChromiumDeployment:
    """Locate code assets, query the installed executable path, and hash exact files.

    The short Node process only queries chromium.executablePath(); it does not
    launch Chromium or inherit the operator process environment or secret bundle.
    The execution resource manager rechecks these hashes before actual launch.
    """
    spec = importlib.util.find_spec("playwright")
    if spec is None or spec.origin is None or sys.platform != "linux":
        raise ValueError("browser_operator_image_invalid")
    root = Path(spec.origin).resolve().parent
    node, package = root / "driver" / "node", root / "driver" / "package"
    helper = Path(__file__).with_name("managed_chromium.js")
    # Only an installed, code-owned package can select the browser executable.
    process = await asyncio.create_subprocess_exec(
        str(node), "-e",
        "process.stdout.write(require(process.argv[1]).chromium.executablePath())",
        str(package), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={"PLAYWRIGHT_BROWSERS_PATH": str(_BROWSERS), "PATH": str(node.parent)},
        limit=8192,
    )
    try:
        async with asyncio.timeout(5):
            assert process.stdout is not None
            output = await process.stdout.read(8193)
            if len(output) > 8192 or await process.wait() != 0:
                raise ValueError
        chromium = Path(output.decode("utf-8")).resolve(strict=True)
        if not chromium.is_relative_to(_BROWSERS.resolve(strict=True)):
            raise ValueError
        node_hash, browser_hash, helper_hash = (
            _file_digest(node), _file_digest(chromium), _file_digest(helper),
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise ValueError("browser_operator_image_invalid") from None
    manifest = hashlib.sha256(canonical_json({
        "provider": PROVIDER_ID, "source": source.manifest.site.source.model_dump(mode="json"),
        "node": node_hash.hex(), "chromium": browser_hash.hex(), "helper": helper_hash.hex(),
        "playwright": "1.63.0", "contract": "local_synthetic_read_only_v1",
    }).encode("utf-8")).digest()
    return LocalChromiumDeployment(
        provider_key=PROVIDER_ID, manifest_digest=manifest, node_executable=node,
        playwright_package=package, browsers_path=_BROWSERS, helper_digest=helper_hash,
        node_digest=node_hash, chromium_digest=browser_hash, enabled=True,
    )


async def _current_publication_actor(
    session: AsyncSession, binder: PrincipalSessionBinder, actor: VerifiedSessionToken,
    bound_session: str,
) -> None:
    if (actor.principal.ai_user_id != PUBLICATION_ACTOR
            or actor.principal.org_ctx.tenant_id != SYNTHETIC_TENANT
            or PUBLICATION_ROLE not in actor.principal.roles
            or binder.bind(actor.principal, bound_session) != bound_session):
        raise BrowserPublicationError("browser_operator_publication_denied")
    present = (await session.execute(text(
        "SELECT 1 WHERE :expires > clock_timestamp()"
        " AND EXISTS (SELECT 1 FROM principal_roles WHERE tenant_id=:tenant"
        " AND ai_user_id=:actor AND role=:role)"
        " AND EXISTS (SELECT 1 FROM sessions WHERE tenant_id=:tenant AND session_id=:session)"
        " AND NOT EXISTS (SELECT 1 FROM auth_session_revocations"
        " WHERE token_fingerprint=:fingerprint)"
    ), {"expires": actor.expires_at, "tenant": SYNTHETIC_TENANT,
        "actor": PUBLICATION_ACTOR, "role": PUBLICATION_ROLE, "session": bound_session,
        "fingerprint": actor.fingerprint})).scalar_one_or_none()
    if present != 1:
        raise BrowserPublicationError("browser_operator_publication_denied")


class SyntheticPublicationGrants:
    """Existing signed operator plus current DB role, exact fixed source and owner."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], binder: PrincipalSessionBinder,
        actor: VerifiedSessionToken, business_actor: VerifiedSessionToken,
        source: FixedSyntheticSource,
    ) -> None:
        if (actor.principal.ai_user_id != PUBLICATION_ACTOR
                or actor.principal.org_ctx.tenant_id != SYNTHETIC_TENANT
                or PUBLICATION_ROLE not in actor.principal.roles):
            raise ValueError("browser_operator_publication_authority_invalid")
        if (business_actor.principal.ai_user_id != SYNTHETIC_USER
                or business_actor.principal.org_ctx.tenant_id != SYNTHETIC_TENANT):
            raise ValueError("browser_operator_business_authority_invalid")
        self._sessions, self._actor, self._source = sessions, actor, source
        self._session = binder.bind(actor.principal, PUBLICATION_ACTOR)
        self._binder = binder
        self._business_actor = business_actor
        self.owner = BrowserOwner(
            tenant_id=SYNTHETIC_TENANT, user_id=SYNTHETIC_USER,
            session_id=binder.bind(business_actor.principal, SYNTHETIC_USER),
        )

    async def current_business(self, session: AsyncSession) -> None:
        actor = self._business_actor
        if self._binder.bind(actor.principal, self.owner.session_id) != self.owner.session_id:
            raise BrowserPublicationError("browser_operator_business_denied")
        present = (await session.execute(text(
            "SELECT 1 WHERE :expires > clock_timestamp()"
            " AND EXISTS (SELECT 1 FROM sessions WHERE tenant_id=:tenant AND session_id=:session)"
            " AND NOT EXISTS (SELECT 1 FROM auth_session_revocations"
            " WHERE token_fingerprint=:fingerprint)"
        ), {"expires": actor.expires_at, "tenant": SYNTHETIC_TENANT,
            "session": self.owner.session_id,
            "fingerprint": actor.fingerprint})).scalar_one_or_none()
        if present != 1:
            raise BrowserPublicationError("browser_operator_business_denied")

    async def current(self, session: AsyncSession) -> None:
        await _current_publication_actor(session, self._binder, self._actor, self._session)

    async def check(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
        publication_digest: str,
    ) -> bool:
        if (owner.tenant_id != SYNTHETIC_TENANT
                or skill_id != self._source.manifest.skill.skill_id
                or publication_digest != self._source.manifest.digest):
            return False
        if operation in {"prepare", "activate", "deactivate"}:
            if owner != self.owner:
                return False
        elif operation in {"read", "execute"}:
            if owner.user_id != SYNTHETIC_USER:
                return False
        else:
            return False
        async with self._sessions() as session:
            await self.current(session)
            if operation in {"prepare", "activate", "deactivate"}:
                await self.current_business(session)
        return True


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticOperatorComponents:
    vertical: BrowserVerticalComponents
    tokens: HMACSessionToken
    binder: PrincipalSessionBinder
    revocations: PostgreSQLSessionRevocationStore
    publication_owner: BrowserOwner


async def _preflight(
    sessions: async_sessionmaker[AsyncSession], registry: PostgreSQLCapabilityRegistry,
    source: FixedSyntheticSource, grants: SyntheticPublicationGrants,
    cleanup: PostgreSQLLocalCleanupGrant, *, require_active_publication: bool,
) -> BrowserBindingFact:
    expected = source.manifest.capability
    registered = await registry.get(expected.capability_id)
    if registered is None or registered != expected:
        raise ValueError("browser_operator_capability_registration_required")
    async with sessions() as session:
        await grants.current(session)
        await grants.current_business(session)
        await cleanup._current(session)
        binding = (await session.execute(text(
            "SELECT binding_revision,binding_subject_digest FROM oa_session_credentials"
            " WHERE tenant_id=:tenant AND ai_user_id=:user AND target_system='oa'"
            " AND binding_id=:binding AND binding_state='active' AND revoked_at IS NULL"
        ), {"tenant": SYNTHETIC_TENANT, "user": SYNTHETIC_USER,
            "binding": BINDING_ID})).mappings().one_or_none()
        capacity = (await session.execute(text(
            "SELECT tenant_id,max_active FROM browser_capacity_limits"
            " WHERE provider_key=:provider AND (tenant_id IS NULL OR tenant_id=:tenant)"
        ), {"provider": PROVIDER_ID, "tenant": SYNTHETIC_TENANT})).mappings().all()
        if require_active_publication:
            active = (await session.execute(text(
                "SELECT 1 FROM browser_publications WHERE tenant_id=:tenant"
                " AND skill_id=:skill AND publication_digest=:digest AND state='active'"
            ), {"tenant": SYNTHETIC_TENANT, "skill": source.manifest.skill.skill_id,
                "digest": bytes.fromhex(source.manifest.digest)})).scalar_one_or_none()
            if active != 1:
                raise ValueError("browser_operator_active_publication_required")
    if (binding is None
            or binding["binding_subject_digest"] != local_subject_digest(SYNTHETIC_TENANT,
                                                                         SYNTHETIC_USER)
            or {row["tenant_id"] for row in capacity} != {None, SYNTHETIC_TENANT}
            or any(type(row["max_active"]) is not int or row["max_active"] != 1
                   for row in capacity)):
        raise ValueError("browser_operator_binding_capacity_registration_required")
    return BrowserBindingFact(
        SYNTHETIC_TENANT, SYNTHETIC_USER, "oa", BINDING_ID,
        binding["binding_revision"], bytes(binding["binding_subject_digest"]),
    )


@asynccontextmanager
async def open_synthetic_operator(
    bundle: SyntheticOperatorBundle, *, jev_key: SecretStr, enabled: bool = False,
    require_active_publication: bool = True,
    input_mode: Literal["chat", "structured"] = "chat",
    attempt_guard: Callable[[], None] | None = None,
) -> AsyncIterator[SyntheticOperatorComponents]:
    """Read-only installation/preflight; caller explicitly serves API or starts worker.

    Registration/provisioning is intentionally not inferred from possession of a
    signing key. Missing existing signed operator/service authority fails closed.
    """
    if (enabled is not True or type(require_active_publication) is not bool
            or input_mode not in {"chat", "structured"}):
        raise ValueError("browser_operator_disabled")
    if bundle.jev_manifest != synthetic_jev_manifest():
        raise ValueError("browser_operator_manifest_invalid")
    tokens = HMACSessionToken(signing_key=_key(bundle.session_signing_key, exact=False),
                              ttl_seconds=3600, tenant_id=SYNTHETIC_TENANT)
    binder = PrincipalSessionBinder(binding_key=_key(bundle.session_binding_key, exact=False))
    publication_actor = tokens.inspect(bundle.publication_token.get_secret_value())
    cleanup_actor = tokens.inspect(bundle.cleanup_token.get_secret_value())
    business_actor = tokens.inspect(bundle.business_token.get_secret_value())
    if cleanup_actor.principal.ai_user_id != CLEANUP_ACTOR:
        raise ValueError("browser_operator_cleanup_authority_invalid")
    source = build_fixed_synthetic_query_source(bundle.jev_manifest)
    deployment = await image_deployment(source)
    engine = create_async_engine(
        DATABASE_URL, echo=False, hide_parameters=True,
        connect_args={"connect_timeout": 5, "application_name": "browser_synthetic_operator",
                      "password": prompt_database_password().get_secret_value(),
                      "options": "-c statement_timeout=5000 -c lock_timeout=1000"},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    registry = PostgreSQLCapabilityRegistry(sessions)
    grants = SyntheticPublicationGrants(sessions, binder, publication_actor, business_actor, source)
    cleanup = PostgreSQLLocalCleanupGrant(
        session_factory=sessions, session_binder=binder, verified_actor=cleanup_actor,
        bound_session=binder.bind(cleanup_actor.principal, CLEANUP_ACTOR),
        binding=BrowserBindingKey(SYNTHETIC_TENANT, SYNTHETIC_USER, "oa", BINDING_ID),
        publication_digest=source.manifest.digest, required_role=CLEANUP_ROLE,
        allowed_operations=frozenset({"recover", "cleanup"}),
    )
    try:
        binding = await _preflight(
            sessions, registry, source, grants, cleanup,
            require_active_publication=require_active_publication,
        )
        decision_deployment = DecisionDeployment(
            disposition="cloud_synthetic", endpoint_origin="https://openrouter.ai",
            manifest_digest=bundle.jev_manifest.manifest_digest,
            registered_sources=(source.manifest.site.source,),
        )
        async with open_openrouter_jev(manifest=bundle.jev_manifest,
                                      deployment=decision_deployment, api_key=jev_key,
                                      attempt_guard=attempt_guard) as decision:
            vertical = build_local_browser_vertical(LocalBrowserInstallationDependencies(
                session_factory=sessions, capability_registry=registry, session_binder=binder,
                policy=MinimalPolicyGuard(), source=source, binding=binding, deployment=deployment,
                decision=decision, provider_pools=BrowserProviderPoolRegistry((BrowserProviderPool(
                    PROVIDER_ID, (), deployment.manifest_digest,
                ),)), proof_context=BrowserClaimProofContext(_key(bundle.proof_context_key)),
                publication_grants=grants, cleanup_authority=cleanup,
                cleanup_authorize=cleanup.authorize_run,
                payload_keys=_keyring(bundle.payload_keys, bundle.active_payload_key_id),
                active_payload_key_id=bundle.active_payload_key_id,
                resource_keys=_keyring(bundle.resource_keys, bundle.active_resource_key_id),
                active_resource_key_id=bundle.active_resource_key_id,
                request_digest_keys=_keyring(bundle.request_digest_keys,
                                             bundle.active_request_digest_key_id),
                active_request_digest_key_id=bundle.active_request_digest_key_id,
                input_digest_key=_key(bundle.input_digest_key),
                result_digest_key=_key(bundle.result_digest_key),
                llm_provider=OpenAICompatibleLLMProvider(
                    base_url="http://34.74.11.38:8011/v1", timeout_seconds=20,
                    max_tokens=2048, temperature=0.6, top_p=0.95, top_k=20,
                    enable_thinking=False, opener=build_opener(ProxyHandler({})),
                ) if input_mode == "chat" else None,
                structured_output=JSONStructuredOutputProvider() if input_mode == "chat" else None,
                intent_model="glm-4.7" if input_mode == "chat" else None,
                input_mode=input_mode,
                trace=PostgreSQLTraceWriter(sessions), sessions=PostgreSQLSessionStore(sessions),
                worker_id="browser_fixture_worker", enabled=True,
            ))
            if vertical is None:
                raise ValueError("browser_operator_installation_unavailable")
            yield SyntheticOperatorComponents(
                vertical, tokens, binder, PostgreSQLSessionRevocationStore(sessions),
                grants.owner,
            )
    finally:
        await engine.dispose()


@asynccontextmanager
async def worker_components() -> AsyncIterator[BrowserVerticalComponents]:
    """Concrete --enable --factory target; only invokes the user's hidden console."""
    bundle = prompt_operator_bundle()
    key = prompt_openrouter_key()
    async with open_synthetic_operator(
        bundle, jev_key=key, enabled=True, input_mode="structured"
    ) as components:
        yield components.vertical


@asynccontextmanager
async def vault_worker_components() -> AsyncIterator[BrowserVerticalComponents]:
    """Named --factory selection only; explicit task vault and hidden Jev input."""
    from app.infra.browser.synthetic_vault import OPERATOR_FILE, VAULT_DIRECTORY

    bundle = prompt_operator_bundle(encrypted_path=VAULT_DIRECTORY / OPERATOR_FILE)
    key = prompt_openrouter_key()
    async with open_synthetic_operator(
        bundle, jev_key=key, enabled=True, input_mode="structured"
    ) as components:
        yield components.vertical


class _DeactivationAuthority:
    """Independent current actor can only disable this exact tenant/skill history."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], binder: PrincipalSessionBinder,
        actor: VerifiedSessionToken,
    ) -> None:
        self._sessions, self._binder, self._actor = sessions, binder, actor
        self.owner = BrowserOwner(
            tenant_id=actor.principal.org_ctx.tenant_id, user_id=actor.principal.ai_user_id,
            session_id=binder.bind(actor.principal, PUBLICATION_ACTOR),
        )

    async def authorize(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
    ) -> bool:
        if owner != self.owner or skill_id != _SYNTHETIC_SKILL or operation != "deactivate":
            return False
        async with self._sessions() as session:
            await _current_publication_actor(session, self._binder, self._actor,
                                             self.owner.session_id)
        return True

    async def verify_manifest(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> bool:
        # Existing store deactivation never calls source/current-capability checks.
        # This authority cannot be reused to prepare, activate, read or execute.
        raise BrowserPublicationError("browser_publication_deactivation_only")


async def deactivate_synthetic_publication(
    bundle: SyntheticDeactivationBundle, *, enabled: bool = False,
) -> None:
    """Disable frozen history using only independent live operator authorization.

    Business token expiry, binding/capacity removal, source revocation and provider
    installation failure must not block this operation. The store still locks the
    exact immutable digest and applies its active-state/revision CAS. Its owner key
    is tenant-scoped; the caller here is the real signed operator, never an invented
    business Principal or a freshly issued token.
    """
    if enabled is not True:
        raise ValueError("browser_operator_disabled")
    tokens = HMACSessionToken(signing_key=_key(bundle.session_signing_key, exact=False),
                              ttl_seconds=3600, tenant_id=SYNTHETIC_TENANT)
    binder = PrincipalSessionBinder(binding_key=_key(bundle.session_binding_key, exact=False))
    actor = tokens.inspect(bundle.publication_token.get_secret_value())
    engine = create_async_engine(
        DATABASE_URL, echo=False, hide_parameters=True,
        connect_args={"connect_timeout": 5, "application_name": "browser_synthetic_operator",
                      "password": prompt_database_password().get_secret_value(),
                      "options": "-c statement_timeout=5000 -c lock_timeout=1000"},
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        authority = _DeactivationAuthority(sessions, binder, actor)
        if not await authority.authorize(authority.owner, _SYNTHETIC_SKILL, "deactivate"):
            raise BrowserPublicationError("browser_publication_denied")
        async with sessions() as session:
            row = (await session.execute(text(
                "SELECT manifest,publication_digest,activation_revision FROM browser_publications"
                " WHERE tenant_id=:tenant AND skill_id=:skill AND state='active'"
            ), {"tenant": SYNTHETIC_TENANT, "skill": _SYNTHETIC_SKILL})).mappings().one_or_none()
        if row is None:
            raise BrowserPublicationError("browser_publication_not_found")
        historical = BrowserPublicationManifest.model_validate_json(canonical_json(row["manifest"]))
        if (historical.skill.skill_id != _SYNTHETIC_SKILL
                or historical.capability.capability_id != SYNTHETIC_DETAIL_CAPABILITY_ID
                or bytes(row["publication_digest"]).hex() != historical.digest):
            raise BrowserPublicationError("browser_publication_storage_invalid")
        store = PostgreSQLBrowserPublicationStore(
            sessions, PostgreSQLCapabilityRegistry(sessions), authority,
        )
        await store.deactivate(
            authority.owner, historical.skill.skill_id, historical.digest,
            expected_revision=row["activation_revision"],
        )
    finally:
        await engine.dispose()
