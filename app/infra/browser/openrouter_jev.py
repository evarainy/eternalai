"""Explicit Jev installation; no environment/secret discovery or startup requests."""

from __future__ import annotations

import getpass
import sys
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from pydantic import SecretStr

from app.browser_skill.models import ModelManifest
from app.infra.browser.decision_adapters import OpenRouterJevCodec
from app.infra.browser.systemone_http import DecisionDeployment, DecisionHTTPProvider

OPENROUTER_ORIGIN = "https://openrouter.ai"
JEV_REQUEST_MODEL = "typesafe/jev-1.13"


def prompt_openrouter_key() -> SecretStr:
    """User-only local console input. Refuse echoing/noninteractive fallbacks.

    Call only in the user's own terminal after the non-secret deployment facts
    are ready. Never invoke this from an assistant/tool session or a web request.
    The value is not saved, returned as plain text, or loaded from environment.
    """
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("jev_secure_console_required")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass("OpenRouter restricted Jev key (hidden): ")
        except (getpass.GetPassWarning, EOFError):
            raise ValueError("jev_secure_console_required") from None
    if not value or value.strip() != value or len(value) > 4096 or any(
        ord(char) < 33 or ord(char) > 126 for char in value
    ):
        raise ValueError("jev_key_input_invalid")
    return SecretStr(value)


@asynccontextmanager
async def open_openrouter_jev(
    *, manifest: ModelManifest, deployment: DecisionDeployment, api_key: SecretStr,
) -> AsyncIterator[DecisionHTTPProvider]:
    """Build a single provider without IO; the caller explicitly dispatches decisions.

    The registered synthetic source and expected served model must be approved
    independently. A key cannot authorize a source, browser, principal, or Run.
    No alias->snapshot inference, retry, proxy inheritance or provider fallback.
    """
    if (
        manifest.request_model != JEV_REQUEST_MODEL
        or manifest.deployment_model == manifest.request_model
        or not manifest.deployment_model.startswith(JEV_REQUEST_MODEL + "-")
        or deployment.endpoint_origin != OPENROUTER_ORIGIN
        or deployment.disposition != "cloud_synthetic"
        or deployment.manifest_digest != manifest.manifest_digest
        or not isinstance(api_key, SecretStr)
    ):
        raise ValueError("jev_installation_invalid")
    key = api_key.get_secret_value()
    if not key or key.strip() != key or len(key) > 4096 or any(
        ord(char) < 33 or ord(char) > 126 for char in key
    ):
        raise ValueError("jev_key_input_invalid")
    async with httpx2.AsyncClient(
        base_url=OPENROUTER_ORIGIN,
        headers={"Authorization": "Bearer " + key},
        trust_env=False, follow_redirects=False, verify=True,
    ) as client:
        yield DecisionHTTPProvider(client, OpenRouterJevCodec(), deployment)
