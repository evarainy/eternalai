import pytest
from pydantic import ValidationError

from app.infra.browser.deployment_manifest import BrowserDeployment, SyntheticSource
from tests.infra.browser.provider_factories import deployment, source


@pytest.mark.parametrize(
    "origin",
    [
        "http://mock-oa.example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://host.internal",
        "https://user@mock-oa.example.com",
        "https://mock-oa.example.com/",
        "https://mock-oa.example.com?x=1",
    ],
)
def test_cloud_registration_rejects_private_or_ambiguous_origins(origin) -> None:
    with pytest.raises(ValidationError):
        SyntheticSource(source_id="fixture", fixture_digest="a" * 64, origins=(origin,))


def test_exact_source_digest_and_origin_registration_cannot_be_relabelled() -> None:
    registered = source()
    config = deployment()
    assert config.accepts(registered)
    assert not config.accepts(registered.model_copy(update={"fixture_digest": "e" * 64}))
    assert registered.permits_url(registered.origins[0] + "/path")
    assert not registered.permits_url(registered.origins[0] + ".evil/path")
    assert not registered.permits_url("https://user@mock-oa.example.com/path")
    assert not registered.permits_url("file:///local")


def test_capability_evidence_cannot_cross_deployment_or_transport() -> None:
    body = deployment().model_dump()
    body["evidence"]["manifest_digest"] = "e" * 64
    with pytest.raises(ValidationError, match="evidence_mismatch"):
        BrowserDeployment.model_validate(body)
