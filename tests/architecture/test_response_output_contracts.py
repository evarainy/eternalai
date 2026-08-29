"""Credential-property guard for valid ResponseEnvelope output contracts."""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.runtime.response_projection import schema_has_credential_property
from tests.runtime.registry_fakes import VALID_RUNTIME_OUTPUT_SCHEMAS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_TEST_ROOT = _REPO_ROOT / "tests" / "runtime"
_PURE_PROJECTION_UNIT = "test_response_projection.py"


def _golden_output_contracts() -> list[tuple[str, dict[str, Any]]]:
    contracts: list[tuple[str, dict[str, Any]]] = []
    fixture_root = _REPO_ROOT / "tests" / "golden_tasks" / "fixtures"
    for path in sorted(fixture_root.glob("GT-*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for capability in fixture["given"]["registered_capabilities"]:
            schema = capability.get("output_schema")
            if isinstance(schema, dict) and schema:
                contracts.append((f"{path.stem}:{capability['capability_id']}", schema))
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
        *(
            (f"runtime-fake:{name}", schema)
            for name, schema in sorted(VALID_RUNTIME_OUTPUT_SCHEMAS.items())
        ),
        *_golden_output_contracts(),
    ]

    offenders = [name for name, schema in contracts if schema_has_credential_property(schema)]

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


def test_every_valid_runtime_fake_schema_detects_an_injected_credential_property() -> None:
    undetected: list[str] = []
    for name, schema in sorted(VALID_RUNTIME_OUTPUT_SCHEMAS.items()):
        mutated = deepcopy(schema)
        properties = mutated.setdefault("properties", {})
        properties["synthetic_password_property"] = {"type": "string"}
        if not schema_has_credential_property(mutated):
            undetected.append(name)

    assert undetected == []


def test_runtime_envelope_fakes_use_only_registered_schema_literals() -> None:
    offenders: list[str] = []
    for path in sorted(_RUNTIME_TEST_ROOT.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if "output_schema" not in source or path.name == _PURE_PROJECTION_UNIT:
            continue
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            candidate: ast.AST | None = None
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Name) and target.id == "output_schema"
                    for target in targets
                ):
                    candidate = node.value
            elif isinstance(node, ast.keyword) and node.arg == "output_schema":
                candidate = node.value
            if candidate is not None and any(
                isinstance(child, ast.Dict) for child in ast.walk(candidate)
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []


def test_runtime_fake_schema_registry_has_no_unreferenced_entries() -> None:
    referenced: set[str] = set()
    for path in sorted(_RUNTIME_TEST_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "runtime_output_schema" or not node.args:
                continue
            name = node.args[0]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                referenced.add(name.value)

    assert referenced == set(VALID_RUNTIME_OUTPUT_SCHEMAS)
