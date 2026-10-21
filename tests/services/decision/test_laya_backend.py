"""Pure codec boundaries for the proposed local Laya checkpoint; no model is loaded."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace

import pytest

from app.browser_skill.models import ModelManifest
from app.infra.browser.decision_adapters import InvalidResponse, LocalChoiceCodec
from services.decision.laya_backend import LayaEngine, _model_question, _model_result
from services.decision.manifest import LayaInputAdapter
from services.decision.protocol import ChoiceRequest, encode_response
from services.decision.service import DecisionService, ServingFailure, ServingLimits
from tests.browser_skill.factories import context, request
from tests.services.decision.test_manifest import artifact_bundle, projection_policy


def _input():
    call = replace(
        context(), manifest=ModelManifest(
            request_model="local", deployment_model="pinned-laya",
            manifest_digest="a" * 64,
        ),
    )
    wire = LocalChoiceCodec().encode(request(), call)
    return projection_policy().project(ChoiceRequest.parse(wire, 65_536))


def test_laya_mapping_only_contains_approved_choice_projection() -> None:
    adapter = LayaInputAdapter(
        schema_version="decision_laya_input.v1",
        state_format="laya_minimal_choice.v1",
        instruction="Select the approved target.",
    )
    state, questions = _model_question(_input(), adapter)
    assert set(state) == {"text", "elements"}
    assert set(questions) == {"target"}
    assert set(questions["target"]["criteria"]) == {"target_a", "target_b"}
    assert "owner" not in json.dumps(state)


def test_model_choice_keeps_its_own_probability_semantics() -> None:
    raw = {
        "answers": {"target": {
            "type": "choice", "choice": "target_a",
            "probabilities": {"target_a": 0.92, "target_b": 0.08},
            "answer_confidence": 0.91,
        }},
        "usage": {"input_tokens": 12, "output_tokens": 0,
                  "truncated": False, "state_tokens_dropped": 0,
                  "truncated_questions": []},
    }
    result = _model_result(raw, _input(), "b" * 64)
    assert result.score_semantics == "laya_answer_confidence"
    assert result.certainty == 0.91
    assert result.calibration_digest == "b" * 64


def test_pinned_score_round_trip_and_low_score_abstention() -> None:
    inputs = _input()
    call = replace(
        context(), manifest=ModelManifest(
            request_model="local", deployment_model="pinned-laya",
            manifest_digest="a" * 64,
        ),
    )
    parsed = ChoiceRequest.parse(LocalChoiceCodec().encode(request(), call), 65_536)
    for chosen_probability, confidence, expected in (
        (0.92, 0.91, "selected"), (0.95, 0.80, "abstained")
    ):
        result = _model_result({
            "answers": {"target": {
                "type": "choice", "choice": "target_a",
                "probabilities": {
                    "target_a": chosen_probability, "target_b": 1 - chosen_probability,
                },
                "answer_confidence": confidence,
            }},
            "usage": {"input_tokens": 12, "output_tokens": 0,
                      "truncated": False, "state_tokens_dropped": 0,
                      "truncated_questions": []},
        }, inputs, "b" * 64)
        wire = encode_response(
            parsed, result, minimum_confidence=0.9, maximum_bytes=65_536,
            expected_semantics="laya_answer_confidence",
            calibration_digest="b" * 64, manifest_digest="a" * 64,
        )
        assert LocalChoiceCodec().decode(wire, request(), call).status == expected
    with pytest.raises(ValueError, match="score_semantics_mismatch"):
        encode_response(parsed, result, minimum_confidence=0.9, maximum_bytes=65_536)


@pytest.mark.parametrize("usage_change", [
    {"truncated": True},
    {"state_tokens_dropped": 1},
    {"truncated_questions": ["target"]},
    {"options": {"target": {"total": 2, "distinct": 1}}},
])
def test_truncated_or_collapsed_model_view_rejected(usage_change: dict) -> None:
    usage = {
        "input_tokens": 12, "output_tokens": 0,
        "truncated": False, "state_tokens_dropped": 0,
        "truncated_questions": [],
    }
    usage.update(usage_change)
    raw = {
        "answers": {"target": {
            "type": "choice", "choice": "target_a",
            "probabilities": {"target_a": 0.92, "target_b": 0.08},
            "answer_confidence": 0.91,
        }},
        "usage": usage,
    }
    with pytest.raises(ValueError, match="model_usage_invalid"):
        _model_result(raw, _input(), "b" * 64)


@pytest.mark.parametrize("change", ["missing", "rank", "unknown", "nonfinite"])
def test_model_score_fail_closed(change: str) -> None:
    raw = {
        "answers": {"target": {
            "type": "choice", "choice": "target_a",
            "probabilities": {"target_a": 0.92, "target_b": 0.08},
            "answer_confidence": 0.91,
        }},
        "usage": {"input_tokens": 12, "output_tokens": 0,
                  "truncated": False, "state_tokens_dropped": 0,
                  "truncated_questions": []},
    }
    answer = raw["answers"]["target"]
    if change == "missing":
        answer.pop("probabilities")
    elif change == "rank":
        answer["probabilities"] = {"target_a": 1, "target_b": 2}
    elif change == "unknown":
        answer["choice"] = "target_c"
    else:
        answer["probabilities"]["target_a"] = float("nan")
    with pytest.raises((ValueError, TypeError)):
        _model_result(raw, _input(), "b" * 64)


def test_unlabelled_model_score_cannot_be_decoded_as_confidence() -> None:
    call = replace(
        context(), manifest=ModelManifest(
            request_model="local", deployment_model="pinned-laya",
            manifest_digest="a" * 64,
        ),
    )
    body = {
        "schema_version": "browser_choice.v1", "request_id": request().request_id,
        "deployment": "pinned-laya", "snapshot": request().scope.model_dump(mode="json"),
        "outcome": "selected", "selected_id": "target_a",
        "distribution": [{"id": "target_a", "p": 0.92}, {"id": "target_b", "p": 0.08}],
        "certainty": 0.92, "usage": {"input_tokens": 12, "output_tokens": 0},
    }
    with pytest.raises(InvalidResponse, match="score_semantics_mismatch"):
        LocalChoiceCodec().decode(json.dumps(body).encode(), request(), call)
    body.update(score_semantics="laya_answer_confidence", calibration_digest="b" * 64)
    with pytest.raises(InvalidResponse, match="calibration_missing"):
        LocalChoiceCodec().decode(json.dumps(body).encode(), request(), call)


def test_large_model_projection_refused_before_inference() -> None:
    adapter = LayaInputAdapter(
        schema_version="decision_laya_input.v1",
        state_format="laya_minimal_choice.v1",
        instruction="Select the approved target.",
    )
    original = _input()
    oversized = original.model_copy(update={"options": original.options * 17})
    with pytest.raises(ServingFailure, match="input_unsupported"):
        _model_question(oversized, adapter)


def test_missing_checkpoint_stops_before_model_import(tmp_path) -> None:
    manifest = artifact_bundle(tmp_path)
    with pytest.raises(ServingFailure, match="WAITING_ENV"):
        DecisionService.from_local_checkpoint(
            manifest=manifest, artifact_root=tmp_path / "missing", device="cpu",
            policy=projection_policy(), authorize=lambda _: True, limits=ServingLimits(),
        )


def test_cancelled_thread_keeps_infer_task_until_forward_settles() -> None:
    """Synthetic scheduler probe only; no weights or model score are involved."""
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingLaya(LayaEngine):
            def _infer_sync(self, inputs):
                started.set()
                release.wait(timeout=2)
                return object()

        engine = object.__new__(BlockingLaya)
        task = asyncio.create_task(engine.infer(_input(), asyncio.Event()))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)

    asyncio.run(scenario())
