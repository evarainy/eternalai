from __future__ import annotations

from itertools import product
from typing import Any

import pytest
from pydantic import ValidationError

from app.ports.capability_registry import CapabilitySpec


def _capability_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "capability_id": "capability-any-open-string",
        "name": "Arbitrary capability name",
        "type": "query",
        "input_schema_digest": "digest:any-input-schema",
        "output_schema_digest": "digest:any-output-schema",
        "risk_level": "low",
        "owner": "owner:any-team",
        "version": "version:any-value",
        "status": "draft",
        "short_description": "Any short description is accepted.",
        "execution_identity": "user_delegated",
        "binding_required": True,
    }
    data.update(overrides)
    return data


def test_all_literal_values_constructible() -> None:
    capability_types = ("query", "action", "workflow", "mock")
    risk_levels = ("low", "medium", "high")
    statuses = ("draft", "active", "disabled", "deprecated")
    target_systems = ("oa", "u8", "hikvision_ivms", None)
    execution_identities = (
        "user_delegated",
        "system_scope",
        "admin_approved_proxy",
    )

    for (
        capability_type,
        risk_level,
        status,
        target_system,
        execution_identity,
    ) in product(
        capability_types,
        risk_levels,
        statuses,
        target_systems,
        execution_identities,
    ):
        spec = CapabilitySpec.model_validate(
            _capability_data(
                type=capability_type,
                risk_level=risk_level,
                status=status,
                target_system=target_system,
                execution_identity=execution_identity,
            )
        )

        assert spec.type == capability_type
        assert spec.risk_level == risk_level
        assert spec.status == status
        assert spec.target_system == target_system
        assert spec.execution_identity == execution_identity


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("type", "report"),
        ("risk_level", "critical"),
        ("status", "archived"),
        ("target_system", "erp"),
        ("execution_identity", "service_account"),
    ],
)
def test_invalid_literal_raises_validation_error(field: str, invalid_value: str) -> None:
    data = _capability_data(target_system="oa")
    data[field] = invalid_value

    with pytest.raises(ValidationError):
        CapabilitySpec.model_validate(data)


def test_open_str_arbitrary_values_locked() -> None:
    spec = CapabilitySpec.model_validate(
        _capability_data(
            capability_id="capability:any/value#123",
            name="Any Display Name / v2",
            owner="owner:any-team/value#123",
            version="release:any-version+build.7",
            short_description="Arbitrary punctuation []{}()/ remains an open string.",
            input_schema_digest="digest:any-input-value#123",
            output_schema_digest="digest:any-output-value#456",
        )
    )

    assert spec.capability_id == "capability:any/value#123"
    assert spec.name == "Any Display Name / v2"
    assert spec.owner == "owner:any-team/value#123"
    assert spec.version == "release:any-version+build.7"
    assert spec.short_description == "Arbitrary punctuation []{}()/ remains an open string."
    assert spec.input_schema_digest == "digest:any-input-value#123"
    assert spec.output_schema_digest == "digest:any-output-value#456"
