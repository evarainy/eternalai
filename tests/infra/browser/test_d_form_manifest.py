"""D publication and pool digests bind the registration, not source file bytes."""

from pathlib import Path

import pytest

from app.infra.browser.d_form_bootstrap import pool_manifest_digest
from app.infra.browser.d_form_manifest import build_fill_stop_manifest
from app.infra.browser.d_form_owned_browser import owned_provider_digest

ORIGIN = "https://synthetic.invalid"


def test_digests_are_computed_without_reading_source_files(monkeypatch):
    expected = build_fill_stop_manifest(origin=ORIGIN)

    def refuse(self, *args, **kwargs):
        raise AssertionError(f"source read: {self.name}")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    manifest = build_fill_stop_manifest(origin=ORIGIN)
    assert manifest == expected
    assert pool_manifest_digest(ORIGIN, "a" * 64) == owned_provider_digest(
        manifest.digest, "a" * 64
    )


def test_publication_digest_still_binds_origin():
    other = build_fill_stop_manifest(origin="https://other.invalid")
    current = build_fill_stop_manifest(origin=ORIGIN)
    assert other.digest != current.digest
    assert other.skill.version != current.skill.version
    assert pool_manifest_digest(ORIGIN, "a" * 64) != pool_manifest_digest(
        "https://other.invalid", "a" * 64
    )


def test_pool_digest_still_binds_chromium_hash():
    assert pool_manifest_digest(ORIGIN, "a" * 64) != pool_manifest_digest(ORIGIN, "b" * 64)
    with pytest.raises(ValueError):
        build_fill_stop_manifest(origin="not an origin")
