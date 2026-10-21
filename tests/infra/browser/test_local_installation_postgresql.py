"""Real SQL for independent cleanup authorization, not provider cleanup success.

Only unique synthetic rows are inserted and retained. Service evidence comes
from the production token issue/inspect and signed-session boundaries. Historical
lease carriers exercise grant checks; no browser or resource release is invoked.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.infra.auth.crypto import HMACSessionToken
from app.infra.browser.local_installation import PostgreSQLLocalCleanupGrant
from app.infra.persistence.browser.authorization import PostgreSQLBrowserRunAuthority
from app.ports.auth import (
    Principal,
    SessionBindingError,
    VerifiedSessionToken,
    authenticated_session,
)
from app.ports.browser_run_store import RunAdmission
from app.ports.browser_store import (
    BrowserBindingKey,
    BrowserLeaseBindingSnapshot,
    BrowserLeaseClaim,
    BrowserLeaseError,
)
from app.ports.credential_vault import BrowserAuthFact, BrowserAuthorizationError
from tests.infra.persistence.browser.test_runs_postgresql import RunHarness, harness

ServiceBoundary = Literal["valid", "missing_role", "revoked", "expired", "missing_session"]
SERVICE_ROLE = "synthetic_browser_cleanup"


def verified_service(h: RunHarness, *, expired: bool = False) -> VerifiedSessionToken:
    principal = Principal(
        ai_user_id="service_" + uuid4().hex,
        display_name="Synthetic independent cleanup service",
        roles=(SERVICE_ROLE,),
        org_ctx={"tenant_id": h.tenant},
    )
    issued_at = datetime.now(UTC).timestamp() - (7200 if expired else 0)
    codec = HMACSessionToken(
        signing_key=b"synthetic-cleanup-service-key-001",
        ttl_seconds=3600,
        tenant_id=h.tenant,
        clock=lambda: issued_at,
    )
    # Inspect at issuance; expiry tests subsequently exercise current wall/DB time.
    return codec.inspect(codec.issue(principal))


def grant_for(
    h: RunHarness, actor: VerifiedSessionToken, session_id: str,
    *, publication: bytes | None = None,
) -> PostgreSQLLocalCleanupGrant:
    return PostgreSQLLocalCleanupGrant(
        session_factory=h.sessions,
        session_binder=h.binder,
        verified_actor=actor,
        bound_session=session_id,
        binding=BrowserBindingKey(h.tenant, h.user, "oa", h.binding),
        publication_digest=(h.publication_digest if publication is None else publication).hex(),
        required_role=SERVICE_ROLE,
        allowed_operations=frozenset({"cleanup"}),
    )


async def seeded_service(
    h: RunHarness, boundary: ServiceBoundary = "valid",
) -> tuple[PostgreSQLLocalCleanupGrant, VerifiedSessionToken, str]:
    actor = verified_service(h, expired=boundary == "expired")
    session_id = h.binder.bind(actor.principal, uuid4().hex)
    async with h.sessions() as session, session.begin():
        params = {"tenant": h.tenant, "actor": actor.principal.ai_user_id,
                  "session": session_id, "role": SERVICE_ROLE}
        if boundary != "missing_session":
            await session.execute(text(
                "INSERT INTO sessions(tenant_id,session_id) VALUES(:tenant,:session)"
            ), params)
        if boundary != "missing_role":
            await session.execute(text(
                "INSERT INTO principal_roles(tenant_id,ai_user_id,role)"
                " VALUES(:tenant,:actor,:role)"
            ), params)
        if boundary == "revoked":
            await session.execute(text(
                "INSERT INTO auth_session_revocations(token_fingerprint,expires_at)"
                " VALUES(:fingerprint,:expires)"
            ), {"fingerprint": actor.fingerprint, "expires": actor.expires_at})
    return grant_for(h, actor, session_id), actor, session_id


async def accepted_claim(h: RunHarness) -> tuple[RunAdmission, BrowserLeaseClaim]:
    request = await h.request()
    admission = h.admission(request)
    await h.store.accept(request, admission)
    return admission, BrowserLeaseClaim(
        auth=BrowserAuthFact(
            h.owner, None, admission.auth_fingerprint, admission.auth_expires_at,
            authorization_run_id=admission.run_id, evidence_version="verified-session-v1",
        ),
        binding=BrowserLeaseBindingSnapshot(h.tenant, h.user, "oa", h.binding, 1),
        lease_epoch=1, lease_revision=1, holder_id=uuid4().hex, operation_id=uuid4().hex,
        provider_key="synthetic_historical_provider",
        deadline=datetime.now(UTC) - timedelta(minutes=1),
    )


def test_independent_cleanup_survives_business_revocation_without_query_grant(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            admission, claim = await accepted_claim(h)
            grant, _, _ = await seeded_service(h)
            before = await h.store.get(h.owner, admission.task_id, admission.run_id)
            await h.auth.check_current(claim.auth)
            async with h.sessions() as session, session.begin():
                await session.execute(text(
                    "INSERT INTO auth_session_revocations(token_fingerprint,expires_at)"
                    " VALUES(:fingerprint,:expires)"
                ), {"fingerprint": h.fingerprint, "expires": h.expires})
            context = authenticated_session.set(None)
            try:
                await grant.check_cleanup(claim)
                h.authority = PostgreSQLBrowserRunAuthority(
                    current_auth=h.auth, cleanup_authorize=grant.authorize_run,
                )
                store = h.make_store({"old": b"o" * 32}, "old")
                historical = await store.get_for_cleanup(
                    h.owner, admission.task_id, admission.run_id,
                )
                assert historical == before
                with pytest.raises(BrowserAuthorizationError) as denied:
                    await h.auth.check_current(claim.auth)
                assert denied.value.code == "browser_session_authorization_invalid"
                with pytest.raises(BrowserAuthorizationError) as read_denied:
                    await store.get(h.owner, admission.task_id, admission.run_id)
                assert read_denied.value.code == "browser_session_authorization_invalid"
                with pytest.raises(BrowserLeaseError) as recovery_denied:
                    await grant.check_recovery(BrowserBindingKey(h.tenant, h.user, "oa", h.binding))
                assert recovery_denied.value.code == "browser_local_cleanup_grant_denied"
                assert await h.count("tasks") == await h.count("browser_runs") == 1
            finally:
                authenticated_session.reset(context)

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["missing_role", "revoked", "expired", "missing_session"])
def test_current_independent_service_authority_required(
    migrated_database_url: str, boundary: ServiceBoundary,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            admission, claim = await accepted_claim(h)
            valid, _, _ = await seeded_service(h)
            await valid.check_cleanup(claim)
            denied_grant, _, _ = await seeded_service(h, boundary)
            expected = ("browser_local_cleanup_grant_expired" if boundary == "expired"
                        else "browser_local_cleanup_grant_denied")
            with pytest.raises(BrowserLeaseError) as denied:
                await denied_grant.check_cleanup(claim)
            assert denied.value.code == expected
            h.authority = PostgreSQLBrowserRunAuthority(
                current_auth=h.auth, cleanup_authorize=denied_grant.authorize_run,
            )
            with pytest.raises(BrowserLeaseError) as run_denied:
                await h.make_store({"old": b"o" * 32}, "old").get_for_cleanup(
                    h.owner, admission.task_id, admission.run_id,
                )
            assert run_denied.value.code == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["publication", "run", "session", "fingerprint"])
def test_cleanup_requires_exact_persisted_run_and_publication(
    migrated_database_url: str, mismatch: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            _, claim = await accepted_claim(h)
            grant, actor, session_id = await seeded_service(h)
            await grant.check_cleanup(claim)
            if mismatch == "publication":
                grant = grant_for(h, actor, session_id, publication=b"x" * 32)
            elif mismatch == "run":
                claim = replace(claim, auth=replace(claim.auth, authorization_run_id=uuid4().hex))
            elif mismatch == "session":
                other_owner = h.owner.model_copy(update={
                    "session_id": h.binder.bind(h.principal, uuid4().hex),
                })
                claim = replace(claim, auth=replace(claim.auth, owner=other_owner))
            else:
                claim = replace(claim, auth=replace(claim.auth, fingerprint=b"x" * 32))
            with pytest.raises(BrowserLeaseError) as denied:
                await grant.check_cleanup(claim)
            assert denied.value.code == "browser_local_cleanup_grant_denied"

    asyncio.run(scenario())


def test_service_session_signature_must_belong_to_verified_actor(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            _, claim = await accepted_claim(h)
            grant, _, owned_session = await seeded_service(h)
            await grant.check_cleanup(claim)
            other_actor = verified_service(h)
            with pytest.raises(SessionBindingError):
                grant_for(h, other_actor, owned_session)

    asyncio.run(scenario())
