"""Deterministic boundaries for the small global knowledge component."""

from __future__ import annotations

import json
from typing import Any

from app.knowledge import BasicKnowledge, sanitize_knowledge_text
from app.knowledge.basic_knowledge import (
    MAX_CAPABILITY_CONTRACT_LENGTH,
    MAX_CAPABILITY_CONTRACTS,
)
from app.ports.capability_registry import CapabilitySpec, CapabilityStatus


def _capability(
    capability_id: str,
    *,
    status: CapabilityStatus = "active",
    description: str | None = None,
    owner: str = "knowledge-test-owner",
    name: str | None = None,
    intent_tags: list[str] | None = None,
    input_schema: dict[str, Any] | None = None,
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=name or capability_id,
        type="query",
        intent_tags=intent_tags or [],
        input_schema=input_schema or {},
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner=owner,
        version="1.0.0",
        status=status,
        short_description=description or capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def test_static_content_covers_required_categories_and_keyword_boundaries() -> None:
    knowledge = BasicKnowledge()

    matched = knowledge.context_items("报销单在哪里审批", ())
    unmatched = knowledge.context_items("完全无关的问题", ())

    assert {item.category for item in knowledge.static_items} == {
        "enterprise_term",
        "mock_system",
        "policy_template",
    }
    assert any("企业术语：报销单" in item for item in matched)
    assert any("制度模板：费用报销" in item for item in matched)
    assert all("设备巡检" not in item for item in matched)
    assert unmatched == ()
    assert knowledge.context_items("roadmap automation", ()) == ()
    assert any(
        "Mock 系统说明" in item
        for item in knowledge.context_items("查询 OA 待办", ())
    )


def test_capability_contracts_follow_each_registry_snapshot() -> None:
    knowledge = BasicKnowledge()
    first = _capability("oa.expense.first", status="active")
    second = _capability("oa.expense.second", status="active")
    inactive = tuple(
        _capability(f"oa.expense.{status}", status=status)
        for status in ("draft", "disabled", "deprecated")
    )

    first_context = knowledge.capability_input_contracts((first,))
    second_context = knowledge.capability_input_contracts((second,))
    inactive_context = knowledge.capability_input_contracts(inactive)

    assert len(first_context) == len(second_context) == 1
    assert first_context[0]["capability_id"] == "oa.expense.first"
    assert first_context[0]["capability_type"] == "query"
    assert first_context[0]["target_system"] == "oa"
    assert first_context[0]["status"] == "active"
    assert second_context[0]["capability_id"] == "oa.expense.second"
    assert second_context[0]["status"] == "active"
    assert inactive_context == ()


def test_zero_argument_contract_explicitly_requires_empty_arguments() -> None:
    capability = _capability(
        "oa.list_pending_workflows",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )

    contract = BasicKnowledge().capability_input_contracts((capability,))[0]

    assert contract["capability_id"] == "oa.list_pending_workflows"
    assert contract["allowed_argument_keys"] == []
    assert contract["required_argument_keys"] == []
    assert contract["additionalProperties"] is False
    assert contract["arguments_must_be"] == {}


def test_registry_free_text_is_never_read_into_capability_knowledge() -> None:
    description_marker = "unique-description-marker-7f3a"
    name_marker = "unique-name-marker-8b4c"
    owner_marker = "unique-owner-marker-9d5e"
    intent_marker = "unique-intent-marker-0a6f"
    schema_value_markers = (
        "unique-schema-description-marker",
        "unique-schema-default-marker",
        "unique-schema-example-marker",
    )
    capability = _capability(
        "oa.safe.query",
        description=description_marker,
        owner=owner_marker,
        name=name_marker,
        intent_tags=[intent_marker],
        input_schema={
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": schema_value_markers[0],
                    "default": schema_value_markers[1],
                    "examples": [schema_value_markers[2]],
                }
            },
            "required": ["user"],
            "additionalProperties": False,
            "description": "top-level-schema-description-must-not-enter",
        },
    )

    contracts = BasicKnowledge().capability_input_contracts((capability,))
    serialized = json.dumps(contracts, ensure_ascii=False)

    for free_text in (
        description_marker,
        name_marker,
        owner_marker,
        intent_marker,
        *schema_value_markers,
        "top-level-schema-description-must-not-enter",
    ):
        assert free_text not in serialized
    assert "oa.safe.query" in serialized
    contract = contracts[0]
    assert contract["capability_type"] == "query"
    assert contract["allowed_argument_keys"] == ["user"]
    assert contract["required_argument_keys"] == ["user"]


