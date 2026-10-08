from __future__ import annotations

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.browser_skill.models import ModelManifest
from app.infra.browser.decision_adapters import LocalChoiceCodec
from services.decision.protocol import (
    ChoiceRequest,
    EngineResult,
    ProbabilityEntry,
    Usage,
    encode_response,
)
from tests.browser_skill.factories import context, request
from tests.services.decision.test_manifest import projection_policy


def wire() -> bytes:
    call = replace(
        context(),
        manifest=ModelManifest(
            request_model="test",
            deployment_model="synthetic-decision-test-v1",
            manifest_digest="a" * 64,
        ),
    )
    return LocalChoiceCodec().encode(request(), call)


def selected(
    probabilities: tuple[float, float] = (0.95, 0.05), *, confidence: float = 0.95
) -> EngineResult:
    return EngineResult(
        outcome="selected",
        selected_id="target_a",
        distribution=tuple(
            ProbabilityEntry(id=name, p=probability)
            for name, probability in zip(("target_a", "target_b"), probabilities, strict=True)
        ),
        certainty=confidence,
        usage=Usage(input_tokens=8, output_tokens=0),
    )


def test_exact_local_codec_round_trip_and_minimal_projection() -> None:
    parsed = ChoiceRequest.parse(wire(), 65_536)
    projected = projection_policy().project(parsed)
    assert set(projected.model_dump()) == {"task_kind", "criteria", "options"}
    assert set(projected.options[0].model_dump()) == {
        "id",
        "role",
        "label",
        "context",
        "row_label",
        "column_label",
    }
    output = encode_response(parsed, selected(), minimum_confidence=0.9, maximum_bytes=65_536)
    call = replace(
        context(),
        manifest=ModelManifest(
            request_model="test",
            deployment_model="synthetic-decision-test-v1",
            manifest_digest="a" * 64,
        ),
    )
    result = LocalChoiceCodec().decode(output, request(), call)
    assert result.status == "selected"
    assert result.selected == request().candidates[0].ref


@pytest.mark.parametrize("field", ["owner", "url", "password", "raw_dom", "operation", "value"])
def test_extra_wire_fields_never_enter_model(field: str) -> None:
    data = json.loads(wire())
    data[field] = "generated forbidden marker"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ChoiceRequest.parse(json.dumps(data).encode(), 65_536)


@pytest.mark.parametrize("path", ["constraints", "label", "context", "row_label", "column_label"])
def test_projection_rejects_unapproved_text(path: str) -> None:
    data = json.loads(wire())
    if path == "constraints":
        data[path] = ["unapproved generated marker"]
    else:
        data["options"][0][path] = (
            ["unapproved generated marker"] if path == "context" else "unapproved generated marker"
        )
    with pytest.raises(ValueError, match="input_unapproved"):
        projection_policy().project(ChoiceRequest.parse(json.dumps(data).encode(), 65_536))


@pytest.mark.parametrize("change", ["unknown", "missing", "duplicate", "sum", "maximal", "nan"])
def test_invalid_engine_output_rejected(change: str) -> None:
    result = selected()
    data = result.model_dump(mode="json")
    if change == "unknown":
        data["distribution"][0]["id"] = "unknown"
    elif change == "missing":
        data["distribution"] = data["distribution"][:1]
    elif change == "duplicate":
        data["distribution"][1]["id"] = "target_a"
    elif change == "sum":
        data["distribution"][0]["p"] = 0.8
    elif change == "maximal":
        data["selected_id"] = "target_b"
    else:
        data["certainty"] = float("nan")
    with pytest.raises(ValueError):
        # model_construct deliberately bypasses Pydantic; boundary must revalidate.
        forged = EngineResult.model_construct(**data)
        encode_response(
            ChoiceRequest.parse(wire(), 65_536),
            forged,
            minimum_confidence=0.9,
            maximum_bytes=65_536,
        )


@pytest.mark.parametrize(
    "distribution,confidence,expected",
    [((0.5, 0.5), 0.99, "ambiguous"), ((0.95, 0.05), 0.8, "abstained")],
)
def test_ties_and_low_confidence_are_not_selected(
    distribution: tuple[float, float], confidence: float, expected: str
) -> None:
    data = json.loads(
        encode_response(
            ChoiceRequest.parse(wire(), 65_536),
            selected(distribution, confidence=confidence),
            minimum_confidence=0.9,
            maximum_bytes=65_536,
        )
    )
    assert data["outcome"] == expected
    assert data["selected_id"] is None
    assert data["distribution"] == []


@pytest.mark.parametrize("data", [b'{"a":1,"a":2}', b'{"certainty":NaN}', b"[]"])
def test_invalid_json_and_shape(data: bytes) -> None:
    with pytest.raises(ValueError):
        ChoiceRequest.parse(data, 65_536)


def test_input_and_output_byte_budgets() -> None:
    with pytest.raises(ValueError, match="request_budget"):
        ChoiceRequest.parse(wire(), 5)
    with pytest.raises(ValueError, match="response_budget"):
        encode_response(
            ChoiceRequest.parse(wire(), 65_536), selected(), minimum_confidence=0.9, maximum_bytes=5
        )
