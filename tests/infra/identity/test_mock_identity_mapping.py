"""Tests for the Phase 0 MockIdentityMapping implementation."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from app.ports.capability_gateway import RequestOrgContext
from app.ports.identity_mapping import IdentityCheckResult, IdentityMappingPort

REPO_ROOT = Path(__file__).resolve().parents[3]
IMPLEMENTATION_SOURCE = REPO_ROOT / "app" / "infra" / "identity" / "mock_identity_mapping.py"

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _mock_class() -> type[Any]:
    try:
        module = importlib.import_module("app.infra.identity.mock_identity_mapping")
    except ModuleNotFoundError as exc:
        pytest.fail(f"MockIdentityMapping import failed: {exc}")
    return module.MockIdentityMapping


def _mapping(rows: list[dict[str, str | None]] | None = None) -> Any:
    mapping_class = _mock_class()
    if rows is None:
        return mapping_class()
    return mapping_class(rows=rows)


def _context(**overrides: str) -> RequestOrgContext:
    return RequestOrgContext(request_id="identity-test-request", **overrides)


def test_resolve_execution_identity_returns_active_for_known_identity() -> None:
    result = _run(
        _mapping().resolve_execution_identity(
            ai_user_id="ai-user-001",
            target_system="oa",
            execution_identity="user_delegated",
            request_context=_context(resource_scope="oa-finance"),
        )
    )

    assert isinstance(result, IdentityCheckResult)
    assert result.bind_status == "active"
    assert result.binding_id == "bind-oa-user-001"
    assert result.target_system == "oa"
    assert result.execution_identity == "user_delegated"
    assert result.binding_scope == "oa-finance"


def test_resolve_execution_identity_returns_unbound_for_unknown_without_raising() -> None:
    result = _run(
        _mapping().resolve_execution_identity(
            ai_user_id="unknown-ai-user",
            target_system="oa",
            execution_identity="user_delegated",
            request_context=_context(),
        )
    )

    assert isinstance(result, IdentityCheckResult)
    assert result.bind_status == "unbound"
    assert result.reason_code == "identity_unbound"


def test_get_mapping_returns_identity_check_result_for_known_mapping() -> None:
    result = _run(
        _mapping().get_mapping(
            ai_user_id="ai-user-001",
            target_system="oa",
            binding_scope="oa-finance",
        )
    )

    assert isinstance(result, IdentityCheckResult)
    assert result.binding_id == "bind-oa-user-001"


def test_get_mapping_returns_none_for_unknown_without_raising() -> None:
    result = _run(
        _mapping().get_mapping(
            ai_user_id="unknown-ai-user",
            target_system="oa",
            binding_scope="oa-finance",
        )
    )

    assert result is None


def test_list_mappings_filters_by_target_system() -> None:
    results = _run(_mapping().list_mappings(ai_user_id="ai-user-001", target_system="oa"))

    assert results
    assert all(result.target_system == "oa" for result in results)


def test_list_mappings_filters_by_binding_scope() -> None:
    results = _run(
        _mapping().list_mappings(ai_user_id="ai-user-001", binding_scope="oa-admin-proxy")
    )

    assert [result.binding_id for result in results] == ["bind-oa-admin-001"]


def test_list_mappings_filters_by_account_set_id() -> None:
    results = _run(
        _mapping().list_mappings(ai_user_id="ai-user-multi-u8", account_set_id="u8-acct-a")
    )

    assert [result.binding_id for result in results] == ["bind-u8-user-a"]
    assert results[0].account_set_id == "u8-acct-a"


def test_list_mappings_filters_by_device_domain_id() -> None:
    results = _run(
        _mapping().list_mappings(
            ai_user_id="ai-user-multi-hikvision",
            device_domain_id="camera-domain-west",
        )
    )

    assert [result.binding_id for result in results] == ["bind-hikvision-user-west"]
    assert results[0].device_domain_id == "camera-domain-west"


def test_resolve_u8_multi_account_without_scope_needs_binding_scope() -> None:
    result = _run(
        _mapping().resolve_execution_identity(
            ai_user_id="ai-user-multi-u8",
            target_system="u8",
            execution_identity="user_delegated",
            request_context=_context(),
        )
    )

    assert result.bind_status == "needs_binding_scope"
    assert result.reason_code == "needs_binding_scope"


def test_resolve_u8_with_explicit_account_set_id_returns_matching_active_binding() -> None:
    result = _run(
        _mapping().resolve_execution_identity(
            ai_user_id="ai-user-multi-u8",
            target_system="u8",
            execution_identity="user_delegated",
            request_context=_context(account_set_id="u8-acct-b"),
        )
    )

    assert result.bind_status == "active"
    assert result.binding_id == "bind-u8-user-b"
    assert result.account_set_id == "u8-acct-b"


def test_resolve_hikvision_with_explicit_device_domain_id_returns_matching_binding() -> None:
    result = _run(
        _mapping().resolve_execution_identity(
            ai_user_id="ai-user-multi-hikvision",
            target_system="hikvision_ivms",
            execution_identity="user_delegated",
            request_context=_context(device_domain_id="camera-domain-west"),
        )
    )

    assert result.bind_status == "active"
    assert result.binding_id == "bind-hikvision-user-west"
    assert result.device_domain_id == "camera-domain-west"


def test_precheck_returns_false_for_unresolvable_identity() -> None:
    result = _mapping().precheck(
        ai_user_id="unknown-ai-user",
        target_system="oa",
        execution_identity="user_delegated",
        request_context=_context(),
    )

    assert result is False


def test_precheck_returns_true_for_active_resolvable_identity() -> None:
    result = _mapping().precheck(
        ai_user_id="ai-user-001",
        target_system="oa",
        execution_identity="user_delegated",
        request_context=_context(resource_scope="oa-finance"),
    )

    assert result is True


def test_all_target_system_values_construct_identity_check_result() -> None:
    for target_system in ("oa", "u8", "hikvision_ivms"):
        result = IdentityCheckResult(
            bind_status="active",
            target_system=target_system,
            execution_identity="user_delegated",
        )

        assert result.target_system == target_system


def test_all_execution_identity_values_construct_identity_check_result() -> None:
    for execution_identity in ("user_delegated", "system_scope", "admin_approved_proxy"):
        result = IdentityCheckResult(
            bind_status="active",
            target_system="oa",
            execution_identity=execution_identity,
        )

        assert result.execution_identity == execution_identity


def test_mock_produced_statuses_cover_phase0_statuses_except_verification_failed() -> None:
    mapping = _mapping()
    observed = {
        result.bind_status
        for ai_user_id in (
            "ai-user-001",
            "ai-user-expired",
            "ai-user-revoked",
            "ai-user-unbound",
        )
        for result in _run(mapping.list_mappings(ai_user_id=ai_user_id))
    }
    ambiguous = _run(
        mapping.resolve_execution_identity(
            ai_user_id="ai-user-multi-u8",
            target_system="u8",
            execution_identity="user_delegated",
            request_context=_context(),
        )
    )
    observed.add(ambiguous.bind_status)

    assert {"active", "unbound", "expired", "revoked", "needs_binding_scope"} <= observed
    assert "verification_failed" not in observed


def test_mock_never_produces_verification_failed() -> None:
    mapping = _mapping()
    results = [
        result
        for ai_user_id in (
            "ai-user-001",
            "ai-user-multi-u8",
            "ai-user-multi-hikvision",
            "ai-user-expired",
            "ai-user-revoked",
            "ai-user-unbound",
        )
        for result in _run(mapping.list_mappings(ai_user_id=ai_user_id))
    ]

    assert results
    assert all(result.bind_status != "verification_failed" for result in results)


def test_arbitrary_strings_round_trip_through_model_and_mock_lookup() -> None:
    arbitrary_ai_user_id = "tenant.alpha.user.custom"
    rows = [
        {
            "ai_user_id": arbitrary_ai_user_id,
            "bind_status": "active",
            "binding_id": "binding.custom.001",
            "target_system": "oa",
            "execution_identity": "user_delegated",
            "binding_scope": "scope.custom.alpha",
            "account_set_id": "account.custom.alpha",
            "device_domain_id": "device.custom.alpha",
            "reason_code": "reason.custom.alpha",
        }
    ]

    result = _run(
        _mapping(rows).get_mapping(
            ai_user_id=arbitrary_ai_user_id,
            target_system="oa",
            binding_scope="scope.custom.alpha",
            account_set_id="account.custom.alpha",
            device_domain_id="device.custom.alpha",
        )
    )

    assert isinstance(result, IdentityCheckResult)
    assert result.binding_id == "binding.custom.001"
    assert result.reason_code == "reason.custom.alpha"
    assert result.binding_scope == "scope.custom.alpha"
    assert result.account_set_id == "account.custom.alpha"
    assert result.device_domain_id == "device.custom.alpha"


def test_identity_check_result_extra_field_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        IdentityCheckResult(
            bind_status="active",
            target_system="oa",
            execution_identity="user_delegated",
            unexpected_field="not-allowed",
        )


def test_protocol_methods_return_identity_check_result_instances() -> None:
    async def exercise(port: IdentityMappingPort) -> tuple[
        IdentityCheckResult,
        IdentityCheckResult | None,
        list[IdentityCheckResult],
    ]:
        resolved = await port.resolve_execution_identity(
            ai_user_id="ai-user-001",
            target_system="oa",
            execution_identity="user_delegated",
            request_context=_context(resource_scope="oa-finance"),
        )
        mapping = await port.get_mapping(
            ai_user_id="ai-user-001",
            target_system="oa",
            binding_scope="oa-finance",
        )
        mappings = await port.list_mappings(ai_user_id="ai-user-001", target_system="oa")
        return resolved, mapping, mappings

    resolved_result, mapping_result, mapping_results = _run(exercise(_mapping()))

    assert isinstance(resolved_result, IdentityCheckResult)
    assert isinstance(mapping_result, IdentityCheckResult)
    assert mapping_results
    assert all(isinstance(result, IdentityCheckResult) for result in mapping_results)


def test_concrete_precheck_is_not_added_to_identity_mapping_port_protocol() -> None:
    assert "precheck" not in IdentityMappingPort.__protocol_attrs__
    assert hasattr(_mapping(), "precheck")
    assert not inspect.iscoroutinefunction(_mapping().precheck)


def test_source_has_no_forbidden_identity_provider_imports() -> None:
    source = IMPLEMENTATION_SOURCE.read_text(encoding="utf-8").lower()
    forbidden_terms = (
        "ldap",
        "sso",
        "active directory",
        "msal",
        "oauth",
        "authlib",
        "requests",
        "httpx",
        "aiohttp",
        "subprocess",
        "playwright",
        "selenium",
        "browser",
    )

    assert not any(term in source for term in forbidden_terms)


def test_source_has_no_plaintext_secret_like_values() -> None:
    source = IMPLEMENTATION_SOURCE.read_text(encoding="utf-8")
    sensitive_assignment_pattern = re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret|authorization|bearer|cookie|"
        r"session[_-]?id)\s*[:=]\s*[\"']?[^\"'<\s]{6,}"
    )

    assert sensitive_assignment_pattern.search(source) is None


def test_no_identity_package_init_files_are_created() -> None:
    assert not (REPO_ROOT / "app" / "infra" / "identity" / "__init__.py").exists()
    assert not (REPO_ROOT / "tests" / "infra" / "identity" / "__init__.py").exists()


def test_invalid_target_system_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        IdentityCheckResult(
            bind_status="active",
            target_system="sap",
            execution_identity="user_delegated",
        )


def test_invalid_execution_identity_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        IdentityCheckResult(
            bind_status="active",
            target_system="oa",
            execution_identity="service_account",
        )


def test_invalid_bind_status_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        IdentityCheckResult(
            bind_status="pending_review",
            target_system="oa",
            execution_identity="user_delegated",
        )


def test_precheck_callable_from_within_running_event_loop() -> None:
    async def _inner() -> bool:
        return _mapping().precheck(
            ai_user_id="ai-user-001",
            target_system="oa",
            execution_identity="user_delegated",
            request_context=_context(resource_scope="oa-finance"),
        )

    assert _run(_inner()) is True
