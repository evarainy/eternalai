"""Fixed synthetic installation identity and private bundle schemas.

Bootstrap imports this small module without constructing the live browser/model
operator or importing its optional HTTP client dependencies.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys

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
_DATABASE_PASSWORD_FILE = "/run/secrets/task-db-password"
_DATABASE_PASSWORD_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def read_database_password() -> SecretStr:
    """Read only the approved task bind mount; no path or credential fallback."""
    try:
        if sys.platform != "linux":
            raise ValueError
        descriptor = os.open(
            _DATABASE_PASSWORD_FILE,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != 64:
                raise ValueError
            if not os.fstatvfs(descriptor).f_flag & os.ST_RDONLY:
                raise ValueError
            value = os.read(descriptor, 65)
            if len(value) != 64 or any(byte not in _DATABASE_PASSWORD_ALPHABET for byte in value):
                raise ValueError
            return SecretStr(value.decode("ascii"))
        finally:
            os.close(descriptor)
    except (Exception, KeyboardInterrupt):
        raise ValueError("browser_database_password_file_invalid") from None


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
