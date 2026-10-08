"""Source-only prepared tests for installed and generic capability observations."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.browser_skill.models import BrowserOwner
from app.infra.browser.fixed_synthetic_seed import build_fixed_synthetic_query_source
from app.infra.browser.synthetic_configuration import synthetic_jev_manifest
from app.infra.persistence.browser.publications import PostgreSQLBrowserPublicationStore
from app.ports.browser_publication_store import BrowserPublicationError


def fixtures(callback=None):
    manifest = build_fixed_synthetic_query_source(synthetic_jev_manifest()).manifest
    owner = BrowserOwner(tenant_id="synthetic", user_id="reader", session_id="conversation")
    registry, authority, session = Mock(), Mock(), Mock()
    registry.get = AsyncMock(return_value=manifest.capability)
    authority.verify_manifest = AsyncMock(return_value=True)
    result = Mock()
    result.mappings.return_value.first.return_value = manifest.capability.model_dump(mode="python")
    session.execute = AsyncMock(return_value=result)
    store = PostgreSQLBrowserPublicationStore(
        Mock(), registry, authority, capability_in_session=callback
    )
    return store, owner, manifest, registry, authority, session


def test_installed_capability_uses_exact_session_and_preserves_source_check() -> None:
    callback = AsyncMock()
    store, owner, manifest, registry, authority, session = fixtures(callback)
    callback.return_value = manifest.capability
    assert asyncio.run(store._current(owner, manifest, session)) == manifest.capability
    callback.assert_awaited_once_with(session, manifest.capability.capability_id)
    registry.get.assert_not_awaited()
    session.execute.assert_not_awaited()
    authority.verify_manifest.assert_awaited_once_with(owner, manifest)


@pytest.mark.parametrize("error,code", [
    (RuntimeError("synthetic failure"), "browser_publication_registry_unavailable"),
    (SQLAlchemyError("synthetic SQL"), None),
])
def test_installed_failure_never_falls_back_to_generic_registry(error, code) -> None:
    callback = AsyncMock(side_effect=error)
    store, owner, manifest, registry, authority, session = fixtures(callback)
    if code is None:
        with pytest.raises(SQLAlchemyError) as caught:
            asyncio.run(store._current(owner, manifest, session))
        assert caught.value is error
    else:
        with pytest.raises(BrowserPublicationError) as caught:
            asyncio.run(store._current(owner, manifest, session))
        assert caught.value.code == code
    registry.get.assert_not_awaited()
    session.execute.assert_not_awaited()
    authority.verify_manifest.assert_not_awaited()


def test_generic_registry_keeps_both_observations_and_rejects_locked_change() -> None:
    store, owner, manifest, registry, authority, session = fixtures()
    result = session.execute.return_value
    changed = manifest.capability.model_copy(update={"name": "changed"})
    result.mappings.return_value.first.return_value = changed.model_dump(mode="python")
    with pytest.raises(BrowserPublicationError) as caught:
        asyncio.run(store._current(owner, manifest, session))
    assert caught.value.code == "browser_publication_capability_changed"
    registry.get.assert_awaited_once_with(manifest.capability.capability_id)
    assert session.execute.await_count == 1
    authority.verify_manifest.assert_not_awaited()


def test_locked_capability_is_authoritative_and_unavailable_never_reaches_source() -> None:
    callback = AsyncMock(return_value=None)
    store, owner, manifest, registry, authority, session = fixtures(callback)
    with pytest.raises(BrowserPublicationError) as caught:
        asyncio.run(store._current(owner, manifest, session))
    assert caught.value.code == "browser_publication_capability_unavailable"
    registry.get.assert_not_awaited()
    authority.verify_manifest.assert_not_awaited()
