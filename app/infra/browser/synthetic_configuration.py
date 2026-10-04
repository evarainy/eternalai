"""Fixed synthetic installation identity and private bundle schemas.

Bootstrap imports this small module without constructing the live browser/model
operator or importing its optional HTTP client dependencies.
"""

from __future__ import annotations

import getpass
import hashlib
import sys
import warnings

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.browser_skill.models import ModelManifest
from app.browser_skill.publication_contracts import canonical_json

DATABASE_URL = "postgresql+psycopg://browser_v42_test@postgres:15432/eternalai_test"
BINDING_ID = "browser_fixture_binding"
PROVIDER_ID = "browser_fixture_local"
PUBLICATION_ACTOR = "browser_fixture_operator"
CLEANUP_ACTOR = "browser_fixture_cleanup"
PUBLICATION_ROLE = "browser_fixture_publication"
CLEANUP_ROLE = "browser_fixture_cleanup"
JEV_REQUEST_MODEL = "typesafe/jev-1.13"
JEV_DEPLOYMENT_MODEL = "typesafe/jev-1.13-20260917"


def prompt_database_password() -> SecretStr:
    """User-owned TTY input only; never discover, persist, or put it in a URL."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("browser_database_private_console_required")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass("PostgreSQL browser_v42_test password (hidden): ")
        except (Exception, KeyboardInterrupt):
            raise ValueError("browser_database_private_console_required") from None
    if not value or len(value) > 4096 or "\x00" in value:
        raise ValueError("browser_database_password_invalid")
    return SecretStr(value)


def synthetic_jev_manifest() -> ModelManifest:
    """Code-owned task registration expectation, not a provider fingerprint or review receipt."""
    registration = {
        "domain": "browser.synthetic.jev.manifest.v1",
        "request_model": JEV_REQUEST_MODEL,
        "deployment_model": JEV_DEPLOYMENT_MODEL,
        "endpoint_origin": "https://openrouter.ai",
        "provider": "TypeSafe",
    }
    return ModelManifest(
        request_model=JEV_REQUEST_MODEL,
        deployment_model=JEV_DEPLOYMENT_MODEL,
        manifest_digest=hashlib.sha256(canonical_json(registration).encode("utf-8")).hexdigest(),
    )


class SyntheticDeactivationBundle(BaseModel):
    """Existing independent operator authority only; no business/provider secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    session_signing_key: SecretStr = Field(repr=False)
    session_binding_key: SecretStr = Field(repr=False)
    publication_token: SecretStr = Field(repr=False)


class SyntheticOperatorBundle(BaseModel):
    """Private task keys and independently issued authority tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    session_signing_key: SecretStr = Field(repr=False)
    session_binding_key: SecretStr = Field(repr=False)
    payload_keys: dict[str, SecretStr] = Field(repr=False)
    active_payload_key_id: str
    resource_keys: dict[str, SecretStr] = Field(repr=False)
    active_resource_key_id: str
    request_digest_keys: dict[str, SecretStr] = Field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: SecretStr = Field(repr=False)
    result_digest_key: SecretStr = Field(repr=False)
    proof_context_key: SecretStr = Field(repr=False)
    publication_token: SecretStr = Field(repr=False)
    cleanup_token: SecretStr = Field(repr=False)
    business_token: SecretStr = Field(repr=False)
    jev_manifest: ModelManifest
