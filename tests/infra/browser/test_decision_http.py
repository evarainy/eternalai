from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

import httpx2
import pytest

from app.browser_skill.models import DecisionBudget, DecisionSource
from app.infra.browser.decision_adapters import LocalChoiceCodec, TypeSafeCodec
from app.infra.browser.systemone_http import DecisionDeployment, DecisionHTTPProvider
from tests.browser_skill.factories import DIGEST, context, request, source


def deployment() -> DecisionDeployment:
    return DecisionDeployment(
        disposition="cloud_synthetic",
        endpoint_origin="https://decision.invalid",
        manifest_digest=DIGEST,
        registered_sources=(source(),),
    )


def response_body() -> dict[str, object]:
    return {
        "model": "jev-test-1",
        "answers": {
            "select_target": {
                "type": "choice",
                "choice": "target_a",
                "probabilities": {"target_a": 0.95, "target_b": 0.05},
                "confidence": 0.95,
            }
        },
        "usage": {"input_tokens": 20, "output_tokens": 10},
    }


def run(body: object, status: int = 200):
    calls = []

    async def execute():
        def handler(req):
            calls.append(req)
            return httpx2.Response(status, content=json.dumps(body).encode())

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            return await DecisionHTTPProvider(client, TypeSafeCodec(), deployment()).decide(
                request(), context()
            )

    return asyncio.run(execute()), calls


def test_actual_typesafe_http_serialization_and_strict_decode() -> None:
    result, calls = run(response_body())
    assert result.status == "selected"
    assert result.selected.target_id == "target_a"
    assert len(calls) == 1 and calls[0].url.path == "/v1/systemone"
    wire = json.loads(calls[0].content)
    assert set(wire) == {"model", "state", "questions"}
    assert wire["model"] == "jev-test-alias"
    assert set(wire["questions"]["select_target"]["criteria"]) == {"target_a", "target_b"}
    assert wire["state"]["scope"]["frame_path"][1] == {"frame_id": "nested", "frame_epoch": 2}
    assert b"tenant" not in calls[0].content and b"deadline" not in calls[0].content
    assert b"fixture.invalid" not in calls[0].content


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "missing",
        "nan",
        "infinity",
        "extra_probability",
        "missing_probability",
        "bad_sum",
        "nonmax",
        "wrong_type",
        "bool_probability",
        "extra_action",
    ],
)
def test_unsafe_http_response_never_yields_selection(mutation: str) -> None:
    body = response_body()
    answer = body["answers"]["select_target"]
    if mutation == "unknown":
        answer["choice"] = "invented"
    elif mutation == "missing":
        del answer["confidence"]
    elif mutation in {"nan", "infinity"}:
        answer["confidence"] = float("nan" if mutation == "nan" else "inf")
    elif mutation == "extra_probability":
        answer["probabilities"]["invented"] = 0.0
    elif mutation == "missing_probability":
        del answer["probabilities"]["target_b"]
    elif mutation == "bad_sum":
        answer["probabilities"]["target_b"] = 0.4
    elif mutation == "nonmax":
        answer["choice"] = "target_b"
    elif mutation == "wrong_type":
        answer["type"] = "score"
    elif mutation == "bool_probability":
        answer["probabilities"]["target_a"] = True
    else:
        answer["operation"] = "navigate"
    result, calls = run(body)
    assert result.error == "invalid_response" and result.selected is None
    assert len(calls) == 1


def test_pinned_deployment_and_low_confidence_have_distinct_results() -> None:
    body = response_body()
    body["model"] = "jev-test-2"
    result, _ = run(body)
    assert result.error == "model_mismatch"
    body = response_body()
    body["answers"]["select_target"]["confidence"] = 0.2
    result, _ = run(body)
    assert result.status == "abstained" and result.selected is None
    body["answers"]["select_target"]["probabilities"] = {"target_a": 0.5, "target_b": 0.5}
    result, _ = run(body)
    assert result.status == "ambiguous" and result.selected is None


@pytest.mark.parametrize(
    "status,error,reason",
    [
        (401, "unavailable", "unauthorized"),
        (422, "input_unsupported", "bad_input"),
        (429, "overloaded", "rate_limited"),
        (529, "overloaded", "capacity"),
        (504, "timeout", "deadline"),
        (499, "cancelled", "cancelled"),
        (412, "unavailable", "transport"),
        (500, "unavailable", "transport"),
        (302, "unavailable", "transport"),
    ],
)
def test_http_failures_are_distinct_and_never_retried(status, error, reason) -> None:
    result, calls = run({"diagnostic": "untrusted synthetic marker"}, status)
    assert (result.error, result.reason, len(calls)) == (error, reason, 1)
    assert "untrusted synthetic marker" not in result.model_dump_json()


