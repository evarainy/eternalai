import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.infra.browser.deployment_manifest import (
    BrowserDeployment,
    LocalOfflineManifest,
    SyntheticSource,
    select_local_offline_manifest,
    verify_local_offline_assets,
)
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


def test_local_offline_manifest_checks_exact_registered_files(tmp_path: Path) -> None:
    assets = []
    for role in ("node", "chromium", "playwright", "helper"):
        name = role + ".bin"
        content = ("fictitious-" + role).encode()
        (tmp_path / name).write_bytes(content)
        assets.append({
            "asset_id": role, "role": role, "relative_path": name,
            "sha256": hashlib.sha256(content).hexdigest(), "license_ref": "fixture_license",
        })
    body = {"deployment_id": "fixture", "assets": tuple(assets)}
    body["manifest_digest"] = hashlib.sha256((
        "browser.local.offline.v1\n"
        + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    ).encode("ascii")).hexdigest()
    manifest = LocalOfflineManifest.model_validate(body)
    verify_local_offline_assets(tmp_path, manifest)
    with pytest.raises(ValueError, match="offline_lock_incomplete"):
        manifest.require_registered_site_lock()
    (tmp_path / "node.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="asset_digest_invalid"):
        verify_local_offline_assets(tmp_path, manifest)


def test_registered_offline_lock_requires_font_certificate_and_matching_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infra.browser import deployment_manifest as module

    assets = []
    for role in ("node", "chromium", "playwright", "helper", "font", "certificate"):
        assets.append({
            "asset_id": role, "role": role, "relative_path": role + ".bin",
            "sha256": hashlib.sha256(role.encode()).hexdigest(),
            "license_ref": "synthetic-license-record",
        })
    body = {
        "deployment_id": "fixture", "assets": tuple(assets),
        "runtime": {
            "platform": "linux", "architecture": "x86_64",
            "playwright_version": "1.63.0", "node_version": "v22.0.0",
            "chromium_revision": "fixture", "configuration_digest": "a" * 64,
        },
    }
    body["manifest_digest"] = hashlib.sha256((
        "browser.local.offline.v1\n"
        + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    ).encode("ascii")).hexdigest()
    manifest = LocalOfflineManifest.model_validate(body)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    assert manifest.require_registered_site_lock().architecture == "x86_64"
    assert select_local_offline_manifest(
        active=manifest, rollback=None, requested_digest=manifest.manifest_digest,
    ) == manifest
    with pytest.raises(ValueError, match="selection_invalid"):
        select_local_offline_manifest(
            active=manifest, rollback=None, requested_digest="f" * 64,
        )
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    with pytest.raises(ValueError, match="offline_lock_incomplete"):
        manifest.require_registered_site_lock()
