"""Installation and secure-input regressions; no model request or browser IO."""

from __future__ import annotations

import asyncio
import getpass
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.browser_skill.models import DecisionSource, ModelManifest
from app.infra.browser.openrouter_jev import open_openrouter_jev, prompt_openrouter_key
from app.infra.browser.systemone_http import DecisionDeployment


def test_secure_input_refuses_noninteractive_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.infra.browser.openrouter_jev.sys.stdin",
                        SimpleNamespace(isatty=lambda: False))
    with pytest.raises(ValueError, match="^jev_secure_console_required$"):
        prompt_openrouter_key()


def test_secure_input_refuses_getpass_echo_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(prompt: str) -> str:
        raise getpass.GetPassWarning("synthetic console has no secure input")

    monkeypatch.setattr("app.infra.browser.openrouter_jev.sys.stdin",
                        SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("app.infra.browser.openrouter_jev.sys.stderr",
                        SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("app.infra.browser.openrouter_jev.getpass.getpass", unavailable)
    with pytest.raises(ValueError, match="^jev_secure_console_required$"):
        prompt_openrouter_key()


def test_installation_requires_explicit_snapshot_and_origin() -> None:
    async def scenario() -> None:
        manifest = ModelManifest(
            request_model="typesafe/jev-1.13", deployment_model="typesafe/jev-1.13",
            manifest_digest="1" * 64,
        )
        deployment = DecisionDeployment(
            disposition="cloud_synthetic", endpoint_origin="https://openrouter.ai",
            manifest_digest=manifest.manifest_digest,
            registered_sources=(DecisionSource(source_id="synthetic_fixture",
                origin="https://synthetic.invalid", fixture_digest="2" * 64),),
        )
        with pytest.raises(ValueError, match="^jev_installation_invalid$"):
            async with open_openrouter_jev(
                manifest=manifest, deployment=deployment,
                api_key=SecretStr("synthetic_fixture_only"),
            ):
                pytest.fail("unverified snapshot enabled")

    asyncio.run(scenario())
