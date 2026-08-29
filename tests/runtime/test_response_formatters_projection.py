"""Formatter reads stay inside the dynamically observed output contract."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult
from app.runtime.models import CapabilityRef
from app.runtime.response_projection import ProjectionContractSnapshot
from app.runtime.runtime import RuntimeImpl, _format_capability_response
from tests.runtime.registry_fakes import active_capability, runtime_output_schema

PathPart = str | int


class TrackingDict(dict[str, Any]):
    def __init__(
        self,
        value: dict[str, Any],
        accesses: set[tuple[PathPart, ...]],
        prefix: tuple[PathPart, ...] = (),
    ) -> None:
        super().__init__(value)
        self._accesses = accesses
        self._prefix = prefix

    def get(self, key: str, default: Any = None) -> Any:
        path = (*self._prefix, key)
        self._accesses.add(path)
        return _tracked(super().get(key, default), self._accesses, path)


class TrackingList(list[Any]):
    def __init__(
        self,
        value: list[Any],
        accesses: set[tuple[PathPart, ...]],
        prefix: tuple[PathPart, ...],
    ) -> None:
        super().__init__(value)
        self._accesses = accesses
        self._prefix = prefix

    def __iter__(self) -> Iterator[Any]:
        for index, item in enumerate(super().__iter__()):
            yield _tracked(item, self._accesses, (*self._prefix, index))


def _tracked(
    value: Any,
    accesses: set[tuple[PathPart, ...]],
    prefix: tuple[PathPart, ...],
) -> Any:
    if isinstance(value, dict):
        return TrackingDict(value, accesses, prefix)
    if isinstance(value, list):
        return TrackingList(value, accesses, prefix)
    return value


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        resolved = root.get("$defs", {}).get(reference.removeprefix("#/$defs/"))
        return resolved if isinstance(resolved, dict) else {}
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        candidates = [
            branch for branch in any_of if isinstance(branch, dict) and branch.get("type") != "null"
        ]
        return candidates[0] if len(candidates) == 1 else {}
    return schema


def _path_is_authorized(
    path: tuple[PathPart, ...],
    schema: dict[str, Any],
) -> bool:
    root = schema
    current = schema
    for part in path:
        current = _resolve(current, root)
        if isinstance(part, int):
            if current.get("type") != "array":
                return False
            items = current.get("items")
            if not isinstance(items, dict):
                return False
            current = items
            continue
        properties = current.get("properties")
        if not isinstance(properties, dict) or part not in properties:
            return False
        child = properties[part]
        if not isinstance(child, dict):
            return False
        current = child
    return True


FORMATTER_CASES = (
    (
        "oa.list_pending_workflows",
        runtime_output_schema("test_response_formatters.pending_workflows"),
        {"workflows": [{"title": "SYNTHETIC_PENDING"}], "is_complete": True},
        "workflows",
        "OA待办共1条（结果完整）: SYNTHETIC_PENDING",
    ),
    (
        "oa.list_system_messages",
        runtime_output_schema("test_response_formatters.system_messages"),
        {"messages": [{"title": "SYNTHETIC_MESSAGE"}], "is_complete": False},
        "messages",
        "OA系统消息返回1条（结果不完整，可能还有更多消息）: SYNTHETIC_MESSAGE",
    ),
    (
        "oa.get_workflow_status",
        runtime_output_schema("test_runtime_response_content.workflow_status"),
        {
            "workflow_id": "SYNTHETIC_WORKFLOW",
            "current_step": "SYNTHETIC_STEP",
            "approver": "SYNTHETIC_APPROVER",
        },
        "approver",
        "OA流程状态 SYNTHETIC_WORKFLOW SYNTHETIC_STEP SYNTHETIC_APPROVER",
    ),
    (
        "u8.get_document_status",
        runtime_output_schema("test_runtime_response_content.document_status"),
        {
            "document_no": "SYNTHETIC_DOCUMENT",
            "document_status": "posted",
            "amount": 12.5,
            "currency": "CNY",
        },
        "document_no",
        "U8单据状态 SYNTHETIC_DOCUMENT posted 12.5 CNY",
    ),
    (
        "u8.get_vendor_balance_summary",
        runtime_output_schema("test_response_formatters.vendor_balance"),
        {
            "vendor_id": "SYNTHETIC_VENDOR",
            "vendor_name": "SYNTHETIC_NAME",
            "balance": 2.5,
            "currency": "CNY",
        },
        "vendor_name",
        "供应商余额 SYNTHETIC_VENDOR SYNTHETIC_NAME 2.5 CNY",
    ),
    (
        "ivms.get_device_online_status",
        runtime_output_schema("test_response_formatters.device_status"),
        {
            "device_id": "SYNTHETIC_DEVICE",
            "online": True,
            "last_seen_at": "SYNTHETIC_TIME",
        },
        "last_seen_at",
        "设备状态 SYNTHETIC_DEVICE 在线 SYNTHETIC_TIME",
    ),
    (
        "oa.submit_leave_request.confirmed_mock",
        runtime_output_schema("test_response_formatters.leave_submission"),
        {
            "draft_id": "SYNTHETIC_DRAFT",
            "workflow_id": "SYNTHETIC_WORKFLOW",
            "submit_status": "SYNTHETIC_SUBMITTED",
        },
        "draft_id",
        "已提交 SYNTHETIC_DRAFT SYNTHETIC_WORKFLOW SYNTHETIC_SUBMITTED",
    ),
)


def _build_completed_envelope(
    capability_id: str,
    schema: dict[str, Any],
    data: dict[str, Any],
) -> Any:
    capability = active_capability(capability_id, output_schema=schema)
    runtime = RuntimeImpl.__new__(RuntimeImpl)
    runtime._response_builder = ResponseEnvelopeBuilder()
    return runtime._build_envelope(
        "SYNTHETIC_RESPONSE_ID",
        "SYNTHETIC_TASK_ID",
        "SYNTHETIC_SESSION_ID",
        ExecutionResult(
            status="completed",
            data=data,
            trace_id="SYNTHETIC_EXECUTION_TRACE_ID",
        ),
        "SYNTHETIC_TRACE_ID",
        CapabilityRef(capability_id=capability_id),
        capability=capability,
        projection_snapshot=ProjectionContractSnapshot.from_capability(capability),
    )


def _assert_complete_envelope_omits(envelope: Any, canary: str) -> None:
    assert canary not in envelope.message
    assert canary not in envelope.fallback_text
    assert canary not in json.dumps(
        envelope.ui.payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert canary not in (envelope.trace_summary or "")
    assert canary not in envelope.model_dump_json()


@pytest.mark.parametrize(
    ("capability_id", "schema", "data", "type_error_field", "expected"),
    FORMATTER_CASES,
)
def test_named_formatter_reads_are_dynamically_authorized_and_exact(
    capability_id: str,
    schema: dict[str, Any],
    data: dict[str, Any],
    type_error_field: str,
    expected: str,
) -> None:
    del type_error_field
    accesses: set[tuple[PathPart, ...]] = set()
    tracked = TrackingDict(data, accesses)

    message = _format_capability_response(capability_id, tracked)

    assert message == expected
    assert accesses
    assert all(_path_is_authorized(path, schema) for path in accesses)


@pytest.mark.parametrize(
    ("capability_id", "schema", "data", "type_error_field", "expected"),
    FORMATTER_CASES,
)
def test_named_formatter_never_sees_undeclared_or_type_invalid_values(
    capability_id: str,
    schema: dict[str, Any],
    data: dict[str, Any],
    type_error_field: str,
    expected: str,
) -> None:
    del expected
    unsafe = dict(data)
    unsafe["synthetic_extra"] = "SYNTHETIC_UNDECLARED_CANARY"
    unsafe[type_error_field] = {"raw": "SYNTHETIC_TYPE_CANARY"}

    envelope = _build_completed_envelope(capability_id, schema, unsafe)

    assert "synthetic_extra" not in envelope.model_dump_json()
    _assert_complete_envelope_omits(envelope, "SYNTHETIC_UNDECLARED_CANARY")
    _assert_complete_envelope_omits(envelope, "SYNTHETIC_TYPE_CANARY")


def test_dynamic_formatter_guard_detects_a_new_undeclared_get_access() -> None:
    capability_id, schema, data, _type_error_field, _expected = FORMATTER_CASES[2]
    accesses: set[tuple[PathPart, ...]] = set()
    tracked = TrackingDict(data, accesses)
    _format_capability_response(capability_id, tracked)

    tracked.get("synthetic_extra")

    unauthorized = [path for path in accesses if not _path_is_authorized(path, schema)]
    assert unauthorized == [("synthetic_extra",)]


def test_generic_formatter_never_interpolates_capability_values() -> None:
    data = {
        "safe": "SYNTHETIC_GENERIC_CANARY",
        "nested": {"value": "SYNTHETIC_NESTED_CANARY"},
    }

    message = _format_capability_response("SYNTHETIC_UNKNOWN_CAPABILITY", data)

    assert message == "操作完成"
    assert "SYNTHETIC_GENERIC_CANARY" not in message
    assert "SYNTHETIC_NESTED_CANARY" not in message


@pytest.mark.parametrize("data", (None, {}))
def test_formatter_none_and_empty_data_use_fixed_completion_message(
    data: dict[str, Any] | None,
) -> None:
    assert _format_capability_response("SYNTHETIC_CAPABILITY", data) == "操作完成"
