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
    return registry_fakes.active_capability(
        capability_id,
        output_schema=forwarded_schema,
    )


__all__ = ("build_inventory_capability",)
