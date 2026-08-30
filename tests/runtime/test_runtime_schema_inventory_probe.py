"""Probe that keeps helper-forwarded Runtime schemas inside the architecture inventory."""

from __future__ import annotations

from typing import Any

from tests.runtime.schema_inventory_helpers import build_completed_envelope

_HELPER_FORWARDED_SCHEMA = {
    "type": "object",
    "properties": {"safe": {"type": "string"}},
}


def _forward_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return schema


def test_helper_forwarded_unregistered_schema_reaches_runtime_safely() -> None:
    envelope = build_completed_envelope(
        "SYNTHETIC_UNREGISTERED_CAPABILITY",
        _forward_schema(_HELPER_FORWARDED_SCHEMA),
        {"safe": "SYNTHETIC_SAFE_VALUE", "extra": "SYNTHETIC_DROPPED_VALUE"},
    )

    assert envelope.data == {"safe": "SYNTHETIC_SAFE_VALUE"}
    assert "SYNTHETIC_DROPPED_VALUE" not in envelope.model_dump_json()
