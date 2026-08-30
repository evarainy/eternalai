"""Pure factory helper reached by a Runtime-building test helper."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import tests.runtime.registry_fakes as registry_fakes
from app.ports.capability_registry import CapabilitySpec

_FACTORY_SCHEMA_EXTENSION: dict[str, Any] = {}


def build_inventory_capability(
    capability_id: str,
    schema: dict[str, Any],
) -> CapabilitySpec:
    forwarded_schema = deepcopy(schema)
    forwarded_schema.update(_FACTORY_SCHEMA_EXTENSION)
    return CapabilitySpec.model_construct(
        capability_id=capability_id,
        name=capability_id,
        type="query",
        input_schema_digest=f"input-{capability_id}",
        output_schema=forwarded_schema,
        output_schema_digest=registry_fakes.schema_digest(forwarded_schema),
        risk_level="low",
        owner="runtime-test",
        version="1.0.0",
        status="active",
        short_description=capability_id,
        target_system=None,
        execution_identity="user_delegated",
        binding_required=False,
    )


__all__ = ("build_inventory_capability",)
