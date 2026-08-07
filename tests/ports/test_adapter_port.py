"""Contract tests for AdapterPort adapter-facing models."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.adapter import (
    MOCK_ERROR_MODE_TO_ERROR_CODE,
    AdapterFailureStage,
    AdapterPort,
    AdapterResult,
    AdapterStatus,
    AdapterTraceMetadata,
    MockErrorMode,
)
from app.ports.capability_gateway import ErrorCode

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_SOURCE = REPO_ROOT / "app" / "ports" / "adapter.py"

EXPECTED_ADAPTER_RESULT_FIELDS = {
    "status",
    "data",
    "error_code",
    "raw_payload_ref",
    "trace_metadata",
}

EXPECTED_ADAPTER_STATUS_VALUES = (
    "success",
    "error",
    "timeout",
    "permission_denied",
)

EXPECTED_ADAPTER_FAILURE_STAGE_VALUES = (
    "argument_validation",
    "credential_read",
    "provider_transport",
    "normalization",
    "unknown",
)

EXPECTED_MOCK_ERROR_MODE_VALUES = (
    "timeout",
    "permission_denied",
    "malformed_json",
    "empty_response",
    "http_500",
    "missing_required_field",
)

EXPECTED_MOCK_ERROR_MODE_TO_ERROR_CODE = {
    "timeout": "adapter_timeout",
    "permission_denied": "upstream_permission_denied",
    "malformed_json": "adapter_payload_invalid",
    "empty_response": "adapter_empty_response",
    "http_500": "adapter_http_500",
    "missing_required_field": "adapter_missing_required_field",
}


def test_adapter_result_field_set_matches_contract() -> None:
    assert set(AdapterResult.model_fields.keys()) == EXPECTED_ADAPTER_RESULT_FIELDS


def test_adapter_status_literal_values_match_contract() -> None:
    assert get_args(AdapterStatus) == EXPECTED_ADAPTER_STATUS_VALUES


def test_adapter_failure_stage_literal_values_match_bounded_trace_contract() -> None:
    assert get_args(AdapterFailureStage) == EXPECTED_ADAPTER_FAILURE_STAGE_VALUES


def test_adapter_result_rejects_status_outside_contract() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AdapterResult(status="ok")

    assert "Input should be" in str(exc_info.value)


def test_adapter_result_defaults_match_contract() -> None:
    result = AdapterResult(status="success")

    assert result.data is None
    assert result.error_code is None
    assert result.raw_payload_ref is None
    assert result.trace_metadata is None


def test_adapter_trace_metadata_is_bounded_frozen_and_not_business_serialized() -> None:
    metadata = AdapterTraceMetadata(
        argument_keys=("alpha", "zeta"),
        failure_stage="argument_validation",
    )
    result = AdapterResult(status="error", trace_metadata=metadata)

    assert metadata.model_dump(mode="json") == {
        "argument_keys": ["alpha", "zeta"],
        "failure_stage": "argument_validation",
    }
    assert set(AdapterTraceMetadata.model_fields) == {
        "argument_keys",
        "failure_stage",
    }
    assert AdapterTraceMetadata.model_config["extra"] == "forbid"
    assert AdapterTraceMetadata.model_config["frozen"] is True
    assert "trace_metadata" not in result.model_dump(mode="json")
    assert "trace_metadata" not in repr(result)
    with pytest.raises(ValidationError):
        AdapterTraceMetadata(argument_keys=("zeta", "alpha"))
    with pytest.raises(ValidationError):
        AdapterTraceMetadata(argument_keys=("alpha",), raw_value="forbidden")
    with pytest.raises(ValidationError):
        metadata.failure_stage = "unknown"


def test_adapter_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        AdapterResult(status="success", unexpected="extra")

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_adapter_result_accepts_shared_error_code_values() -> None:
    shared_error_code = get_args(ErrorCode)[get_args(ErrorCode).index("adapter_timeout")]

    result = AdapterResult(status="error", error_code=shared_error_code)

    assert result.error_code == "adapter_timeout"


def test_mock_error_mode_literal_values_match_contract() -> None:
    assert get_args(MockErrorMode) == EXPECTED_MOCK_ERROR_MODE_VALUES


def test_mock_error_modes_are_representable_in_execution_context() -> None:
    for mode in get_args(MockErrorMode):
        execution_context: dict[str, Any] = {"mock_error_mode": mode}

        assert execution_context["mock_error_mode"] == mode


def test_mock_error_mode_to_error_code_mapping_matches_contract() -> None:
    assert dict(MOCK_ERROR_MODE_TO_ERROR_CODE) == EXPECTED_MOCK_ERROR_MODE_TO_ERROR_CODE


def test_mock_error_mode_to_error_code_values_are_shared_error_codes() -> None:
    error_code_values = set(get_args(ErrorCode))

    assert set(MOCK_ERROR_MODE_TO_ERROR_CODE.values()) <= error_code_values


def test_mock_error_mode_to_error_code_mapping_is_frozen() -> None:
    with pytest.raises(TypeError):
        cast(Any, MOCK_ERROR_MODE_TO_ERROR_CODE)["x"] = "y"


class TestAdapterPortProtocol:
    def test_protocol_is_not_runtime_checkable(self) -> None:
        assert hasattr(AdapterPort, "__protocol_attrs__")
        assert not getattr(AdapterPort, "_is_runtime_protocol", False)

    def test_protocol_defines_only_execute(self) -> None:
        assert set(AdapterPort.__protocol_attrs__) == {"execute"}

    def test_execute_signature_matches_contract(self) -> None:
        hints = get_type_hints(AdapterPort.execute)
        signature = inspect.signature(AdapterPort.execute)

        assert AdapterPort.execute.__name__ == "execute"
        assert list(signature.parameters) == [
            "self",
            "capability_id",
            "arguments",
            "execution_context",
        ]
        assert hints["capability_id"] is str
        assert hints["arguments"] == dict[str, Any]
        assert hints["execution_context"] == dict[str, Any]
        assert hints["return"] is AdapterResult
        assert inspect.iscoroutinefunction(AdapterPort.execute)


def test_adapter_source_does_not_contain_concrete_implementation_dependencies() -> None:
    source = ADAPTER_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = (
        "requests",
        "httpx",
        "subprocess",
        "selenium",
        "playwright",
        "open(",
        "sqlalchemy",
        "redis",
        "arq",
        "app.gateway.",
        "app.execution_fabric",
    )
    assert not any(term in source for term in forbidden_terms)
