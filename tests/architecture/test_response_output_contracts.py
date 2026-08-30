"""Credential-property guard for valid ResponseEnvelope output contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.runtime.response_projection import schema_has_credential_property
from tests.architecture.runtime_schema_usage import (
    collect_runtime_schema_inventory,
    collect_runtime_schema_inventory_from_sources,
)
from tests.runtime.registry_fakes import VALID_RUNTIME_OUTPUT_SCHEMAS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_TEST_ROOT = _REPO_ROOT / "tests" / "runtime"


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
    runtime_inventory = collect_runtime_schema_inventory(_RUNTIME_TEST_ROOT)
    assert runtime_inventory.unresolved == ()
    contracts = [
        (
            "production:oa.list_pending_workflows",
            OAPendingWorkflowCollection.model_json_schema(),
        ),
        (
            "production:oa.list_system_messages",
            OASystemMessageCollection.model_json_schema(),
        ),
        *((f"runtime-usage:{item.source}", item.schema) for item in runtime_inventory.usages),
        *_golden_output_contracts(),
    ]

    offenders = [name for name, schema in contracts if schema_has_credential_property(schema)]

    assert offenders == []
    assert any(
        item.source.startswith("test_runtime_schema_inventory_probe.py:")
        for item in runtime_inventory.usages
    )
    assert not any(
        item.source.startswith("schema_inventory_non_runtime_decoy.py:")
        for item in runtime_inventory.usages
    )
    missing_registry_schemas = [
        name
        for name, schema in sorted(VALID_RUNTIME_OUTPUT_SCHEMAS.items())
        if not any(item.schema == schema for item in runtime_inventory.usages)
    ]
    assert missing_registry_schemas == []


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


def test_runtime_fake_schema_registry_has_no_unreferenced_entries() -> None:
    import ast

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


def test_runtime_schema_inventory_keeps_every_control_flow_assignment() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_branch_assignment.py": """
from app.domain.models import CapabilitySpec

flag = True

def test_branch_assignment() -> None:
    if flag:
        schema = {
            "type": "object",
            "properties": {"synthetic_password_property": {"type": "string"}},
        }
    else:
        schema = {"type": "object", "properties": {"safe": {"type": "string"}}}
    CapabilitySpec(capability_id="synthetic.branch", output_schema=schema)
"""
        }
    )

    assert inventory.unresolved == ()
    assert any(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_fails_closed_on_unresolved_dict_branch() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_unresolved_branch.py": """
from app.domain.models import CapabilitySpec

flag = True

def test_unresolved_branch() -> None:
    schema = {
        "type": "object",
        "properties": (
            {"safe": {"type": "string"}}
            if flag
            else unknown_schema_properties()
        ),
    }
    CapabilitySpec(capability_id="synthetic.unresolved", output_schema=schema)
"""
        }
    )

    assert inventory.unresolved
    assert any("dict-entry" in item for item in inventory.unresolved)


def test_runtime_schema_inventory_resolves_named_capability_update_mappings() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_named_updates.py": """
from app.ports.capability_registry import CapabilitySpec

safe_schema = {"type": "object", "properties": {"safe": {"type": "string"}}}
payload = {"output_schema": safe_schema}
update = {"output_schema": safe_schema}

CapabilitySpec.model_validate(payload)
capability.model_copy(update=update)
"""
        }
    )

    assert inventory.unresolved == ()
    assert len(inventory.usages) == 2
    assert not any(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_detects_helper_returned_capability_updates() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_helper_updates.py": """
from app.ports.capability_registry import CapabilitySpec

def forward(mapping):
    return mapping

unsafe_schema = {
    "type": "object",
    "properties": {"synthetic_password_property": {"type": "string"}},
}
payload = forward({"output_schema": unsafe_schema})
update = forward({"output_schema": unsafe_schema})

CapabilitySpec.model_validate(payload)
capability.model_copy(update=update)
"""
        }
    )

    assert inventory.unresolved == ()
    assert len(inventory.usages) == 2
    assert all(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_detects_post_construction_schema_mutations() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_schema_mutations.py": """
unsafe_schema = {
    "type": "object",
    "properties": {"synthetic_password_property": {"type": "string"}},
}

capability.output_schema = unsafe_schema
capability.output_schema["properties"]["synthetic_token_property"] = {
    "type": "string"
}
capability.output_schema["properties"].update(
    {"synthetic_cookie_property": {"type": "string"}}
)
"""
        }
    )

    assert inventory.unresolved == ()
    assert len(inventory.usages) == 3
    assert all(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_detects_schema_alias_and_root_update_mutations() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_schema_alias_mutations.py": """
