"""Local installation boundaries; no browser launch or real database connection."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.deployment_manifest import LocalOfflineManifest
from app.infra.browser.local_installation import (
    LocalBrowserInstallationDependencies,
    PostgreSQLLocalCleanupGrant,
    UnsupportedLocalProfiles,
    build_local_browser_vertical,
)
from app.infra.browser.local_resource_lifecycle import (
    LocalChromiumDeployment,
    registered_subject_digest,
)
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.infra.persistence.browser.leases import BrowserProviderPool, BrowserProviderPoolRegistry
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.ports.auth import Principal, PrincipalOrgContext, SessionBindingError, VerifiedSessionToken
from app.ports.browser_profile_store import (
    BrowserProfileContext,
    BrowserProfileError,
    BrowserProfileGeneration,
)
from app.ports.browser_store import BrowserBindingKey, BrowserLeaseClaim, BrowserLeaseError
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact
from app.ports.policy_guard import PolicyGuardPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort
from tests.infra.browser.test_local_read_execution import registered_site


def _grant(*, expires: datetime | None = None) -> PostgreSQLLocalCleanupGrant:
    actor = Principal(ai_user_id="cleanup_service", display_name="Synthetic cleanup service",
                      roles=("explicit_cleanup_role",),
                      org_ctx=PrincipalOrgContext(tenant_id="synthetic_tenant"))
    binder = PrincipalSessionBinder(binding_key=b"synthetic-test-binding-key-32bytes")
    return PostgreSQLLocalCleanupGrant(
        session_factory=async_sessionmaker(), session_binder=binder,
        verified_actor=VerifiedSessionToken(
            principal=actor, fingerprint=b"s" * 32,
            expires_at=expires or datetime.now(UTC) + timedelta(minutes=5), version=2,
        ),
        bound_session=binder.bind(actor, "cleanup_conversation"),
        binding=BrowserBindingKey("synthetic_tenant", "business_user", "oa", "binding"),
        publication_digest="a" * 64, required_role="explicit_cleanup_role",
        allowed_operations=frozenset({"recover", "cleanup"}),
    )


def test_default_installation_is_absent() -> None:
    assert build_local_browser_vertical() is None


def test_cleanup_scope_denies_different_binding_before_any_db_lookup() -> None:
    grant = _grant()
    with pytest.raises(BrowserLeaseError) as failure:
        asyncio.run(grant.check_recovery(
            BrowserBindingKey("synthetic_tenant", "different_user", "oa", "binding"),
        ))
    assert failure.value.code == "browser_local_cleanup_grant_denied"


def test_expired_service_cannot_cleanup_even_with_a_business_owner() -> None:
    grant = _grant(expires=datetime.now(UTC) - timedelta(seconds=1))
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(BrowserLeaseError) as failure:
        asyncio.run(grant._current(session))
    assert failure.value.code == "browser_local_cleanup_grant_expired"
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("database_result", [None, 1])
def test_service_checks_current_role_revocation_owned_session_and_expiry(
    database_result: int | None,
) -> None:
    grant = _grant()
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = database_result
    session.execute.return_value = result
    if database_result is None:
        with pytest.raises(BrowserLeaseError) as failure:
            asyncio.run(grant._current(session))
        assert failure.value.code == "browser_local_cleanup_grant_denied"
    else:
        asyncio.run(grant._current(session))
    statement, parameters = session.execute.await_args.args
    sql = str(statement)
    assert "principal_roles" in sql and "auth_session_revocations" in sql
    assert "sessions" in sql and "clock_timestamp()" in sql
    assert parameters["actor"] == "cleanup_service"
    assert parameters["tenant"] == "synthetic_tenant"
    assert parameters["role"] == "explicit_cleanup_role"
    assert parameters["session"].startswith("sid_v1.")


@pytest.mark.parametrize("publication", [None, bytes.fromhex("b" * 64), bytes.fromhex("a" * 64)])
def test_cleanup_requires_exact_durable_run_publication(publication: bytes | None) -> None:
    grant = _grant()
    session = AsyncMock(spec=AsyncSession)
    authorized, historical = Mock(), Mock()
    authorized.scalar_one_or_none.return_value = 1
    historical.scalar_one_or_none.return_value = publication
    session.execute.side_effect = [authorized, historical]
    manager = Mock()
    manager.return_value.__aenter__ = AsyncMock(return_value=session)
    manager.return_value.__aexit__ = AsyncMock(return_value=None)
    grant._sessions = cast(async_sessionmaker[AsyncSession], manager)
    business_expiry = datetime.now(UTC) - timedelta(days=1)
    claim = BrowserLeaseClaim(
        auth=BrowserAuthFact(
            owner=BrowserOwner(tenant_id="synthetic_tenant", user_id="business_user",
                               session_id="historical_session"),
            authorization_revision=None, fingerprint=b"b" * 32, expires_at=business_expiry,
            authorization_run_id="historical_run", evidence_version="verified-session-v1",
        ),
        binding=BrowserBindingFact("synthetic_tenant", "business_user", "oa", "binding", 1,
                                   b"x" * 32),
        lease_epoch=1, lease_revision=1, holder_id="holder", operation_id="operation",
        provider_key="provider", deadline=business_expiry,
    )
    if publication == bytes.fromhex("a" * 64):
        asyncio.run(grant.check_cleanup(claim))
    else:
        with pytest.raises(BrowserLeaseError) as failure:
            asyncio.run(grant.check_cleanup(claim))
        assert failure.value.code == "browser_local_cleanup_grant_denied"
    statement, parameters = session.execute.await_args.args
    assert "FROM browser_runs" in str(statement)
    assert "auth_evidence_version='verified-session-v1'" in str(statement)
    assert parameters["run"] == "historical_run"
    assert parameters["session"] == "historical_session"
    assert parameters["user"] == "business_user"
    assert parameters["expires_at"] == business_expiry


def test_signed_service_session_cannot_be_reused_by_another_actor() -> None:
    grant = _grant()
    other = grant._actor.principal.model_copy(update={"ai_user_id": "different_actor"})
    with pytest.raises(SessionBindingError):
        PostgreSQLLocalCleanupGrant(
            session_factory=grant._sessions, session_binder=grant._binder,
            verified_actor=replace(grant._actor, principal=other), bound_session=grant._session,
            binding=grant._binding, publication_digest="a" * 64,
            required_role="explicit_cleanup_role", allowed_operations=frozenset({"cleanup"}),
        )


def test_local_profiles_never_claim_capture_or_cleanup_success() -> None:
    unsupported = UnsupportedLocalProfiles()
    context = cast(BrowserProfileContext, None)
    generation = cast(BrowserProfileGeneration, None)
    for action in (
        unsupported.verify_capture(context, generation, b""),
        unsupported.check_live_subject(context, generation),
        unsupported.verify_cleanup(context, generation, b""),
        unsupported.check_cleanup(context),
    ):
        with pytest.raises(BrowserProfileError) as failure:
            asyncio.run(action)
        assert failure.value.code == "browser_local_profile_unsupported"


def test_registered_site_builds_the_existing_vertical_without_chat_parser(
    tmp_path: Path,
) -> None:
    source = registered_site()
    assets = [
        {"asset_id": role, "role": role, "relative_path": role + ".bin",
         "sha256": digest.hex(), "license_ref": "fixture_license"}
        for role, digest in (
            ("node", b"n" * 32), ("chromium", b"c" * 32),
            ("playwright", b"w" * 32), ("helper", b"h" * 32),
        )
    ]
    body = {"deployment_id": "fixture", "assets": assets}
    body["manifest_digest"] = hashlib.sha256((
        "browser.local.offline.v1\n"
        + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    ).encode("ascii")).hexdigest()
    inventory = LocalOfflineManifest.model_validate(body)
    deployment = LocalChromiumDeployment(
        "local", bytes.fromhex(inventory.manifest_digest), tmp_path / "node",
        tmp_path / "package", tmp_path / "browsers", b"h" * 32,
        b"n" * 32, b"c" * 32, enabled=True,
        assets_root=tmp_path, offline_manifest=inventory,
    )
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker()
    binder = PrincipalSessionBinder(binding_key=b"fictitious-test-session-key-32bytes")
    binding = BrowserBindingFact(
        "tenant", "reader", "oa", "binding", 1,
        registered_subject_digest("oa", "account_a"),
    )
    installed = build_local_browser_vertical(LocalBrowserInstallationDependencies(
        session_factory=sessions,
        capability_registry=PostgreSQLCapabilityRegistry(sessions),
        session_binder=binder,
        policy=Mock(spec=PolicyGuardPort), source=source, binding=binding,
        deployment=deployment, decision=Mock(),
        provider_pools=BrowserProviderPoolRegistry((BrowserProviderPool(
            "local", (), deployment.manifest_digest,
        ),)),
        proof_context=BrowserClaimProofContext(b"p" * 32),
        publication_grants=Mock(), cleanup_authority=Mock(),
        cleanup_authorize=AsyncMock(),
        payload_keys={"payload": b"p" * 32}, active_payload_key_id="payload",
        resource_keys={"resource": b"r" * 32}, active_resource_key_id="resource",
        request_digest_keys={"request": b"q" * 32},
        active_request_digest_key_id="request",
        input_digest_key=b"i" * 32, result_digest_key=b"o" * 32,
        llm_provider=None, structured_output=None, intent_model=None,
        trace=Mock(spec=TracePort), sessions=Mock(spec=SessionStorePort),
        worker_id="fixture_worker", enabled=True, input_mode="structured",
    ))
    assert installed is not None
    assert installed.chat is None
    assert installed.submission.skill_id == source.manifest.skill.skill_id
    assert installed.profiles is not None
