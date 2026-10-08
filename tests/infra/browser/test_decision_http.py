from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest

from app.browser_skill.models import DecisionBudget, DecisionSource
from app.infra.browser.decision_adapters import LocalChoiceCodec, TypeSafeCodec
from app.infra.browser.local_read_execution import _FencedDecision
from app.infra.browser.read_execution import VerifiedBrowserReadExecution
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


@pytest.mark.parametrize("boundary,expected", [
    ("status", {"http_status": 504, "http_headers_received": True, "http_exception": "none"}),
    ("timeout", {"http_status": "unknown", "http_headers_received": False,
                 "http_exception": "timeout",
                 "http_exception_kind": "read_timeout", "http_timeout_phase": "read"}),
    ("transport", {"http_status": "unknown", "http_headers_received": False,
                   "http_exception": "transport",
                   "http_exception_kind": "connect_error", "http_timeout_phase": "none"}),
])
def test_http_collector_records_only_same_invocation_transport_facts(boundary, expected) -> None:
    async def scenario():
        def handler(req):
            if boundary == "timeout":
                raise httpx2.ReadTimeout("private exception marker", request=req)
            if boundary == "transport":
                raise httpx2.ConnectError("private exception marker", request=req)
            return httpx2.Response(504, content=b"private body marker")

        async with httpx2.AsyncClient(base_url="https://decision.invalid", trust_env=False,
                                     transport=httpx2.MockTransport(handler)) as client:
            collector = {}
            result = await DecisionHTTPProvider(
                client, TypeSafeCodec(), deployment()
            )._decide_with_collector(request(), context(), collector)
            assert collector == expected
            assert result.error == ("unavailable" if boundary == "transport" else "timeout")
            assert "private" not in json.dumps(collector)

    asyncio.run(scenario())


class _SyntheticUnknownHTTPError(httpx2.HTTPError):
    def __str__(self) -> str:
        raise AssertionError("exception text must not be read")


