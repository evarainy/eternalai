"""Trusted deployment registrations, never derived from a page or an ENV label."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from app.browser_skill.models import BrowserCapabilities, Contract, Digest, OpaqueId


def exact_origin(value: str) -> str:
    """Accept a canonical HTTPS origin; reject userinfo and ambiguous URL spellings."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc != parsed.hostname
        or any(character in value for character in "\\\r\n\t%")
    ):
        raise ValueError("browser_origin_invalid")
    return value


class SyntheticSource(Contract):
    source_id: OpaqueId
    fixture_digest: Digest
    origins: Annotated[tuple[str, ...], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def public_origins(self) -> SyntheticSource:
        if len(set(self.origins)) != len(self.origins):
            raise ValueError("browser_origin_duplicate")
        for origin in self.origins:
            host = urlsplit(exact_origin(origin)).hostname
            assert host is not None
            if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
                raise ValueError("browser_source_not_public")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                continue
            if not address.is_global:
                raise ValueError("browser_source_not_public")
        return self

    def permits_url(self, value: str) -> bool:
        try:
            parsed = urlsplit(value)
            if parsed.username or parsed.password or any(c in value for c in "\\\r\n\t"):
                return False
            return f"{parsed.scheme}://{parsed.netloc}" in self.origins
        except ValueError:
            return False


class CapabilityEvidence(Contract):
    """Provisioned by deployment acceptance, not inferred from vendor documentation."""

    manifest_digest: Digest
    evidence_digest: Digest
    transport: Literal["playwright", "cdp"]
    cookies: bool = False
    local_storage: bool = False
    indexed_db: bool = False
    immutable_capture: bool = False
    confirmed_termination: bool = False


class BrowserDeployment(Contract):
    deployment_id: OpaqueId
    manifest_digest: Digest
    endpoint_origin: str = Field(repr=False)
    transport: Literal["playwright", "cdp"]
    registered_sources: Annotated[tuple[SyntheticSource, ...], Field(min_length=1)]
    # No enterprise registration is accepted by this cloud-only implementation.
    disposition: Literal["cloud_synthetic"] = "cloud_synthetic"
    ttl_ms: Annotated[int, Field(gt=0, le=600_000)] = 120_000
    timeout_seconds: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 30.0
    max_response_bytes: Annotated[int, Field(gt=0, le=4_194_304)] = 2_097_152
    playwright_version: Literal["1.63"] = "1.63"
    evidence: CapabilityEvidence | None = None

    @model_validator(mode="after")
    def registered(self) -> BrowserDeployment:
        exact_origin(self.endpoint_origin)
        if self.endpoint_origin not in {
            "https://production-sfo.browserless.io",
            "https://production-lon.browserless.io",
            "https://production-ams.browserless.io",
        }:
            raise ValueError("browser_deployment_unregistered")
        if len({source.source_id for source in self.registered_sources}) != len(
            self.registered_sources
        ):
            raise ValueError("browser_source_duplicate")
        if self.evidence and (
            self.evidence.manifest_digest != self.manifest_digest
            or self.evidence.transport != self.transport
        ):
            raise ValueError("browser_capability_evidence_mismatch")
        return self

    def accepts(self, source: SyntheticSource) -> bool:
        return source in self.registered_sources

    def capabilities(self) -> BrowserCapabilities:
        evidence = self.evidence
        return BrowserCapabilities(
            transport=self.transport,
            cookies=bool(evidence and evidence.cookies),
            local_storage=bool(evidence and evidence.local_storage),
            indexed_db=bool(evidence and evidence.indexed_db),
            immutable_capture=bool(evidence and evidence.immutable_capture),
            confirmed_termination=bool(evidence and evidence.confirmed_termination),
        )


class LocalOfflineAsset(Contract):
    """One exact installed file; the path is relative to a trusted asset root."""

    asset_id: OpaqueId
    role: Literal[
        "node", "chromium", "playwright", "helper", "font", "certificate",
        "model", "runtime_dependency",
    ]
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: Digest
    license_ref: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def safe_path(self) -> LocalOfflineAsset:
        parts = self.relative_path.split("/")
        if (not self.relative_path.isascii()
                or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", part)
                       or part in {".", ".."} or part.startswith(".env") for part in parts)
                or "\\" in self.relative_path):
            raise ValueError("browser_local_asset_path_invalid")
        return self


class LocalOfflineManifest(Contract):
    """Audited offline inventory. Runtime verification reads only these files."""

    deployment_id: OpaqueId
    manifest_digest: Digest
    assets: Annotated[tuple[LocalOfflineAsset, ...], Field(min_length=4, max_length=4096)]

    @model_validator(mode="after")
    def complete_inventory(self) -> LocalOfflineManifest:
        if ({"node", "chromium", "playwright", "helper"}
                - {asset.role for asset in self.assets}
                or len({asset.asset_id for asset in self.assets}) != len(self.assets)
                or len({asset.relative_path for asset in self.assets}) != len(self.assets)):
            raise ValueError("browser_local_asset_manifest_invalid")
        payload = self.model_dump(mode="json", exclude={"manifest_digest"})
        digest = hashlib.sha256((
            "browser.local.offline.v1\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        ).encode("ascii")).hexdigest()
        if digest != self.manifest_digest:
            raise ValueError("browser_local_asset_manifest_digest_invalid")
        return self


def verify_local_offline_assets(root: Path, manifest: LocalOfflineManifest) -> None:
    """Verify exact files without scanning the surrounding installation tree."""
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError("browser_local_asset_root_invalid")
    resolved_root = root.resolve(strict=True)
    for asset in manifest.assets:
        path = (resolved_root / asset.relative_path).resolve(strict=True)
        if not path.is_relative_to(resolved_root) or not path.is_file():
            raise ValueError("browser_local_asset_path_invalid")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != asset.sha256:
                raise ValueError("browser_local_asset_digest_invalid")
