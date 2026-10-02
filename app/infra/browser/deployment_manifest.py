"""Trusted deployment registrations, never derived from a page or an ENV label."""

from __future__ import annotations

import ipaddress
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