from tests.runtime.registry_fakes import active_capability

schema = {"type": "object", "properties": {"safe": {"type": "string"}}}
schema.update(
    {"properties": {"synthetic_password_property": {"type": "string"}}}
)
capability = active_capability("synthetic.alias", output_schema=schema)
schema["properties"]["synthetic_token_property"] = {"type": "string"}
properties = schema["properties"]
properties["synthetic_cookie_property"] = {"type": "string"}
capability_schema = capability.output_schema
capability_schema.update(
    {"properties": {"synthetic_sessionid_property": {"type": "string"}}}
)
"""
        }
    )

    assert inventory.unresolved == ()
    assert sum(schema_has_credential_property(item.schema) for item in inventory.usages) >= 4


def test_runtime_schema_inventory_fails_closed_on_dynamic_schema_update() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_dynamic_schema_update.py": """
from tests.runtime.registry_fakes import active_capability

schema = {"type": "object", "properties": {"safe": {"type": "string"}}}
schema.update(external_schema_update())
active_capability("synthetic.dynamic", output_schema=schema)
"""
        }
    )

    assert inventory.unresolved
    assert any("external_schema_update" in item for item in inventory.unresolved)


def test_runtime_schema_inventory_checks_every_nested_schema_update_branch() -> None:
    nested_updates = (
        {"$defs": {"Secret": {"properties": {"password": {"type": "string"}}}}},
        {"items": {"properties": {"access_token": {"type": "string"}}}},
        {"additionalProperties": {"properties": {"refresh_token": {"type": "string"}}}},
        {
            "anyOf": [
                {"type": "null"},
                {"properties": {"cookie": {"type": "string"}}},
            ]
        },
    )
    for index, update in enumerate(nested_updates):
        inventory = collect_runtime_schema_inventory_from_sources(
            {
                f"test_nested_schema_update_{index}.py": f"""
from tests.runtime.registry_fakes import active_capability

schema = {{"type": "object", "properties": {{"safe": {{"type": "string"}}}}}}
schema.update({update!r})
active_capability("synthetic.nested", output_schema=schema)
"""
            }
        )

        assert inventory.unresolved == ()
        assert any(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_keeps_parameter_source_beside_branch_assignment() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "schema_branch_helper.py": """
from tests.runtime.registry_fakes import active_capability

def build(schema, flag):
    if flag:
        schema = {"type": "object", "properties": {"safe": {"type": "string"}}}
    return active_capability("synthetic.branch-helper", output_schema=schema)
""",
            "test_schema_branch_caller.py": """
from tests.runtime.schema_branch_helper import build

unsafe = {
    "type": "object",
    "properties": {"synthetic_password_property": {"type": "string"}},
}
build(unsafe, False)
""",
        }
    )

    assert inventory.unresolved == ()
    assert any(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_resolves_output_schema_kwargs_unpacking() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_schema_kwargs.py": """
from tests.runtime.registry_fakes import active_capability
from app.ports.capability_registry import CapabilitySpec

unsafe = {
    "type": "object",
    "properties": {"synthetic_token_property": {"type": "string"}},
}
kwargs = {"output_schema": unsafe}
active_capability("synthetic.kwargs", **kwargs)
CapabilitySpec(**kwargs)
"""
        }
    )

    assert inventory.unresolved == ()
    assert (
        len([item for item in inventory.usages if schema_has_credential_property(item.schema)]) == 2
    )


def test_runtime_schema_inventory_fails_closed_on_structural_alias_mutations() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_structural_alias_mutations.py": """
from tests.runtime.registry_fakes import active_capability

schema = {"type": "object", "properties": {"safe": {"type": "string"}}}
active_capability("synthetic.structural", output_schema=schema)
schema["additionalProperties"] = {
    "properties": {"synthetic_access_token_property": {"type": "string"}}
}
schema[dynamic_schema_key()] = load_schema_branch()
schema["anyOf"].append(load_schema_branch())
"""
        }
    )

    assert any(schema_has_credential_property(item.schema) for item in inventory.usages)
    assert any("output-schema-path" in item for item in inventory.unresolved)
    assert any("load_schema_branch" in item for item in inventory.unresolved)


def test_runtime_schema_inventory_resolves_package_import_factory() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_package_import_factory.py": """
from tests.runtime import registry_fakes

