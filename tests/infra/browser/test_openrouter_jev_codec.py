"""Offline OpenRouter Decisions API choice contract, with an explicit Jev pin."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import httpx2
import pytest

from app.browser_skill.models import DecisionCallContext, DecisionResult, ModelManifest
from app.infra.browser.decision_adapters import OpenRouterJevCodec, TypeSafeCodec
from app.infra.browser.systemone_http import DecisionDeployment, DecisionHTTPProvider
from tests.browser_skill.factories import DIGEST, context, request, source


def _context(deployment_model: str = "typesafe/jev-1.13-20260917") -> DecisionCallContext:
    # This is a test fixture pin from the published example, not a runtime default.
    return replace(
        context(),
        manifest=ModelManifest(
            request_model="typesafe/jev-1.13",
            deployment_model=deployment_model,
            manifest_digest=DIGEST,
        ),
    )


def _response() -> dict[str, Any]:
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": {
            "select_target": {
                "type": "choice",
                "choice": "target_a",
                "probabilities": {"target_a": 1, "target_b": 0},
                "confidence": 1,
            }
        },
        "usage": {"input_tokens": 357, "output_tokens": 38, "cost": 0.000014994},
        "id": "gen-dec-synthetic-001",
        "provider": "TypeSafe",
    }


def _run(
    body: object | bytes, *, ctx: DecisionCallContext | None = None
) -> tuple[DecisionResult, list[httpx2.Request]]:
    calls: list[httpx2.Request] = []
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def execute() -> DecisionResult:
        def handler(req: httpx2.Request) -> httpx2.Response:
            calls.append(req)
            return httpx2.Response(200, content=raw)

        deployment = DecisionDeployment(
            disposition="cloud_synthetic",
            endpoint_origin="https://openrouter.ai",
            manifest_digest=DIGEST,
            registered_sources=(source(),),
        )
        async with httpx2.AsyncClient(
            base_url="https://openrouter.ai",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            return await DecisionHTTPProvider(client, OpenRouterJevCodec(), deployment).decide(
                request(), ctx or _context()
            )

    return asyncio.run(execute()), calls


def test_openrouter_jev_post_uses_typesafe_choice_wire_and_explicit_manifest() -> None:
    result, calls = _run(_response())
    assert result.status == "selected" and result.selected is not None
    assert result.selected.target_id == "target_a"
    assert len(calls) == 1
    assert str(calls[0].url) == "https://openrouter.ai/api/alpha/decisions"
    assert calls[0].method == "POST"
    assert calls[0].content == OpenRouterJevCodec().encode(request(), _context())
    wire = json.loads(calls[0].content)
    assert set(wire) == {"model", "state", "questions"}
    assert wire["model"] == "typesafe/jev-1.13"
    assert set(wire["questions"]["select_target"]["criteria"]) == {"target_a", "target_b"}
    assert b"tenant" not in calls[0].content and b"session" not in calls[0].content


def test_deployment_model_is_the_supplied_pin_not_the_documentation_example() -> None:
    body = _response()
    body["model"] = "typesafe/jev-1.13-operator-pin"
    result, _ = _run(body, ctx=_context(deployment_model=body["model"]))
    assert result.status == "selected"

    mismatch, _ = _run(body)
    assert mismatch.error == "model_mismatch" and mismatch.selected is None


def test_openrouter_metadata_does_not_expand_direct_typesafe_response_contract() -> None:
    with pytest.raises(ValueError):
        TypeSafeCodec().decode(json.dumps(_response()).encode(), request(), _context())


@pytest.mark.parametrize(
    "change",
    [
        "wrong_provider",
        "missing_provider",
        "missing_id",
        "missing_cost",
        "negative_cost",
        "bool_cost",
        "overlong_id",
        "overlong_model",
        "extra_top_level",
        "extra_usage",
        "extra_answer",
        "invented_choice",
        "extra_candidate",
        "missing_candidate",
        "bad_sum",
        "nonmax_choice",
        "nan_cost",
        "infinite_probability",
        "duplicate_provider",
        "duplicate_choice",
    ],
)
def test_invalid_openrouter_response_never_selects(change: str) -> None:
    body = deepcopy(_response())
    answer = body["answers"]["select_target"]
    if change == "wrong_provider":
        body["provider"] = "OtherProvider"
    elif change == "missing_provider":
        del body["provider"]
    elif change == "missing_id":
        del body["id"]
    elif change == "missing_cost":
        del body["usage"]["cost"]
    elif change == "negative_cost":
        body["usage"]["cost"] = -0.01
    elif change == "bool_cost":
        body["usage"]["cost"] = True
    elif change == "overlong_id":
        body["id"] = "x" * 97
    elif change == "overlong_model":
        body["model"] = "x" * 257
    elif change == "extra_top_level":
        body["operation"] = "navigate"
    elif change == "extra_usage":
        body["usage"]["unapproved"] = 1
    elif change == "extra_answer":
        answer["operation"] = "navigate"
    elif change == "invented_choice":
        answer["choice"] = "invented"
    elif change == "extra_candidate":
        answer["probabilities"]["invented"] = 0
    elif change == "missing_candidate":
        del answer["probabilities"]["target_b"]
    elif change == "bad_sum":
        answer["probabilities"]["target_a"] = 0.5
    elif change == "nonmax_choice":
        answer["choice"] = "target_b"
    elif change == "nan_cost":
        body["usage"]["cost"] = float("nan")
    elif change == "infinite_probability":
        answer["probabilities"]["target_a"] = float("inf")
    else:
        raw = json.dumps(body)
        if change == "duplicate_provider":
            raw = raw.replace(
                '"provider": "TypeSafe"', '"provider": "TypeSafe", "provider": "TypeSafe"'
            )
        else:
            raw = raw.replace('"choice": "target_a"', '"choice": "target_a", "choice": "target_a"')
        result, calls = _run(raw.encode())
        assert result.error == "invalid_response" and result.selected is None
        assert len(calls) == 1
        return

    result, calls = _run(body)
    assert result.error == "invalid_response" and result.selected is None
    assert len(calls) == 1


def test_openrouter_choice_retains_confidence_and_tie_handling() -> None:
    body = _response()
    body["answers"]["select_target"]["confidence"] = 0.2
    low, _ = _run(body)
    assert low.status == "abstained" and low.selected is None

    body["answers"]["select_target"]["probabilities"] = {"target_a": 0.5, "target_b": 0.5}
    tied, _ = _run(body)
    assert tied.status == "ambiguous" and tied.selected is None