@pytest.mark.parametrize(
    "change", ["source_missing", "source_id", "origin", "fixture", "manifest", "freshness_missing"]
)
def test_unregistered_source_and_manifest_are_blocked_before_dispatch(change: str) -> None:
    async def execute():
        calls = []

        def handler(req):
            calls.append(req)
            return httpx2.Response(200, json=response_body())

        ctx = context()
        if change == "source_missing":
            ctx = replace(ctx, source=None)
        elif change == "manifest":
            ctx = replace(
                ctx, manifest=ctx.manifest.model_copy(update={"manifest_digest": "b" * 64})
            )
        elif change == "freshness_missing":
            ctx = replace(ctx, current_targets=None)
        else:
            changes = {
                "source_id": {"source_id": "unregistered"},
                "origin": {"origin": "https://real.invalid"},
                "fixture": {"fixture_digest": "b" * 64},
            }
            ctx = replace(
                ctx,
                source=DecisionSource.model_validate({**source().model_dump(), **changes[change]}),
            )
        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            result = await DecisionHTTPProvider(client, TypeSafeCodec(), deployment()).decide(
                request(), ctx
            )
        return result, calls

    result, calls = asyncio.run(execute())
    assert result.error == "input_unsupported" and calls == []


@pytest.mark.parametrize("phase", ["before", "during"])
@pytest.mark.parametrize("field,value", [("candidate_epoch", 9), ("target_id", "replaced")])
def test_target_generation_changed_before_or_during_http_is_rejected(phase, field, value) -> None:
    async def execute():
        refs = [c.ref for c in request().candidates]
        calls = []

        def mutate():
            refs[0] = refs[0].model_copy(update={field: value})

        def handler(req):
            calls.append(req)
            if phase == "during":
                mutate()
            return httpx2.Response(200, json=response_body())

        if phase == "before":
            mutate()
        ctx = replace(context(), current_targets=lambda: tuple(refs))
        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            result = await DecisionHTTPProvider(client, TypeSafeCodec(), deployment()).decide(
                request(), ctx
            )
        return result, len(calls)

    result, count = asyncio.run(execute())
    assert result.error == "invalid_response" and result.selected is None
    assert count == (0 if phase == "before" else 1)


def test_deadline_cancellation_and_request_budget_prevent_dispatch() -> None:
    async def execute():
        calls = []

        def handler(req):
            calls.append(req)
            return httpx2.Response(200, json=response_body())

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            provider = DecisionHTTPProvider(client, TypeSafeCodec(), deployment())
            timeout = await provider.decide(
                request(), replace(context(), deadline_monotonic=time.monotonic() - 1)
            )
            cancelled = context()
            cancelled.cancellation.set()
            cancel = await provider.decide(request(), cancelled)
            budget = await provider.decide(
                request(), replace(context(), budget=DecisionBudget(max_request_bytes=1))
            )
        return timeout, cancel, budget, calls

    timeout, cancel, budget, calls = asyncio.run(execute())
    assert (timeout.error, cancel.error, budget.error) == (
        "timeout",
        "cancelled",
        "input_unsupported",
    )
    assert calls == []


def test_inflight_cancel_reaps_http_task() -> None:
    async def execute():
        ctx = context()
        reaped = asyncio.Event()

        async def handler(req):
            ctx.cancellation.set()
            try:
                await asyncio.Event().wait()
            finally:
                reaped.set()

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            result = await DecisionHTTPProvider(client, TypeSafeCodec(), deployment()).decide(
                request(), ctx
            )
        return result, reaped.is_set()

    result, reaped = asyncio.run(execute())
    assert result.error == "cancelled" and reaped


@pytest.mark.parametrize("reason", ["cancelled", "timeout"])
def test_unsettled_transport_does_not_hold_the_decision_past_its_deadline(reason: str) -> None:
    async def execute():
        ctx = context()
        if reason == "timeout":
            ctx = replace(ctx, deadline_monotonic=time.monotonic() + 0.04)
        started, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(req):
            calls.append(req)
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate a transport that has not acknowledged cancellation.
                await release.wait()
            finally:
                settled.set()
            return httpx2.Response(200, json=response_body())

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            provider = DecisionHTTPProvider(client, TypeSafeCodec(), deployment())
            job = asyncio.create_task(provider.decide(request(), ctx))
            await started.wait()
            timer = asyncio.get_running_loop().call_later(0.45, release.set)
            before = time.monotonic()
            if reason == "cancelled":
                ctx.cancellation.set()
            try:
                result = await job
                elapsed = time.monotonic() - before
                unsettled_when_returned = not settled.is_set()
            finally:
                timer.cancel()
                release.set()
                await settled.wait()
                await asyncio.sleep(0)
        return result, elapsed, unsettled_when_returned, len(calls)

    result, elapsed, unsettled, count = asyncio.run(execute())
    assert result.error == reason and result.selected is None
    assert elapsed < 0.2 and unsettled and count == 1


def test_local_deployment_precondition_rejection_keeps_model_mismatch() -> None:
    async def execute():
        calls = []

        def handler(req):
            calls.append(req)
            return httpx2.Response(412, json={"diagnostic": "untrusted synthetic marker"})

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            result = await DecisionHTTPProvider(client, LocalChoiceCodec(), deployment()).decide(
                request(), context()
            )
        return result, calls

    result, calls = asyncio.run(execute())
    assert (result.error, result.reason, len(calls)) == ("model_mismatch", "model", 1)
    assert result.selected is None and calls[0].url.path == "/select"
    assert "untrusted synthetic marker" not in result.model_dump_json()


