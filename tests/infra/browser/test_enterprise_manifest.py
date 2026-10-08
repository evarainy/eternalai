from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.enterprise_manifest import (
    REQUIRED_FACTS,
    DeploymentView,
    EnterpriseManifest,
    EnterpriseSyntheticSource,
    exact_enterprise_origin,
    require_current_view,
)


def registration(transport="playwright", **updates):
    values = dict(
        deployment_id="enterprise_fixture", worker_id="worker_a", revision=1,
        endpoint_origin="https://browser.fixture.internal:3443", image_digest="a" * 64,
        image_architecture="amd64", enterprise_version="0.0.0", transport=transport,
        server_playwright_version="1.63", tls_evidence_digest="b" * 64,
        protocol_revision="unverified_fixture_only",
        registered_sources=(EnterpriseSyntheticSource(
            source_id="fixture", fixture_digest="c" * 64,
            origins=("https://oa.fixture.internal:8443",),
        ),),
    )
    values.update(updates)
    return EnterpriseManifest(**values)


def accepted_view(manifest, **updates):
    # Unit-only authority response. Never represents an actual server acceptance.
    values = dict(
        manifest=manifest, worker_id=manifest.worker_id, revision=manifest.revision,
        expires_at=200.0, facts=REQUIRED_FACTS | {"native_version", "cdp_version"},
        evidence_digest="d" * 64,
    )
    values.update(updates)
    return DeploymentView(**values)


@pytest.mark.parametrize("origin", [
    "http://fixture.internal", "https://fixture.internal/", "https://fixture.internal?x=1",
    "https://fixture.internal#x", "https://user@fixture.internal", "https://fixture.internal:0",
    "https://fixture.internal:99999", "https://Fixture.internal", "https://fixture.internal:03443",
    "https://fixture.internal.", "https://fixture..internal", "https://[::1]", "https://a\\b",
    " https://fixture.internal", "https://fixture.internal\n", "https://fixture.internal:abc",
])
def test_reject_noncanonical_tls_origin(origin) -> None:
    with pytest.raises(ValueError, match="enterprise_origin_invalid"):
        exact_enterprise_origin(origin)


def test_exact_port_and_every_manifest_field_are_bound() -> None:
    manifest = registration()
    assert exact_enterprise_origin(manifest.endpoint_origin) == manifest.endpoint_origin
    assert registration().digest == manifest.digest
    other_port = registration(endpoint_origin="https://browser.fixture.internal:4443")
    assert other_port.digest != manifest.digest
    assert registration(worker_id="worker_b").digest != manifest.digest
    assert registration(revision=2).digest != manifest.digest
    assert "fixture.internal" not in repr(manifest)
    with pytest.raises(ValidationError):
        manifest.revision = 2


@pytest.mark.parametrize("mutation", ["clone", "worker", "revision", "expired", "nan"])
def test_authority_view_binds_exact_registration_and_current_worker(mutation) -> None:
    manifest = registration()
    view = accepted_view(manifest)
    now = 100.0
    if mutation == "clone":
        view = accepted_view(registration())
    elif mutation == "worker":
        view = accepted_view(manifest, worker_id="worker_b")
    elif mutation == "revision":
        view = accepted_view(manifest, revision=2)
    elif mutation == "expired":
        now = 200.0
    else:
        now = float("nan")
    with pytest.raises(BrowserProviderError) as caught:
        require_current_view(manifest, view, now, "restore", acceptance=True)
    assert caught.value.failure.code == "stale" and caught.value.reason == "proof"


@pytest.mark.parametrize("missing", sorted(REQUIRED_FACTS | {"native_version"}))
def test_each_acceptance_fact_is_required(missing) -> None:
    manifest = registration()
    view = accepted_view(manifest, facts=(REQUIRED_FACTS | {"native_version"}) - {missing})
    with pytest.raises(BrowserProviderError) as caught:
        require_current_view(manifest, view, 100.0, "restore", acceptance=True)
    assert caught.value.failure.code == "unsupported" and caught.value.reason == "proof"
    require_current_view(manifest, view, 100.0, "acquire", acceptance=False)


def test_synthetic_source_must_be_authority_registered_object() -> None:
    manifest = registration()
    source = manifest.registered_sources[0]
    assert manifest.accepts(source)
    assert not manifest.accepts(source.model_copy())
    assert source.permits_url("https://oa.fixture.internal:8443/fixture?row=1")
    assert not source.permits_url("https://oa.fixture.internal/fixture")
    assert not source.permits_url("https://oa.fixture.internal:8443.evil/fixture")
    with pytest.raises(ValidationError):
        EnterpriseSyntheticSource(
            source_id="fixture", fixture_digest="c" * 64,
            disposition="real_intranet", origins=source.origins,
        )


@pytest.mark.parametrize("changes", [
    {"server_playwright_version": "1.62"}, {"server_playwright_version": None},
])
def test_native_server_version_must_match_before_start(changes) -> None:
    manifest = registration(**changes)
    with pytest.raises(BrowserProviderError) as caught:
        require_current_view(manifest, accepted_view(manifest), 100.0, "restore", acceptance=True)
    assert caught.value.failure.code == "unsupported" and caught.value.reason == "transport"


def test_cdp_acceptance_does_not_claim_native_version() -> None:
    manifest = registration("cdp", server_playwright_version=None)
    view = accepted_view(manifest, facts=REQUIRED_FACTS | {"cdp_version"})
    require_current_view(manifest, view, 100.0, "restore", acceptance=True)
    assert view.facts == REQUIRED_FACTS | {"cdp_version"}


@pytest.mark.parametrize("updates", [
    {"tls_trust": "ignore_https_errors"}, {"timeout_seconds": float("inf")},
    {"max_response_bytes": 8_000_000}, {"revision": 0},
])
def test_manifest_rejects_unsupported_trust_or_resource_budgets(updates) -> None:
    with pytest.raises(ValidationError):
        registration(**updates)