def test_structured_contract_preserves_all_multi_and_long_argument_keys() -> None:
    long_key = "employee_" + ("x" * 180)
    allowed_keys = ["region-code", long_key, "employee_id"]
    required_keys = [long_key, "employee_id"]
    capability = _capability(
        "oa.long-arguments.query",
        input_schema={
            "type": "object",
            "properties": {
                key: {
                    "type": "string",
                    "description": f"description-value-{index}",
                    "default": f"default-value-{index}",
                    "example": f"example-value-{index}",
                }
                for index, key in enumerate(allowed_keys)
            },
            "required": required_keys,
            "additionalProperties": False,
        },
    )

    contract = BasicKnowledge().capability_input_contracts((capability,))[0]
    serialized = json.dumps(contract, ensure_ascii=True, separators=(",", ":"))

    assert contract["capability_id"] == "oa.long-arguments.query"
    assert contract["capability_type"] == "query"
    assert contract["target_system"] == "oa"
    assert contract["allowed_argument_keys"] == sorted(allowed_keys)
    assert contract["required_argument_keys"] == sorted(required_keys)
    assert contract["additionalProperties"] is False
    assert len(serialized) <= MAX_CAPABILITY_CONTRACT_LENGTH
    assert json.loads(serialized) == contract
    for index in range(len(allowed_keys)):
        assert f"description-value-{index}" not in serialized
        assert f"default-value-{index}" not in serialized
        assert f"example-value-{index}" not in serialized


def test_contract_payload_is_count_bounded_without_partial_contracts() -> None:
    knowledge = BasicKnowledge()
    capabilities = tuple(
        _capability(f"oa.contract-{index}")
        for index in range(MAX_CAPABILITY_CONTRACTS + 2)
    )
    oversized_key = "x" * MAX_CAPABILITY_CONTRACT_LENGTH

    contracts = knowledge.capability_input_contracts(capabilities)
    rejected = knowledge.capability_input_contracts(
        (
            _capability(
                "oa.oversized",
                input_schema={
                    "type": "object",
                    "properties": {oversized_key: {"type": "string"}},
                },
            ),
        )
    )

    assert len(contracts) == MAX_CAPABILITY_CONTRACTS
    assert rejected == ()


def test_sensitive_items_and_unsafe_capability_ids_fail_closed_as_whole_values() -> None:
    sensitive_items = (
        "token=synthetic-token-value",
        "credential=synthetic-credential-value",
        '"access_token": "synthetic-json-value"',
        'Bearer "synthetic bearer value"',
        '联系人="张三 李四"',
        "endpoint=https://10.20.30.40/internal",
        "mail=person@example.internal",
        "host=192.168.10.20",
    )

    assert [sanitize_knowledge_text(item) for item in sensitive_items] == [
        "[REDACTED]"
    ] * len(sensitive_items)
    contracts = BasicKnowledge().capability_input_contracts(
        (_capability("token=synthetic-unsafe-id"),)
    )
    assert contracts == ()


def test_no_capability_guidance_filters_to_active_registry_entries() -> None:
    active = _capability("oa.active.query")
    disabled = _capability(
        "oa.disabled.query",
        status="disabled",
    )

    message, fallback = BasicKnowledge().no_capability_guidance((disabled, active))

    assert "oa.active.query" in message
    assert "query/oa/active" in message
    assert "oa.disabled.query" not in message
    assert "oa.active.query" in fallback
    assert "query/oa/active" in fallback
    assert "oa.disabled.query" not in fallback
    assert "Admin Lite > Registry" in message
    assert "will not create or execute" in fallback
