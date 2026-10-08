"""Real RunStore/current-auth SQL with synthetic identities and publication grants.

No provider, capture or production authorization coverage is claimed. The actual
MinimalPolicyGuard query path permits empty roles; we verify its received live
role intersection and test configured Policy denial separately. Fixtures retain
uniquely owned rows: no DELETE, reset, cascade or teardown data mutation.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.browser_skill.models import BrowserOwner
from app.db.config import normalize_database_url
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.persistence.browser.authorization import (
    PostgreSQLBrowserCurrentAuth,
    PostgreSQLBrowserRunAuthority,
)
from app.infra.persistence.browser.payload_crypto import (
    BrowserPayloadCipher,
    BrowserRunCryptoIdentity,
)
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.auth import AuthenticatedSessionContext, Principal, authenticated_session
from app.ports.browser_run_store import BrowserRunStoreError, CanonicalRequest, RunAdmission
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_vault import BrowserAuthFact, BrowserAuthorizationError
from app.ports.policy_guard import PolicyDecision, PolicyRequestContext


class RecordingPolicy(MinimalPolicyGuard):
    """Observe the context but delegate the complete decision to production Policy."""

    def __init__(self) -> None:
        super().__init__()
        self.observed_roles: tuple[str, ...] | None = None
        self.observed_channel: str | None = None

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: PolicyRequestContext,
    ) -> PolicyDecision:
        self.observed_roles = tuple(request_context.roles)
        self.observed_channel = request_context.channel
        return await super().decide(ai_user_id, capability_id, arguments, request_context)


class RunHarness:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(
            normalize_database_url(database_url), hide_parameters=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.suffix = uuid4().hex
        self.tenant = "runpg_" + self.suffix
        self.user = "user_" + self.suffix
        self.binding = "binding_" + self.suffix
        self.principal = Principal(
            ai_user_id=self.user,
            display_name="Synthetic Run Test",
            roles=("reader", "captured_only"),
            org_ctx={"tenant_id": self.tenant},
        )
        self.binder = PrincipalSessionBinder(binding_key=b"synthetic-session-binder-key-0001")
        self.owner = BrowserOwner(
            tenant_id=self.tenant,
            user_id=self.user,
            session_id=self.binder.bind(self.principal, self.suffix),
        )
        self.fingerprint = hashlib.sha256(self.suffix.encode()).digest()
        self.expires = datetime.now(UTC) + timedelta(hours=1)
        self.publication_digest = hashlib.sha256(("publication" + self.suffix).encode()).digest()
        self.capability = CapabilitySpec(
            capability_id="runpg_query_" + self.suffix,
            name="Synthetic query",
            type="query",
            input_schema_digest="input_v1",
            output_schema_digest="output_v1",
            risk_level="low",
            owner="synthetic fixture",
            version="v1",
            status="active",
            short_description="Run DB test",
            target_system="oa",
            execution_identity="user_delegated",
            binding_required=True,
        )
        self.registry = PostgreSQLCapabilityRegistry(self.sessions)
        self.cipher = BrowserPayloadCipher({"fixture": b"p" * 32}, active_key_id="fixture")
        self.policy = RecordingPolicy()
        self.channel: Literal["web", "cli", "api", "mock"] = "web"
        self.auth = PostgreSQLBrowserCurrentAuth(
            session_factory=self.sessions,
            cipher=self.cipher,
            session_binder=self.binder,
            policy=self.policy,
            publication_check=self.publication_check,
        )
        self.authority = PostgreSQLBrowserRunAuthority(current_auth=self.auth)
        self.store = self.make_store({"old": b"o" * 32}, "old")

    def make_store(self, keys: dict[str, bytes], active: str) -> PostgreSQLBrowserRunStore:
        return PostgreSQLBrowserRunStore(
            session_factory=self.sessions,
            authority=self.authority,
            digest_keys=keys,
            active_digest_key_id=active,
        )

    async def publication_check(
        self,
        owner: BrowserOwner,
        digest: bytes,
        capability_id: str,
        require_active: bool,
    ) -> CapabilitySpec:
        # Explicit synthetic source grant, backed by real publication/catalog rows.
        # This tests CurrentAuth consumers, not the publication/source verifier.
        if owner != self.owner or digest != self.publication_digest:
            raise BrowserAuthorizationError("synthetic_publication_grant_denied")
        async with self.sessions() as session:
            state = (
                await session.execute(
                    text(
                        "SELECT state FROM browser_publications WHERE tenant_id=:tenant"
                        " AND publication_digest=:digest AND capability_id=:capability"
                    ),
                    {"tenant": self.tenant, "digest": digest, "capability": capability_id},
                )
            ).scalar_one()
        capability = await self.registry.get(capability_id)
        if capability is None or (require_active and state != "active"):
            raise BrowserAuthorizationError("synthetic_publication_grant_denied")
        return capability

    async def seed(self) -> None:
        await self.registry.create(self.capability)
        async with self.sessions() as session, session.begin():
            params = {
                "tenant": self.tenant,
                "user": self.user,
                "session": self.owner.session_id,
                "binding": self.binding,
                "digest": self.publication_digest,
                "capability": self.capability.capability_id,
            }
            await session.execute(
                text("INSERT INTO sessions(tenant_id,session_id) VALUES(:tenant,:session)"), params
            )
            await session.execute(
                text(
                    "INSERT INTO principal_roles(tenant_id,ai_user_id,role) VALUES"
                    " (:tenant,:user,'reader'),(:tenant,:user,'db_only')"
                ),
                params,
            )
            await session.execute(
                text(
                    "INSERT INTO oa_session_credentials(tenant_id,ai_user_id,"
                    "target_system,binding_id,"
                    "binding_state,binding_subject_digest,binding_subject_verified_at,updated_at)"
                    " VALUES(:tenant,:user,'oa',:binding,'active',:digest,"
                    "clock_timestamp(),clock_timestamp())"
                ),
                params,
            )
            await session.execute(
                text(
                    "INSERT INTO browser_publications(tenant_id,"
                    "publication_digest,skill_id,skill_version,"
                    "capability_id,capability_digest,manifest,state,"
                    "activation_revision,activated_at)"
                    " VALUES(:tenant,:digest,'synthetic_skill','v1',"
                    ":capability,:digest,'{}','active',1,"
                    "clock_timestamp())"
                ),
                params,
            )

    async def count(self, table: str) -> int:
        assert table in {"tasks", "browser_runs"}
        async with self.sessions() as session:
            return int(
                (
                    await session.execute(
                        text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                        {"tenant": self.tenant},
                    )
                ).scalar_one()
            )

    async def request(self) -> CanonicalRequest:
        return await self.store.get_or_create_request(
            self.owner, "request", b'{"message":"synthetic read"}', processing_owner="parser"
        )

    def admission(self, request: CanonicalRequest) -> RunAdmission:
        identity = BrowserRunCryptoIdentity(
            owner=self.owner,
            task_id=request.task_id,
            run_id=uuid4().hex,
            target_system="oa",
            binding_id=self.binding,
            binding_revision=1,
            auth_fingerprint=self.fingerprint,
            auth_expires_at=self.expires,
            publication_digest=self.publication_digest,
            input_revision=1,
            input_digest=hashlib.sha256(b"synthetic input").digest(),
        )
        envelope = self.cipher.encrypt_input(
            identity,
            {
                "schema_version": "browser.request.input.v2",
                "channel": self.channel,
                "principal": self.principal.model_dump(mode="json"),
                "capability_id": self.capability.capability_id,
                "arguments": {},
            },
        )
        return RunAdmission(
            owner=identity.owner,
            task_id=identity.task_id,
            run_id=identity.run_id,
            target_system=identity.target_system,
            binding_id=identity.binding_id,
            binding_revision=identity.binding_revision,
            auth_fingerprint=identity.auth_fingerprint,
            auth_expires_at=identity.auth_expires_at,
            publication_digest=identity.publication_digest,
            input_revision=identity.input_revision,
            input_digest=identity.input_digest,
            protected_input=envelope,
        )


@asynccontextmanager
async def harness(database_url: str) -> AsyncIterator[RunHarness]:
    h = RunHarness(database_url)
    context_token = authenticated_session.set(
        AuthenticatedSessionContext(h.principal, h.fingerprint, h.expires)
    )
    try:
        await h.seed()
        yield h
    finally:
        authenticated_session.reset(context_token)
        # Deliberately retain this unique synthetic tenant; no cleanup authorization.
        print("retained_runpg_fixture_tenant=" + h.tenant)
        await h.engine.dispose()


@pytest.mark.parametrize("channel", ["web", "cli", "api", "mock"])
def test_policy_receives_original_protected_channel(
    migrated_database_url: str, channel: Literal["web", "cli", "api", "mock"],
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            h.channel = channel
            request = await h.request()
            admission = h.admission(request)
            await h.store.accept(request, admission)
            assert h.auth.input(admission).channel == channel
            assert h.policy.observed_channel == channel

    asyncio.run(scenario())


def test_canonical_race_conflict_and_wrong_owner(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            results = await asyncio.gather(h.request(), h.request())
            assert results[0].task_id == results[1].task_id
            assert sum(result.parse_winner for result in results) == 1
            assert await h.count("tasks") == 1
            assert await h.count("browser_runs") == 0
            with pytest.raises(BrowserRunStoreError) as conflict:
                await h.store.get_or_create_request(
                    h.owner, "request", b"changed message", processing_owner="other_parser"
                )
            assert conflict.value.code == "request_key_conflict"
            wrong = h.owner.model_copy(update={"user_id": "other_user"})
            with pytest.raises(BrowserRunStoreError) as owner:
                await h.store.get_or_create_request(
                    wrong, "request", b"changed message", processing_owner="other_parser"
                )
            assert owner.value.code == "browser_owner_authorization_invalid"
            assert await h.count("tasks") == 1
            assert await h.count("browser_runs") == 0

    asyncio.run(scenario())


def test_retry_uses_retained_digest_key_and_missing_key_fails_closed(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            original = await h.request()
            rotated = h.make_store({"old": b"o" * 32, "new": b"n" * 32}, "new")
            retry = await rotated.get_or_create_request(
                h.owner, "request", b'{"message":"synthetic read"}', processing_owner="retry_parser"
            )
            assert retry.task_id == original.task_id and not retry.parse_winner
            assert retry.request_digest_key_id == "old"
            assert retry.request_digest == original.request_digest
            missing = h.make_store({"new": b"n" * 32}, "new")
            with pytest.raises(BrowserRunStoreError) as unavailable:
                await missing.get_or_create_request(
                    h.owner,
                    "request",
                    b'{"message":"synthetic read"}',
                    processing_owner="retry_parser",
                )
            assert unavailable.value.code == "browser_request_digest_key_unavailable"
            assert await h.count("tasks") == 1
            assert await h.count("browser_runs") == 0

    asyncio.run(scenario())


def test_real_current_auth_rechecks_owner_roles_binding_and_revocation(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            request = await h.request()
            admission = h.admission(request)
            await h.store.accept(request, admission)
            persisted = await h.store.get(h.owner, request.task_id, admission.run_id)
            assert persisted.admission == admission
            assert h.cipher.decrypt_input(persisted.admission)[
                "principal"
            ] == h.principal.model_dump(mode="json")
            fact = BrowserAuthFact(
                h.owner,
                None,
                h.fingerprint,
                h.expires,
                authorization_run_id=admission.run_id,
                evidence_version="verified-session-v1",
            )
            await h.auth.check_current(fact)
            assert h.policy.observed_roles == ("reader",)
            with pytest.raises(BrowserAuthorizationError) as wrong_owner:
                await h.auth.check_current(
                    replace(fact, owner=h.owner.model_copy(update={"user_id": "other_user"}))
                )
            assert wrong_owner.value.code == "browser_authorization_evidence_invalid"
            with pytest.raises(BrowserAuthorizationError) as fingerprint:
                await h.auth.check_current(replace(fact, fingerprint=b"x" * 32))
            assert fingerprint.value.code == "browser_authorization_evidence_invalid"
            async with h.sessions() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE principal_roles SET role='removed_role'"
                        " WHERE tenant_id=:tenant AND ai_user_id=:user AND role='reader'"
                    ),
                    {"tenant": h.tenant, "user": h.user},
                )
            await h.auth.check_current(fact)
            assert h.policy.observed_roles == ()  # Real query Policy permits empty roles.
            async with h.sessions() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials"
                        " SET binding_revision=binding_revision+1 WHERE tenant_id=:tenant"
                        " AND ai_user_id=:user AND binding_id=:binding"
                    ),
                    {"tenant": h.tenant, "user": h.user, "binding": h.binding},
                )
            with pytest.raises(BrowserAuthorizationError) as stale:
                await h.auth.check_current(fact)
            assert stale.value.code == "browser_binding_stale"
            async with h.sessions() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO auth_session_revocations"
                        "(token_fingerprint,expires_at) VALUES(:fingerprint,:expires)"
                    ),
                    {"fingerprint": h.fingerprint, "expires": h.expires},
                )
            with pytest.raises(BrowserAuthorizationError) as revoked:
                await h.auth.check_current(fact)
            assert revoked.value.code == "browser_session_authorization_invalid"
            assert await h.count("tasks") == await h.count("browser_runs") == 1

    asyncio.run(scenario())


def test_actual_policy_denial_rolls_back_run_acceptance(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            request = await h.request()
            admission = h.admission(request)
            denied = PostgreSQLBrowserCurrentAuth(
                session_factory=h.sessions,
                cipher=h.cipher,
                session_binder=h.binder,
                policy=MinimalPolicyGuard(governed_leaf_ids=[h.capability.capability_id]),
                publication_check=h.publication_check,
            )
            store = PostgreSQLBrowserRunStore(
                session_factory=h.sessions,
                authority=PostgreSQLBrowserRunAuthority(current_auth=denied),
                digest_keys={"old": b"o" * 32},
                active_digest_key_id="old",
            )
            with pytest.raises(BrowserAuthorizationError) as policy:
                await store.accept(request, admission)
            assert policy.value.code == "browser_policy_denied"
            retry = await h.request()
            assert retry.task_id == request.task_id and retry.task_status == "created"
            assert not retry.parse_winner
            assert await h.count("tasks") == 1
            assert await h.count("browser_runs") == 0

    asyncio.run(scenario())