@pytest.mark.parametrize("error_type,kind,phase,category", [
    (httpx2.ConnectTimeout, "connect_timeout", "connect", "timeout"),
    (httpx2.ReadTimeout, "read_timeout", "read", "timeout"),
    (httpx2.WriteTimeout, "write_timeout", "write", "timeout"),
    (httpx2.PoolTimeout, "pool_timeout", "pool", "timeout"),
    (httpx2.TimeoutException, "timeout", "unknown", "timeout"),
    (httpx2.ConnectError, "connect_error", "none", "transport"),
    (httpx2.ReadError, "read_error", "none", "transport"),
    (httpx2.WriteError, "write_error", "none", "transport"),
    (httpx2.CloseError, "close_error", "none", "transport"),
    (httpx2.ProxyError, "proxy_error", "none", "transport"),
    (httpx2.LocalProtocolError, "local_protocol_error", "none", "transport"),
    (httpx2.RemoteProtocolError, "remote_protocol_error", "none", "transport"),
    (httpx2.UnsupportedProtocol, "unsupported_protocol", "none", "transport"),
    (httpx2.DecodingError, "decoding_error", "none", "transport"),
    (httpx2.HTTPError, "http_error", "none", "transport"),
    (_SyntheticUnknownHTTPError, "http_error", "none", "transport"),
])
def test_transport_classification_reaches_bounded_trace_without_text_or_retry(
    error_type, kind: str, phase: str, category: str, caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "synthetic-exception-detail-must-not-leave"

    async def scenario() -> None:
        calls = []

        def handler(req):
            calls.append(req)
            raise error_type(marker)

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid", trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            provider = DecisionHTTPProvider(client, TypeSafeCodec(), deployment())
            factory = SimpleNamespace(decision=provider, _fence=AsyncMock())
            decision = _FencedDecision(factory, object())
            result = await decision.decide(request(), context())
            diagnostic = decision.http_diagnostic()
            assert diagnostic == {
                "http_status": "unknown", "http_headers_received": False,
                "http_exception": category, "http_exception_kind": kind,
                "http_timeout_phase": phase,
            }
            expected_error = "timeout" if category == "timeout" else "unavailable"
            expected_reason = "deadline" if category == "timeout" else "transport"
            assert (result.error, result.reason, result.selected) == (
                expected_error, expected_reason, None,
            )
            assert len(calls) == 1 and factory._fence.await_count == 2
            writer, run_ref = AsyncMock(), Mock()
            bridge = VerifiedBrowserReadExecution(
                Mock(), Mock(), Mock(), Mock(), result_digest_key=b"d" * 32,
                record_diagnostic=writer,
            )
            await bridge._record_failure_diagnostic(
                run_ref, None, decision=result, http_diagnostic=diagnostic,
            )
            assert writer.await_count == 1
            assert writer.await_args.args[0] is run_ref
            attributes = writer.await_args.args[1]
            assert attributes == {
                "browser_read_outcome": "failed",
                "browser_completion_scope": "adapter_execution",
                "browser_timing_clock": "monotonic_relative_ms",
                "browser_stage_parent": "worker_adapter_execution",
                "browser_stage_offsets_origin": "adapter_execution",
                "read_stage_offsets_origin": "read_bridge_executor",
                "decision_http_status": "unknown", "decision_http_headers_received": False,
                "decision_http_exception": category, "decision_http_exception_kind": kind,
                "decision_http_timeout_phase": phase,
                "decision_error": expected_error, "decision_reason": expected_reason,
            }
            assert marker not in (
                json.dumps(diagnostic) + json.dumps(attributes) + result.model_dump_json()
            )
            assert "_SyntheticUnknownHTTPError" not in json.dumps(attributes)

    asyncio.run(scenario())
    assert marker not in caplog.text


@pytest.mark.parametrize("error_type,kind,phase", [
    (httpx2.ReadTimeout, "read_timeout", "read"),
    (httpx2.ReadError, "read_error", "none"),
])
def test_transport_classification_preserves_headers_when_response_body_fails(
    error_type, kind: str, phase: str,
) -> None:
    async def scenario() -> None:
        class FailedBody(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield b"{"
                raise error_type("synthetic body failure detail")

        calls = []

        def handler(req):
            calls.append(req)
            return httpx2.Response(200, stream=FailedBody())

        async with httpx2.AsyncClient(
            base_url="https://decision.invalid", trust_env=False,
            transport=httpx2.MockTransport(handler),
        ) as client:
            collector = {}
            provider = DecisionHTTPProvider(client, TypeSafeCodec(), deployment())
            result = await provider._decide_with_collector(
                request(), context(), collector,
            )
        assert collector == {
            "http_status": 200, "http_headers_received": True,
            "http_exception": "timeout" if phase == "read" else "transport",
            "http_exception_kind": kind, "http_timeout_phase": phase,
        }
        assert result.error == ("timeout" if phase == "read" else "unavailable")
        assert result.selected is None and len(calls) == 1
        assert "synthetic body failure detail" not in (
            json.dumps(collector) + result.model_dump_json()
        )

    asyncio.run(scenario())


def test_late_cancelled_http_task_cannot_change_frozen_or_next_invocation_summary() -> None:
    async def scenario():
        entered, resume = asyncio.Event(), asyncio.Event()
        calls = 0

        async def handler(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                try:
                    await resume.wait()
                except asyncio.CancelledError:
                    await resume.wait()  # Simulate a transport completing after cancellation.
                return httpx2.Response(401, content=b"late private response")
            return httpx2.Response(200, json=response_body())

        async with httpx2.AsyncClient(base_url="https://decision.invalid", trust_env=False,
                                     transport=httpx2.MockTransport(handler)) as client:
            provider = DecisionHTTPProvider(client, TypeSafeCodec(), deployment())
            factory = SimpleNamespace(decision=provider, _fence=AsyncMock())
            decision = _FencedDecision(factory, object())
            first_context = context()
            first = asyncio.create_task(decision.decide(request(), first_context))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                first_context.cancellation.set()
                assert (await asyncio.wait_for(first, 2)).error == "cancelled"
                frozen = decision.http_diagnostic()
                assert frozen == {
                    "http_status": "unknown",
                    "http_headers_received": False,
                    "http_exception": "none",
                }
                pending = tuple(provider._pending)
                assert len(pending) == 1
                assert (await decision.decide(request(), context())).status == "selected"
                second = decision.http_diagnostic()
                assert second == {
                    "http_status": 200,
                    "http_headers_received": True,
                    "http_exception": "none",
                }
                resume.set()
                await asyncio.wait_for(asyncio.gather(*pending), 2)
                assert decision.http_diagnostic() == second
                assert frozen == {
                    "http_status": "unknown",
                    "http_headers_received": False,
                    "http_exception": "none",
                }
                assert factory._fence.await_count == 4 and calls == 2
            finally:
                resume.set()
                await asyncio.gather(first, *tuple(provider._pending), return_exceptions=True)

    asyncio.run(scenario())


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
