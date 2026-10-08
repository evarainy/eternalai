"""Synthetic checker substitution tests, not evidence of a production auth authority."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.browser_skill.models import BrowserOwner, ScopeBinding
from app.infra.persistence.browser.credential_vault import BrowserCredentialVault
from app.ports.credential_binding import PasswordBindingCredential
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserAuthorizationError,
    BrowserBindingFact,
)


def setup():
    owner = BrowserOwner(
        tenant_id="tenant", user_id="user", session_id="sid_v1.synthetic.signature"
    )
    scope = ScopeBinding(
        owner=owner,
        binding_id="binding",
        binding_revision=1,
        authorization_revision=5,
        lease_epoch=2,
    )
    auth = BrowserAuthFact(owner, 5, bytes(range(32)), datetime(2099, 1, 1, tzinfo=UTC))
    binding = BrowserBindingFact("tenant", "user", "oa", "binding", 1, bytes(range(32)))
    facts = AsyncMock(return_value=(auth, binding))
    checker = AsyncMock()
    reader = AsyncMock()
    store = AsyncMock()
    store.load_for_browser.return_value = PasswordBindingCredential(
        login_id=SecretStr("synthetic-login"), password=SecretStr("synthetic-password")
    )
    consume = AsyncMock()
    vault = BrowserCredentialVault(
        binding_reader=reader,
        current_auth=checker,
        fact_reader=facts,
        credential_store=store,
        consumer=consume,
    )
    return vault, scope, auth, binding, facts, checker, reader, store, consume


def test_default_vault_denies_missing_real_authority_before_credential_io():
    reader = AsyncMock()
    vault = BrowserCredentialVault(binding_reader=reader)
    _, scope, *_ = setup()
    with pytest.raises(BrowserAuthorizationError) as error:
        asyncio.run(vault.authorize(scope, "browser_login"))
    assert error.value.code == "browser_authorization_revision_unavailable"
    reader.check_binding.assert_not_awaited()


def test_private_grant_consumes_once_with_current_checks_at_every_boundary():
    async def exercise():
        vault, scope, _, binding, _, checker, reader, store, consume = setup()
        grant = await vault.authorize(scope, "browser_login")
        assert "synthetic-password" not in grant.model_dump_json()
        await vault.consume(grant)
        store.load_for_browser.assert_awaited_once_with(binding, "browser_login")
        consume.assert_awaited_once_with(store.load_for_browser.return_value)
        assert checker.check_current.await_count == reader.check_binding.await_count == 3
        with pytest.raises(BrowserAuthorizationError) as error:
            await vault.consume(grant)
        assert error.value.code == "browser_credential_grant_invalid"

    asyncio.run(exercise())


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id"])
def test_owner_boundary_rejects_cross_tenant_user_and_chat(field):
    async def exercise():
        vault, scope, auth, binding, facts, _, _, store, _ = setup()
        altered = auth.owner.model_copy(update={field: "other"})
        facts.return_value = (replace(auth, owner=altered), binding)
        with pytest.raises(BrowserAuthorizationError):
            await vault.authorize(scope, "browser_login")
        store.load_for_browser.assert_not_awaited()

    asyncio.run(exercise())


def test_revocation_between_grant_and_consume_denies_private_material():
    async def exercise():
        vault, scope, _, _, _, checker, _, store, consume = setup()
        grant = await vault.authorize(scope, "browser_login")
        checker.check_current.side_effect = BrowserAuthorizationError("browser_auth_revoked")
        with pytest.raises(BrowserAuthorizationError) as error:
            await vault.consume(grant)
        assert error.value.code == "browser_auth_revoked"
        store.load_for_browser.assert_not_awaited()
        consume.assert_not_awaited()

    asyncio.run(exercise())


def test_purpose_tamper_invalidates_and_burns_grant():
    async def exercise():
        vault, scope, *_ = setup()
        grant = await vault.authorize(scope, "browser_login")
        with pytest.raises(BrowserAuthorizationError):
            await vault.consume(grant.model_copy(update={"purpose": "browser_restore"}))
        with pytest.raises(BrowserAuthorizationError):
            await vault.consume(grant)

    asyncio.run(exercise())