unsafe = {
    "type": "object",
    "properties": {"synthetic_cookie_property": {"type": "string"}},
}
registry_fakes.active_capability("synthetic.package-import", output_schema=unsafe)
"""
        }
    )

    assert inventory.unresolved == ()
    assert any(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_preserves_sequence_mutation_shape() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_sequence_mutations.py": """
from tests.runtime.registry_fakes import active_capability

schema = {"type": "object", "anyOf": []}
active_capability("synthetic.sequence", output_schema=schema)
schema["anyOf"].append(
    {"properties": {"synthetic_password_property": {"type": "string"}}}
)
schema["anyOf"].insert(
    0,
    {"properties": {"synthetic_token_property": {"type": "string"}}},
)
schema["anyOf"].extend([
    {"properties": {"synthetic_cookie_property": {"type": "string"}}}
])
schema["anyOf"].extend((
    {"properties": {"synthetic_sessionid_property": {"type": "string"}}},
))
"""
        }
    )

    assert inventory.unresolved == ()
    offenders = [
        item.schema for item in inventory.usages if schema_has_credential_property(item.schema)
    ]
    assert len(offenders) == 4
    assert all(isinstance(schema["anyOf"], list) for schema in offenders)


def test_runtime_schema_inventory_replays_incremental_mapping_and_schema_writes() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_incremental_writes.py": """
from app.ports.capability_registry import CapabilitySpec
from tests.runtime.registry_fakes import active_capability

unsafe = {
    "type": "object",
    "properties": {"synthetic_password_property": {"type": "string"}},
}
kwargs = {}
kwargs["output_schema"] = unsafe
CapabilitySpec(**kwargs)

schema = {"type": "object", "anyOf": []}
schema["anyOf"] += [
    {"properties": {"synthetic_access_token_property": {"type": "string"}}}
]
active_capability("synthetic.incremental", output_schema=schema)
"""
        }
    )

    assert inventory.unresolved == ()
    assert sum(schema_has_credential_property(item.schema) for item in inventory.usages) >= 2


def test_runtime_schema_inventory_fails_closed_on_dynamic_augmented_assignment() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_dynamic_augmented_assignment.py": """
from tests.runtime.registry_fakes import active_capability

schema = {"type": "object", "anyOf": []}
schema["anyOf"] += load_schema_branches()
active_capability("synthetic.dynamic-augmented", output_schema=schema)
"""
        }
    )

    assert inventory.unresolved
    assert any("load_schema_branches" in item for item in inventory.unresolved)


def test_runtime_schema_inventory_detects_root_mapping_unions() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_root_mapping_unions.py": """
from app.ports.capability_registry import CapabilitySpec
from tests.runtime.registry_fakes import active_capability

kwargs = {}
kwargs |= {
    "output_schema": {
        "properties": {"synthetic_password_property": {"type": "string"}}
    }
}
CapabilitySpec(**kwargs)

schema = {"type": "object"}
schema |= {
    "properties": {"synthetic_access_token_property": {"type": "string"}}
}
capability = active_capability("synthetic.root-union", output_schema=schema)
capability.output_schema |= {
    "properties": {"synthetic_cookie_property": {"type": "string"}}
}
"""
        }
    )

    assert inventory.unresolved == ()
    assert sum(schema_has_credential_property(item.schema) for item in inventory.usages) >= 3


def test_runtime_schema_inventory_ignores_unrelated_credential_payload_keys() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_unrelated_payload.py": """
from tests.runtime.registry_fakes import active_capability

payload = {"password": {}}
payload["password"] = {"value": "SYNTHETIC_PRIVATE_INPUT"}
payload["password"].update({"second": "SYNTHETIC_PRIVATE_INPUT"})
safe_schema = {
    "type": "object",
    "properties": {"safe": {"type": "string"}},
}
active_capability("synthetic.safe-schema", output_schema=safe_schema)
"""
        }
    )

    assert inventory.unresolved == ()
    assert not any(schema_has_credential_property(item.schema) for item in inventory.usages)


def test_runtime_schema_inventory_seeds_explicit_output_schema_aliases() -> None:
    inventory = collect_runtime_schema_inventory_from_sources(
        {
            "test_fixture_capability_alias.py": """
def test_fixture_capability_alias(capability) -> None:
    schema_alias = capability.output_schema
    schema_alias["properties"]["synthetic_password_property"] = {
        "type": "string"
    }
    schema_alias.update({
        "properties": {"synthetic_access_token_property": {"type": "string"}}
    })
"""
        }
    )

    assert inventory.unresolved == ()
    assert sum(schema_has_credential_property(item.schema) for item in inventory.usages) == 2
