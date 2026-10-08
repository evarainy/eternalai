import asyncio
from unittest.mock import Mock

import pytest

from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.enterprise_connector import EnterpriseConnector
from tests.infra.browser.test_enterprise_manifest import accepted_view, registration


@pytest.mark.parametrize("transport", ["cdp", "playwright"])
def test_connect_never_reads_credentials_or_creates_driver_for_unverified_schema(transport) -> None:
    manifest = registration(transport)
    credential = Mock(side_effect=AssertionError("credential_must_remain_lazy"))
    with pytest.raises(BrowserProviderError) as caught:
        asyncio.run(EnterpriseConnector().connect(
            manifest, accepted_view(manifest), 100.0, credential,
        ))
    assert caught.value.failure.code == "unsupported"
    assert caught.value.failure.dispatch_state == "not_sent"
    credential.assert_not_called()


@pytest.mark.parametrize("version", ["1.62.9", "1.64.0", "2.0.0"])
def test_installed_client_version_is_checked_before_credentials(monkeypatch, version) -> None:
    manifest = registration()
    monkeypatch.setattr("importlib.metadata.version", lambda package: version)
    with pytest.raises(BrowserProviderError) as caught:
        EnterpriseConnector().preflight(manifest, accepted_view(manifest), 100.0)
    assert caught.value.failure.code == "unsupported" and caught.value.reason == "transport"


def test_native_version_mismatch_cannot_trigger_cdp_fallback() -> None:
    manifest = registration(server_playwright_version="1.62")
    credential = Mock()
    with pytest.raises(BrowserProviderError) as caught:
        asyncio.run(EnterpriseConnector().connect(
            manifest, accepted_view(manifest), 100.0, credential,
        ))
    assert caught.value.reason == "transport" and manifest.transport == "playwright"
    credential.assert_not_called()


def test_missing_acceptance_stops_before_driver_metadata(monkeypatch) -> None:
    manifest = registration()
    metadata = Mock(side_effect=AssertionError("preflight_must_stop_at_acceptance"))
    monkeypatch.setattr("importlib.metadata.version", metadata)
    with pytest.raises(BrowserProviderError) as caught:
        EnterpriseConnector().preflight(manifest, accepted_view(manifest, facts=frozenset()), 100.0)
    assert caught.value.reason == "proof"
    metadata.assert_not_called()
