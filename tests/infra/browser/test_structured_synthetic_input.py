"""Fixture-only structured submission; no network, browser or database access."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Literal, cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.models import ModelManifest
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    canonical_json,
    publication_digest,
)
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser import local_installation
from app.infra.browser.chat_inputs import (
    FrozenBrowserChatParser,
    FrozenSyntheticStructuredParser,
)
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_TENANT,
    SYNTHETIC_USER,
    build_fixed_synthetic_diagnostic_source,
    build_fixed_synthetic_query_source,
    build_fixed_synthetic_source,
    build_fixed_synthetic_visible_query_source,
)
from app.infra.browser.local_installation import LocalBrowserInstallationDependencies
from app.infra.browser.local_resource_lifecycle import (
    LocalChromiumDeployment,
    local_subject_digest,
)
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.infra.persistence.browser.leases import BrowserProviderPool, BrowserProviderPoolRegistry
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.browser_chat import BrowserChatError
from app.ports.credential_vault import BrowserBindingFact


def _source():
    manifest = ModelManifest(
        request_model="fixture_model_v1", deployment_model="fixture_deployment_v1",
        manifest_digest=hashlib.sha256(b"structured-parser-fixture-v1").hexdigest(),
    )
    return build_fixed_synthetic_query_source(manifest)


def _principal(*, tenant: str = SYNTHETIC_TENANT, user: str = SYNTHETIC_USER) -> Principal:
    return Principal(ai_user_id=user, display_name="Synthetic fixture user", roles=(),
                     org_ctx=PrincipalOrgContext(tenant_id=tenant))


def _parse(
    parser: FrozenSyntheticStructuredParser, message: str, *,
    principal: Principal | None = None, context: AuthenticatedSessionContext | None = None,
    capability=None,
) -> dict[str, object]:
    actor = principal or _principal()
    verified = context or AuthenticatedSessionContext(
        principal=actor, fingerprint=b"f" * 32,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    token = authenticated_session.set(verified)
    try:
        selected = capability or _source().manifest.capability
        return asyncio.run(parser.parse(actor, message, selected))
    finally:
        authenticated_session.reset(token)


def test_exact_json_key_is_only_parsed_argument() -> None:
    source = _source()
    parser = FrozenSyntheticStructuredParser(seed=source.manifest)
    assert _parse(parser, '{"business_key":"different_business_key"}',
                  capability=source.manifest.capability) == {
        "business_key": "different_business_key",
    }
    assert _parse(parser, json.dumps({"business_key": "x" * 96}),
                  capability=source.manifest.capability) == {"business_key": "x" * 96}


@pytest.mark.parametrize("builder", [
    build_fixed_synthetic_query_source, build_fixed_synthetic_diagnostic_source,
    build_fixed_synthetic_visible_query_source,
])
def test_structured_parser_accepts_only_frozen_query_versions(builder) -> None:
    source = builder(_source().manifest.site.decision_manifest)
    parser = FrozenSyntheticStructuredParser(seed=source.manifest)
    assert _parse(parser, '{"business_key":"different_business_key"}',
                  capability=source.manifest.capability) == {
        "business_key": "different_business_key",
    }


@pytest.mark.parametrize("version", ["v2", "v3_diagnostic_3"])
def test_structured_parser_rejects_unapproved_query_versions(version: str) -> None:
    payload = _source().manifest.model_dump(mode="json")
    payload["skill"]["version"] = version
    payload["skill"]["digest"] = publication_digest("browser_skill.v1", payload["skill"])
    payload["digest"] = publication_digest("browser_publication.durable.v1", payload)
    seed = BrowserPublicationManifest.model_validate_json(canonical_json(payload))
    with pytest.raises(ValueError, match="browser_structured_parser_configuration_invalid"):
        FrozenSyntheticStructuredParser(seed=seed)


@pytest.mark.parametrize("message,code", [
    ("{}", "browser_input_missing"),
    ('{"business_key":"x","owner":"other"}', "browser_input_invalid"),
    ('{"business_key":"x","tenant":"other"}', "browser_input_invalid"),
    ('{"business_key":"x","binding":"other"}', "browser_input_invalid"),
    ('{"business_key":"x","scripts":[]}', "browser_input_invalid"),
    ('{"business_key":"x","business_key":"y"}', "browser_input_invalid"),
    ('{"business_key":NaN}', "browser_input_invalid"),
    ('{"business_key":1e999}', "browser_input_invalid"),
    ('{"business_key":12}', "browser_input_invalid"),
    ('{"business_key":"https://example.invalid"}', "browser_input_invalid"),
    (json.dumps({"business_key": "x\n"}), "browser_input_invalid"),
    (json.dumps({"business_key": "x\r\n"}), "browser_input_invalid"),
    (json.dumps({"business_key": "x\ny"}), "browser_input_invalid"),
    (json.dumps({"business_key": "x" * 97}), "browser_input_invalid"),
    ('{"business_key":"x"} trailing', "browser_input_invalid"),
    ('"business_key"', "browser_input_invalid"),
    ("find the synthetic message", "browser_input_invalid"),
    ('{"business_key":"' + "x" * 16_384 + '"}', "browser_input_missing"),
])
def test_structured_input_rejects_nonexact_messages(message: str, code: str) -> None:
    parser = FrozenSyntheticStructuredParser(seed=_source().manifest)
    with pytest.raises(BrowserChatError) as failure:
        _parse(parser, message)
    assert failure.value.code == code
    assert failure.value.http_status == 422


def test_structured_parser_requires_live_matching_auth_and_fixed_capability() -> None:
    source = _source()
    parser = FrozenSyntheticStructuredParser(seed=source.manifest)
    actor = _principal()
    changed = source.manifest.capability.model_copy(update={"capability_id": "other.query"})
    with pytest.raises(BrowserChatError) as changed_error:
        _parse(parser, '{"business_key":"x"}', capability=changed)
    assert (changed_error.value.code, changed_error.value.http_status) == (
        "browser_capability_changed", 409,
    )
    altered_schema = source.manifest.capability.model_copy(update={"input_schema": {
        "type": "object", "properties": {"business_key": {"type": "string"}},
        "additionalProperties": True,
    }})
    with pytest.raises(BrowserChatError) as schema_error:
        _parse(parser, '{"business_key":"x"}', capability=altered_schema)
    assert schema_error.value.code == "browser_capability_changed"
    missing_context = authenticated_session.set(None)
    try:
        with pytest.raises(BrowserChatError) as missing_auth:
            asyncio.run(parser.parse(actor, '{"business_key":"x"}', source.manifest.capability))
        assert (missing_auth.value.code, missing_auth.value.http_status) == (
            "authentication_required", 401,
        )
    finally:
        authenticated_session.reset(missing_context)
    for bad_context in (
        AuthenticatedSessionContext(
            principal=_principal(user="another_user"), fingerprint=b"f" * 32,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        AuthenticatedSessionContext(
            principal=actor, fingerprint=b"f" * 32,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ),
    ):
        with pytest.raises(BrowserChatError) as auth_error:
            _parse(parser, '{"business_key":"x"}', context=bad_context)
        assert (auth_error.value.code, auth_error.value.http_status) == (
            "authentication_required", 401,
        )
    for other in (_principal(user="another_user"), _principal(tenant="another_tenant")):
        with pytest.raises(BrowserChatError) as scope_error:
            _parse(parser, '{"business_key":"x"}', principal=other)
        assert (scope_error.value.code, scope_error.value.http_status) == (
            "browser_structured_scope_denied", 403,
        )


def test_structured_parser_cannot_be_installed_for_legacy_or_changed_seed() -> None:
    source = _source()
    legacy = build_fixed_synthetic_source(source.manifest.site.decision_manifest)
    changed = source.manifest.model_copy(update={"digest": "0" * 64})
    for seed in (legacy.manifest, changed):
        with pytest.raises(ValueError, match="browser_structured_parser_configuration_invalid"):
            FrozenSyntheticStructuredParser(seed=seed)


def _installation(*, input_mode: str, provider: Mock | None,
                  builder=build_fixed_synthetic_query_source):
    source = builder(_source().manifest.site.decision_manifest)
    digest = b"d" * 32
    deployment = LocalChromiumDeployment(
        provider_key="synthetic_provider", manifest_digest=digest,
        node_executable=Path.cwd(), playwright_package=Path.cwd(), browsers_path=Path.cwd(),
        helper_digest=digest, node_digest=digest, chromium_digest=digest, enabled=True,
    )
    return LocalBrowserInstallationDependencies(
        session_factory=Mock(), capability_registry=Mock(),
        session_binder=PrincipalSessionBinder(binding_key=b"synthetic-test-binding-key-32bytes"),
        policy=Mock(), source=source,
        binding=BrowserBindingFact(
            SYNTHETIC_TENANT, SYNTHETIC_USER, "oa", "synthetic_binding", 1,
            local_subject_digest(SYNTHETIC_TENANT, SYNTHETIC_USER),
        ),
        deployment=deployment, decision=Mock(decide=AsyncMock()),
        provider_pools=BrowserProviderPoolRegistry((
            BrowserProviderPool("synthetic_provider", (), digest),
        )), proof_context=BrowserClaimProofContext(b"p" * 32),
        publication_grants=Mock(), cleanup_authority=Mock(
            check_recovery=AsyncMock(), check_cleanup=AsyncMock(),
        ), cleanup_authorize=AsyncMock(),
        payload_keys={"key": b"p" * 32}, active_payload_key_id="key",
        resource_keys={"key": b"r" * 32}, active_resource_key_id="key",
        request_digest_keys={"key": b"q" * 32}, active_request_digest_key_id="key",
        input_digest_key=b"i" * 32, result_digest_key=b"o" * 32,
        llm_provider=provider, structured_output=Mock(parse_to_schema=AsyncMock())
        if provider is not None else None,
        intent_model="fixture_model_v1" if provider is not None else None,
        trace=Mock(), sessions=Mock(), worker_id="fixture_worker", enabled=True,
        input_mode=cast(Literal["chat", "structured"], input_mode),
    )


@pytest.mark.parametrize("builder", [
    build_fixed_synthetic_query_source, build_fixed_synthetic_diagnostic_source,
    build_fixed_synthetic_visible_query_source,
])
def test_installation_selects_structured_without_calling_provider_and_chat_by_default(
    monkeypatch: pytest.MonkeyPatch,
    builder,
) -> None:
    captured: list[object] = []

    class StopAtComposition(Exception):
        pass

    def stop_at_composition(deps: object) -> None:
        captured.append(deps)
        raise StopAtComposition

    monkeypatch.setattr(local_installation, "build_browser_vertical", stop_at_composition)
    provider = Mock(complete=AsyncMock())
    for dependency in (
        _installation(input_mode="structured", provider=provider, builder=builder),
        _installation(input_mode="structured", provider=None, builder=builder),
    ):
        with pytest.raises(StopAtComposition):
            local_installation.build_local_browser_vertical(dependency)
        parser = captured[-1].chat_parser
        assert isinstance(parser, FrozenSyntheticStructuredParser)
        assert _parse(parser, '{"business_key":"key_1"}') == {"business_key": "key_1"}
    provider.complete.assert_not_awaited()

    chat = _installation(input_mode="chat", provider=provider)
    with pytest.raises(StopAtComposition):
        local_installation.build_local_browser_vertical(chat)
    assert isinstance(captured[-1].chat_parser, FrozenBrowserChatParser)
    assert captured[-1].chat_parser._provider is provider

    no_provider = _installation(input_mode="chat", provider=None)
    with pytest.raises(ValueError, match="browser_local_chat_provider_required"):
        local_installation.build_local_browser_vertical(no_provider)
    invalid = _installation(input_mode="invalid", provider=None)
    with pytest.raises(ValueError, match="browser_local_input_mode_invalid"):
        local_installation.build_local_browser_vertical(invalid)


def test_api_cli_selects_input_mode_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, exported in (
        ("app.infra.browser.openrouter_jev", "prompt_openrouter_key"),
        ("app.infra.browser.synthetic_api", "create_synthetic_api"),
        ("app.infra.browser.synthetic_operator", "open_synthetic_operator"),
    ):
        stub = ModuleType(name)
        setattr(stub, exported, Mock())
        if name.endswith("synthetic_operator"):
            stub.prompt_operator_bundle = Mock()
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location(
        "structured_synthetic_api_cli_test", Path("scripts/run_browser_synthetic_api.py"),
    )
    assert spec is not None and spec.loader is not None
    run_browser_synthetic_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_browser_synthetic_api)

    received: list[tuple[str, Path | None]] = []

    async def fake_serve(*, input_mode: str, operator_vault: Path | None) -> None:
        received.append((input_mode, operator_vault))

    monkeypatch.setattr(run_browser_synthetic_api, "_serve", fake_serve)
    assert run_browser_synthetic_api.main(["--enable"]) == 0
    assert run_browser_synthetic_api.main([
        "--enable", "--input-mode", "structured", "--operator-vault", "fixture.enc",
    ]) == 0
    assert received == [("chat", None), ("structured", Path("fixture.enc"))]
    assert run_browser_synthetic_api.main(["--enable", "--input-mode", "unknown"]) == 2
    assert len(received) == 2
