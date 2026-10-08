"""Closed Enterprise codec admission. No server response revision is verified yet.

Generic Cloud compatibility documentation is insufficient to register a codec.
There is deliberately no caller registration hook, Cloud parser, or HTTP call.
An audited, version-specific codec and real deployment evidence are both needed
before adding a supported revision in a later source change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Never
from urllib.parse import urlsplit

from app.infra.browser.browserless_wire import BrowserProviderError, Phase
from app.infra.browser.enterprise_manifest import EnterpriseManifest

Operation = Literal["session", "profile_create", "termination"]


@dataclass(frozen=True)
class VerifiedCodecRevision:
    enterprise_version: str
    image_digest: str
    protocol_revision: str


# Empty by evidence, not an environment switch. No synthetic test registration.
VERIFIED_CODEC_REVISIONS: tuple[VerifiedCodecRevision, ...] = ()


def require_verified_codec(
    manifest: EnterpriseManifest, operation: Operation, phase: Phase,
) -> Never:
    """Fail before a credential factory, HTTP client, or Playwright driver is touched."""
    raise BrowserProviderError("unsupported", phase, "unsupported")


def chromium_path(manifest: EnterpriseManifest) -> str:
    # https://docs.browserless.io/baas/connection-url-patterns
    return "/chromium/playwright" if manifest.transport == "playwright" else "/chromium"


def validate_connection_url(manifest: EnterpriseManifest, endpoint: str) -> None:
    """Private URL check only; this never authorizes a launch or proves ownership."""
    try:
        if len(endpoint) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in endpoint):
            raise ValueError
        parsed = urlsplit(endpoint)
        expected = urlsplit(manifest.endpoint_origin)
        # Reject aliases, redirects, credentials in userinfo and additional query keys.
        # A future codec constructs the token query; this method never decodes/logs it.
        if (
            parsed.scheme != "wss" or parsed.netloc != expected.netloc
            or parsed.path != chromium_path(manifest) or parsed.fragment
            or parsed.username is not None or parsed.password is not None
            or not parsed.query.startswith("token=") or parsed.query == "token="
            or "&" in parsed.query or ";" in parsed.query or "#" in parsed.query
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise BrowserProviderError("denied", "restore", "transport") from None
