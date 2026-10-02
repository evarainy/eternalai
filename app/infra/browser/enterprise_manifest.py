"""Private Enterprise registration. Configuration is not deployment acceptance.

The composition owner injects a trusted authority; requests cannot supply one.
This preparation supports synthetic sources only, even on private deployments.
"""

from __future__ import annotations

import math
import re
from hashlib import sha256
from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from app.browser_skill.models import Contract, Digest, OpaqueId, ScopeBinding
from app.infra.browser.browserless_wire import BrowserProviderError, Phase

AcceptanceFact = Literal[
    "licensed_image", "offline_activation", "tls", "external_origin",
    "native_version", "cdp_version", "session_identity", "immutable_profile",
    "complete_profile_diagnostics", "resource_termination",
]
REQUIRED_FACTS: frozenset[AcceptanceFact] = frozenset({
    "licensed_image", "offline_activation", "tls", "external_origin",
    "session_identity", "immutable_profile", "complete_profile_diagnostics",
    "resource_termination",
})


def exact_enterprise_origin(value: str) -> str:
    """Canonical HTTPS origin, including an explicit non-default port if supplied."""
    try:
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or host.startswith(".") or host.endswith(".") or ".." in host
        ):
            raise ValueError
        # IPv6 and custom trust bundles need an explicit future implementation.
        port = parsed.port
        expected = host if port is None else f"{host}:{port}"
        if parsed.netloc != expected or port == 0 or value != f"https://{expected}":
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ValueError("enterprise_origin_invalid") from None


class EnterpriseSyntheticSource(Contract):
    source_id: OpaqueId
    fixture_digest: Digest
    disposition: Literal["registered_synthetic"] = "registered_synthetic"
    origins: Annotated[tuple[str, ...], Field(min_length=1, max_length=32, repr=False)]

    @model_validator(mode="after")
    def validate_origins(self) -> Self:
        for origin in self.origins:
            exact_enterprise_origin(origin)
        if len(set(self.origins)) != len(self.origins):
            raise ValueError("enterprise_duplicate_origin")
        return self

    def permits_url(self, value: str) -> bool:
        try:
            if len(value) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
                return False
            parsed = urlsplit(value)
            origin = exact_enterprise_origin(f"{parsed.scheme}://{parsed.netloc}")
            return origin in self.origins and not parsed.fragment
        except (ValueError, TypeError):
            return False


class EnterpriseManifest(Contract):
    deployment_id: OpaqueId
    worker_id: OpaqueId
    revision: Annotated[int, Field(ge=1)]
    endpoint_origin: str = Field(repr=False)
    image_digest: Digest
    image_architecture: Literal["amd64", "arm64"]
    enterprise_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)]
    transport: Literal["playwright", "cdp"]
    client_playwright_version: Literal["1.63"] = "1.63"
    server_playwright_version: Annotated[
        str | None, Field(pattern=r"^\d+\.\d+$", max_length=16)
    ] = None
    tls_trust: Literal["system_verified"] = "system_verified"
    tls_evidence_digest: Digest
    protocol_revision: OpaqueId
    registered_sources: Annotated[
        tuple[EnterpriseSyntheticSource, ...], Field(min_length=1, max_length=32, repr=False)
    ]
    timeout_seconds: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 30.0
    max_response_bytes: Annotated[int, Field(ge=1024, le=4_194_304)] = 2_000_000

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        exact_enterprise_origin(self.endpoint_origin)
        ids = [source.source_id for source in self.registered_sources]
        if len(set(ids)) != len(ids):
            raise ValueError("enterprise_duplicate_source")
        return self

    @property
    def digest(self) -> str:
        # Includes all immutable fields; never accept a caller's replacement hash.
        return sha256(self.model_dump_json().encode()).hexdigest()

    def accepts(self, source: EnterpriseSyntheticSource) -> bool:
        return type(source) is EnterpriseSyntheticSource and any(
            source is registered for registered in self.registered_sources
        )


class DeploymentView(Contract):
    """A current view returned by the trusted authority, not by the browser caller."""

    manifest: EnterpriseManifest = Field(repr=False)
    worker_id: OpaqueId
    revision: Annotated[int, Field(ge=1)]
    expires_at: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    facts: frozenset[AcceptanceFact]
    evidence_digest: Digest


class EnterpriseAuthority(Protocol):
    async def deployment(self, manifest: EnterpriseManifest) -> DeploymentView: ...
    async def current(self, binding: ScopeBinding) -> ScopeBinding: ...
    async def source(self, binding: ScopeBinding) -> EnterpriseSyntheticSource: ...

    async def cleanup(self, original_binding: ScopeBinding) -> ScopeBinding:
        """Independently authorize original owner/session cleanup, despite revocation.

        Return the complete original claim only if this caller may clean it up.
        Possession of an old browser handle alone is never sufficient authority.
        """
        ...


def require_current_view(
    manifest: EnterpriseManifest, view: DeploymentView, now: float, phase: Phase,
    *, acceptance: bool,
) -> None:
    if (
        type(view) is not DeploymentView or view.manifest is not manifest
        or view.worker_id != manifest.worker_id or view.revision != manifest.revision
        or not math.isfinite(now) or now >= view.expires_at
    ):
        raise BrowserProviderError("stale", phase, "proof")
    if acceptance:
        version_fact = "native_version" if manifest.transport == "playwright" else "cdp_version"
        if not REQUIRED_FACTS.issubset(view.facts) or version_fact not in view.facts:
            raise BrowserProviderError("unsupported", phase, "proof")
        if (
            manifest.transport == "playwright"
            and manifest.server_playwright_version != manifest.client_playwright_version
        ):
            raise BrowserProviderError("unsupported", phase, "transport")
