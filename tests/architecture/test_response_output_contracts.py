"""Credential-property guard for valid ResponseEnvelope output contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.runtime.response_projection import schema_has_credential_property
from tests.runtime.registry_fakes import VALID_RUNTIME_OUTPUT_SCHEMA

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _golden_output_contracts() -> list[tuple[str, dict[str, Any]]]:
    contracts: list[tuple[str, dict[str, Any]]] = []
    fixture_root = _REPO_ROOT / "tests" / "golden_tasks" / "fixtures"
    for path in sorted(fixture_root.glob("GT-*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for capability in fixture["given"]["registered_capabilities"]:
            schema = capability.get("output_schema")
            if isinstance(schema, dict) and schema:
                contracts.append(
                    (f"{path.stem}:{capability['capability_id']}", schema)
                )
    return contracts


def test_valid_output_contract_inventory_has_no_credential_properties() -> None:
    contracts = [
        (
            "production:oa.list_pending_workflows",
            OAPendingWorkflowCollection.model_json_schema(),
        ),
        (
            "production:oa.list_system_messages",
            OASystemMessageCollection.model_json_schema(),
        ),
        ("runtime-fake:VALID_RUNTIME_OUTPUT_SCHEMA", VALID_RUNTIME_OUTPUT_SCHEMA),
        *_golden_output_contracts(),
    ]

    offenders = [
        name for name, schema in contracts if schema_has_credential_property(schema)
    ]

    assert offenders == []


def test_deliberately_invalid_output_contract_is_detected() -> None:
    invalid = {
        "type": "object",
        "properties": {
            "safe": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"refresh_token": {"type": "string"}},
            },
        },
    }

    assert schema_has_credential_property(invalid) is True
