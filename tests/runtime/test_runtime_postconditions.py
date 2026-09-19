"""Runtime owns mandatory verification before every successful terminal side effect."""

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.evaluator.overview import OverviewPostconditionEvaluator
from app.ports.capability_gateway import ExecutionResult
from app.ports.evaluation import EvaluationScope
from tests.evaluator.test_overview_postconditions import SCOPE, evidence
from tests.runtime.test_production_workflow import component_harness, run_overview


def evaluation_event(h):
    events = [item for item in h.trace.steps if item["event_type"] == "evaluation_recorded"]
    assert len(events) == 1
    return events[0]


def corrupt_aggregation(h):
    original = h.engine.execute

    async def execute(**kwargs):
        result = await original(**kwargs)
        result.output["pending"]["workflows"][0]["title"] = "synthetic-corruption"
        return result

    h.engine.execute = execute


def test_completed_execution_with_wrong_business_result_fails_everywhere():
    h = component_harness()
    corrupt_aggregation(h)
    h.runtime._session_memory.remember_completed = Mock()
    envelope = asyncio.run(run_overview(h))
    assert envelope.status == "failed" and envelope.data is None
    assert envelope.message == "查询已返回，但概览结果未通过核验，本次未展示。"
    assert h.store.status_updates[-1] == ("failed", "internal_error")
    h.runtime._session_memory.remember_completed.assert_not_called()
    assert not any(item["event_type"] == "task_completed" for item in h.trace.steps)
    event = evaluation_event(h)
    assert event["status"] == "failed" and event["error_code"] == "internal_error"
    attributes = event["attributes"]
    assert attributes["execution_status"] == "completed"
    assert attributes["business_status"] == attributes["evaluation_result"] == "failed"
    assert attributes["business_verification"]["reason"] == "pending_mismatch"
    assert "synthetic-corruption" not in repr(h.trace.steps)
    assert h.gateway.execute_capability.await_count == len(h.adapter.calls) == 2


@pytest.mark.parametrize(
    "fault,reason,structure,source",
    [
        ("none", "evidence_missing", "not_checked", "failed"),
        ("version", "unsupported_version", "not_checked", "failed"),
        ("error_code", "evaluator_error", "not_checked", "not_checked"),
        ("snapshot", "output_snapshot_mismatch", "not_checked", "not_checked"),
        ("not_object", "structure_invalid", "failed", "not_checked"),
        ("nan", "structure_invalid", "failed", "not_checked"),
        ("exception", "evaluator_error", "not_checked", "not_checked"),
        ("injection", "evaluator_error", "not_checked", "not_checked"),
    ],
)
def test_required_rule_cannot_be_disabled_by_missing_input_or_injection(
    fault,
    reason,
    structure,
    source,
):
    h = component_harness()
    value, data = evidence()
    version, code = "1.1.0", None
    if fault == "none":
        value = None
    elif fault == "version":
        version = "1.0.0"
    elif fault == "error_code":
        code = "adapter_error"
    elif fault == "snapshot":
        data["pending"]["workflows"][0]["title"] = "synthetic-replacement"
    elif fault == "not_object":
        data = None
    elif fault == "nan":
        data["extra"] = float("nan")
    elif fault == "exception":
        h.runtime._overview_evaluator = Mock(
            spec=OverviewPostconditionEvaluator,
            evaluate=Mock(side_effect=RuntimeError("synthetic-private-exception")),
        )
    else:
        h.runtime._overview_evaluator = None
    execution = ExecutionResult(
        status="completed", data=data, error_code=code, trace_id="trace", postcondition_input=value
    )
    effective, result = h.runtime._evaluate_required_postconditions(
        execution,
        scope=SCOPE,
        capability_id="oa.read_overview",
        version=version,
    )
    assert (effective.status, effective.error_code, effective.data) == (
        "failed",
        "internal_error",
        None,
    )
    assert (result.reason, result.structure_result) == (reason, structure)
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == (source, "not_checked", "not_checked")
    assert execution.status == "completed"
    assert "synthetic-private-exception" not in repr(result)


@pytest.mark.parametrize(
    "fault,reason,code",
    [
        ("output", "execution_not_completed", "adapter_payload_invalid"),
        ("source", "structure_invalid", "internal_error"),
        ("exception", "evaluator_error", "internal_error"),
    ],
)
def test_upstream_structure_failure_and_rule_exception_have_distinct_outcomes(fault, reason, code):
    h = component_harness()
    original = h.engine.execute

    async def execute(**kwargs):
        result = await original(**kwargs)
        if fault == "output":
            result.output["pending"]["returned_count"] = 99
        elif fault == "source":
            result = replace(
                result,
                evaluation_observations=(
                    replace(result.evaluation_observations[0], payload_json='{"workflows":[]}'),
                    result.evaluation_observations[1],
                ),
            )
        return result

    h.engine.execute = execute
    if fault == "exception":
        h.runtime._overview_evaluator.evaluate = Mock(side_effect=ValueError("synthetic-secret"))
    h.runtime._session_memory.remember_completed = Mock()
    envelope = asyncio.run(run_overview(h))
    assert envelope.status == "failed" and envelope.data is None
    assert h.store.status_updates[-1] == ("failed", code)
    assert evaluation_event(h)["attributes"]["business_verification"]["reason"] == reason
    h.runtime._session_memory.remember_completed.assert_not_called()
    assert "synthetic-secret" not in repr(h.trace.steps)


