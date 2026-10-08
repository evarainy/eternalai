import pytest

from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.enterprise_wire import (
    VERIFIED_CODEC_REVISIONS,
    chromium_path,
    require_verified_codec,
    validate_connection_url,
)
from tests.infra.browser.test_enterprise_manifest import registration


@pytest.mark.parametrize("transport", ["cdp", "playwright"])
@pytest.mark.parametrize("operation", ["session", "profile_create", "termination"])
def test_no_doc_or_fixture_claim_registers_an_enterprise_codec(transport, operation) -> None:
    assert VERIFIED_CODEC_REVISIONS == ()
    with pytest.raises(BrowserProviderError) as caught:
        require_verified_codec(registration(transport), operation, "restore")
    assert caught.value.failure.code == "unsupported"
    assert caught.value.failure.dispatch_state == "not_sent"
    assert not caught.value.failure.cleanup_required


@pytest.mark.parametrize("transport,path", [
    ("cdp", "/chromium"), ("playwright", "/chromium/playwright"),
])
def test_only_explicit_chromium_transport_paths_are_validated(transport, path) -> None:
    manifest = registration(transport)
    assert chromium_path(manifest) == path
    validate_connection_url(manifest, f"wss://browser.fixture.internal:3443{path}?token=unit")


@pytest.mark.parametrize("endpoint", [
    "ws://browser.fixture.internal:3443/chromium/playwright?token=unit",
    "wss://browser.fixture.internal/chromium/playwright?token=unit",
    "wss://browser.fixture.internal:4443/chromium/playwright?token=unit",
    "wss://browser.fixture.internal.evil:3443/chromium/playwright?token=unit",
    "wss://user@browser.fixture.internal:3443/chromium/playwright?token=unit",
    "wss://browser.fixture.internal:3443/chromium?token=unit",
    "wss://browser.fixture.internal:3443/session/connect/guessed?token=unit",
    "wss://browser.fixture.internal:3443/chromium/playwright?token=unit&expose_network=*",
    "wss://browser.fixture.internal:3443/chromium/playwright?token=unit#fragment",
    "wss://browser.fixture.internal:3443/chromium/playwright?token=",
    "wss://browser.fixture.internal:3443/chromium/playwright?token=unit;other=1",
    "wss://browser.fixture.internal:3443/chromium/playwright?token=unit\n",
])
def test_external_url_cannot_change_origin_port_or_protocol(endpoint) -> None:
    with pytest.raises(BrowserProviderError) as caught:
        validate_connection_url(registration(), endpoint)
    assert caught.value.failure.code == "denied"
    assert endpoint not in str(caught.value)
    assert "token=" not in repr(caught.value)
