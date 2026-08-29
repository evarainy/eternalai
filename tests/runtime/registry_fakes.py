"""Deterministic Capability Registry fakes shared by Runtime tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.ports.capability_registry import CapabilitySpec

VALID_RUNTIME_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "registry_fakes.default": {
        "type": "object",
        "properties": {"result": {"type": "string"}},
    },
    "test_runtime_workflow.default": {
        "type": "object",
        "properties": {"status": {"type": "string"}},
    },
    "test_runtime_user_action.structured": {
        "type": "object",
        "properties": {
            "safe": {"type": "string"},
            "safe_note": {"type": "string"},
            "second": {"type": "string"},
        },
    },
    "test_runtime_user_action.drift": {
        "type": "object",
        "properties": {"drifted": {"type": "string"}},
    },
    "test_runtime_trace_threading.document_status": {
        "type": "object",
        "properties": {
            "document_no": {"type": "string"},
            "document_status": {"type": "string"},
        },
    },
    "test_runtime_semantic_knowledge.default": {
        "type": "object",
        "properties": {"selected": {"type": "string"}},
    },
    "test_runtime_capability_selection.default": {
        "type": "object",
        "properties": {"selected": {"type": "string"}},
    },
    "test_pilot_foundation_e2e.pending_workflows": {
        "type": "object",
        "properties": {
            "workflows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "source_name": {"type": "string"},
                        "occurred_at": {"type": "string"},
                        "business_state": {"type": "string"},
                        "link": {"type": "string"},
                        "mobile_link": {"type": "string"},
                    },
                },
            }
        },
    },
    "test_runtime_response_content.workflow_status": {
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string"},
            "current_step": {"type": "string"},
            "approver": {"type": "string"},
        },
    },
    "test_runtime_response_content.document_status": {
        "type": "object",
        "properties": {
            "document_no": {"type": "string"},
            "document_status": {"type": "string"},
            "amount": {"type": "number"},
            "currency": {"type": "string"},
        },
    },
    "test_response_formatters.pending_workflows": {
        "type": "object",
        "properties": {
            "workflows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            },
            "is_complete": {"type": "boolean"},
        },
    },
    "test_response_formatters.system_messages": {
        "type": "object",
        "properties": {
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            },
            "is_complete": {"type": "boolean"},
        },
    },
    "test_response_formatters.vendor_balance": {
        "type": "object",
        "properties": {
            "vendor_id": {"type": "string"},
            "vendor_name": {"type": "string"},
            "balance": {"type": "number"},
            "currency": {"type": "string"},
        },
    },
    "test_response_formatters.device_status": {
        "type": "object",
        "properties": {
            "device_id": {"type": "string"},
            "online": {"type": "boolean"},
            "last_seen_at": {"type": "string"},
        },
    },
    "test_response_formatters.leave_submission": {
        "type": "object",
        "properties": {
            "draft_id": {"type": "string"},
            "workflow_id": {"type": "string"},
            "submit_status": {"type": "string"},
        },
    },
}

VALID_RUNTIME_OUTPUT_SCHEMA = VALID_RUNTIME_OUTPUT_SCHEMAS["registry_fakes.default"]


def runtime_output_schema(name: str) -> dict[str, Any]:
    """Return an isolated copy of a machine-inventoried Runtime fake schema."""

    return deepcopy(VALID_RUNTIME_OUTPUT_SCHEMAS[name])


def schema_digest(schema: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def active_capability(
    capability_id: str,
    *,
    output_schema: dict[str, Any] | None = None,
) -> CapabilitySpec:
    output_schema = output_schema or runtime_output_schema("registry_fakes.default")
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type="query",
        input_schema_digest=f"input-{capability_id}",
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        risk_level="low",
        owner="runtime-test",
        version="1.0.0",
        status="active",
        short_description=capability_id,
        target_system=None,
        execution_identity="user_delegated",
        binding_required=False,
    )


class StaticCapabilityRegistry:
    def __init__(self, *capabilities: str | CapabilitySpec) -> None:
        specs = [
            capability if isinstance(capability, CapabilitySpec) else active_capability(capability)
            for capability in capabilities
        ]
        self._capabilities = {capability.capability_id: capability for capability in specs}

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._capabilities.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        capabilities = list(self._capabilities.values())
        if target_system is not None:
            capabilities = [
                capability
                for capability in capabilities
                if capability.target_system == target_system
            ]
        if type is not None:
            capabilities = [capability for capability in capabilities if capability.type == type]
        if status is not None:
            capabilities = [
                capability for capability in capabilities if capability.status == status
            ]
        return capabilities