def test_distinct_local_codec_goes_through_http_and_binds_snapshot() -> None:
    async def execute(stale: bool):
        calls = []

        def handler(req):
            calls.append(json.loads(req.content))
            snapshot = request().scope.model_dump(mode="json")
            if stale:
                snapshot["page_epoch"] = 9
            return httpx2.Response(
                200,
                json={
                    "schema_version": "browser_choice.v1",
                    "request_id": "request",
                    "deployment": "jev-test-1",
                    "snapshot": snapshot,
                    "outcome": "selected",
                    "selected_id": "target_a",
                    "distribution": [{"id": "target_a", "p": 0.95}, {"id": "target_b", "p": 0.05}],
                    "certainty": 0.95,
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                },
            )

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            result = await DecisionHTTPProvider(client, LocalChoiceCodec(), deployment()).decide(
                request(), context()
            )
        return result, calls

    result, calls = asyncio.run(execute(False))
    assert result.selected.target_id == "target_a"
    assert "options" in calls[0] and "questions" not in calls[0]
    assert calls[0]["options"][0]["id"] == "target_a"
    stale, _ = asyncio.run(execute(True))
    assert stale.error == "invalid_response" and stale.selected is None


@pytest.mark.parametrize("mode", ["duplicate", "oversized", "timeout", "network_failure"])
def test_wire_and_transport_failure_boundaries(mode: str) -> None:
    async def execute():
        calls = []

        def handler(req):
            calls.append(req)
            if mode == "timeout":
                raise httpx2.ReadTimeout("synthetic diagnostic")
            if mode == "network_failure":
                raise httpx2.ConnectError("synthetic diagnostic")
            raw = json.dumps(response_body()).encode()
            if mode == "duplicate":
                raw = raw.replace(b'"model":', b'"model":"duplicate", "model":')
            return httpx2.Response(200, content=raw)

        ctx = context()
        if mode == "oversized":
            ctx = replace(ctx, budget=DecisionBudget(max_response_bytes=1))
        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            result = await DecisionHTTPProvider(client, TypeSafeCodec(), deployment()).decide(
                request(),
                ctx,
            )
        return result, len(calls)

    result, calls = asyncio.run(execute())
    expected = {"timeout": "timeout", "network_failure": "unavailable"}.get(
        mode, "invalid_response"
    )
    assert result.error == expected and result.selected is None and calls == 1
    assert "synthetic diagnostic" not in result.model_dump_json()


@pytest.mark.parametrize("status", ["abstained", "ambiguous", "unsupported"])
def test_local_codec_preserves_explicit_nonselection(status: str) -> None:
    raw = json.dumps(
        {
            "schema_version": "browser_choice.v1",
            "request_id": "request",
            "deployment": "jev-test-1",
            "snapshot": request().scope.model_dump(mode="json"),
            "outcome": status,
            "selected_id": None,
            "distribution": [],
            "certainty": 0.0,
            "usage": {"input_tokens": 20, "output_tokens": 0},
        }
    ).encode()
    result = LocalChoiceCodec().decode(raw, request(), context())
    assert result.status == status and result.selected is None


@pytest.mark.parametrize("change", ["page_epoch", "frame_epoch", "region_digest"])
def test_relevant_scope_changes_while_typesafe_is_inflight_are_rejected(change: str) -> None:
    async def execute():
        refs = [candidate.ref for candidate in request().candidates]

        def handler(req):
            stamp = refs[0].scope
            if change == "frame_epoch":
                path = stamp.frame_path
                update = {"frame_path": (path[0], path[1].model_copy(update={"frame_epoch": 9}))}
            else:
                update = {change: "b" * 64 if change == "region_digest" else 9}
            refs[0] = refs[0].model_copy(update={"scope": stamp.model_copy(update=update)})
            return httpx2.Response(200, json=response_body())

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid",
            trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            return await DecisionHTTPProvider(client, TypeSafeCodec(), deployment()).decide(
                request(),
                replace(context(), current_targets=lambda: tuple(refs)),
            )

    result = asyncio.run(execute())
    assert result.error == "invalid_response" and result.selected is None


@pytest.mark.parametrize("codec", [TypeSafeCodec(), LocalChoiceCodec()])
def test_both_codecs_project_only_explicit_safe_row_and_column_labels(codec) -> None:
    req = request()
    candidate = req.candidates[0].model_copy(
        update={"row_label": "Pending", "column_label": "Status"}
    )
    req = req.model_copy(update={"candidates": (candidate, req.candidates[1])})
    wire = json.loads(codec.encode(req, context()))
    if isinstance(codec, TypeSafeCodec):
        item = wire["questions"]["select_target"]["criteria"][candidate.ref.target_id]
    else:
        item = wire["options"][0]
    assert item["row_label"] == "Pending" and item["column_label"] == "Status"
    assert "value" not in item and "owner" not in item and "business_key" not in item