def test_success_uses_detached_output_and_terminal_evaluator_failure_is_noncontrolling():
    h = component_harness()
    value, data = evidence()
    execution = ExecutionResult(
        status="completed", data=data, trace_id="trace", postcondition_input=value
    )
    effective, result = h.runtime._evaluate_required_postconditions(
        execution,
        scope=SCOPE,
        capability_id="oa.read_overview",
        version="1.1.0",
    )
    assert result.result == "passed" and effective.data == data
    data["pending"]["workflows"][0]["title"] = "mutated"
    assert effective.data["pending"]["workflows"][0]["title"] == "待办甲"
    h.runtime._evaluator.evaluate = Mock(side_effect=ValueError("synthetic-terminal-error"))
    envelope = asyncio.run(run_overview(h))
    assert envelope.status == "completed"
    attributes = evaluation_event(h)["attributes"]
    assert attributes["evaluation_result"] == "error"
    assert attributes["business_verification"]["result"] == "passed"


@pytest.mark.parametrize(
    "status,code",
    [
        ("failed", "adapter_payload_invalid"),
        ("denied", "policy_denied"),
        ("binding_required", "identity_unbound"),
        ("timeout", "adapter_timeout"),
        ("waiting_user", "confirm_required"),
        ("no_capability_found", "capability_not_found"),
    ],
)
def test_upstream_failure_preserves_original_code(status, code):
    h = component_harness()
    execution = ExecutionResult(status=status, error_code=code, trace_id="trace")
    effective, result = h.runtime._evaluate_required_postconditions(
        execution,
        scope=SCOPE,
        capability_id="oa.read_overview",
        version="0.0.0",
    )
    assert effective is execution and effective.error_code == code
    assert (result.result, result.reason, result.structure_result) == (
        "not_evaluated",
        "execution_not_completed",
        "not_checked",
    )


def test_observations_capture_gateway_values_before_aggregation():
    h = component_harness()
    original = h.engine.execute
    captured = []

    async def execute(**kwargs):
        result = await original(**kwargs)
        captured.append(result)
        return result

    h.engine.execute = execute
    envelope = asyncio.run(run_overview(h))
    assert envelope.status == "completed"
    result = captured[0]
    observation = result.evaluation_observations[0]
    call = h.gateway.execute_capability.await_args_list[0].args
    assert observation.scope == EvaluationScope(
        call[0], call[5].request_id, call[1], call[5].tenant_id, call[2]
    )
    assert (observation.step_id, observation.capability_id, observation.attempt) == (
        "pending",
        "oa.list_pending_workflows",
        1,
    )
    result.output["pending"]["workflows"][0]["title"] = "changed"
    result.step_outputs["pending"]["workflows"][0]["title"] = "changed-again"
    assert "changed" not in observation.payload_json
    assert "待办甲" in observation.payload_json


def test_observations_are_per_run_and_do_not_create_extra_calls():
    h = component_harness()
    seen = []
    evaluate = h.runtime._overview_evaluator.evaluate

    def record(scope, capability, version, value):
        seen.append(value)
        return evaluate(scope, capability, version, value)

    h.runtime._overview_evaluator.evaluate = record

    async def concurrent():
        return await asyncio.gather(
            run_overview(h, user="alpha", tenant="tenant-a", sid="same-session"),
            run_overview(h, user="beta", tenant="tenant-b", sid="same-session"),
        )

    results = asyncio.run(concurrent())
    assert [result.status for result in results] == ["completed", "completed"]
    assert len(seen) == 2 and seen[0].scope != seen[1].scope
    assert {item.scope.ai_user_id for item in seen} == {"alpha", "beta"}
    for item in seen:
        assert all(observation.scope == item.scope for observation in item.observations)
    assert h.gateway.execute_capability.await_count == len(h.adapter.calls) == 4


def test_snapshot_failure_does_not_query_again(monkeypatch):
    import app.workflow.engine as engine_module

    def broken_snapshot(_value):
        raise ValueError("synthetic-snapshot-failure")

    monkeypatch.setattr(engine_module, "canonical_object", broken_snapshot)
    h = component_harness()
    response = asyncio.run(run_overview(h))
    assert response.status == "failed" and response.data is None
    assert (
        evaluation_event(h)["attributes"]["business_verification"]["reason"] == "evidence_missing"
    )
    assert h.gateway.execute_capability.await_count == len(h.adapter.calls) == 2
    assert "synthetic-snapshot-failure" not in repr(h.trace.steps)


def test_malformed_snapshot_is_a_rule_error_and_output_key_order_is_irrelevant():
    h = component_harness()
    value, data = evidence()
    reordered = {"messages": data["messages"], "pending": data["pending"]}
    execution = ExecutionResult(
        status="completed", trace_id="trace", data=reordered, postcondition_input=value
    )
    effective, result = h.runtime._evaluate_required_postconditions(
        execution,
        scope=SCOPE,
        capability_id="oa.read_overview",
        version="1.1.0",
    )
    assert effective.status == "completed" and result.result == "passed"
    malformed = replace(
        value,
        observations=(
            replace(value.observations[0], payload_json="invalid-json"),
            value.observations[1],
        ),
    )
    effective, result = h.runtime._evaluate_required_postconditions(
        execution.model_copy(update={"postcondition_input": malformed}),
        scope=SCOPE,
        capability_id="oa.read_overview",
        version="1.1.0",
    )
    assert effective.status == "failed" and effective.error_code == "internal_error"
    assert (result.result, result.reason, result.structure_result) == (
        "error",
        "evaluator_error",
        "not_checked",
    )
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("not_checked", "not_checked", "not_checked")
