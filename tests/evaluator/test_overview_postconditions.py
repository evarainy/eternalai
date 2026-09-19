"""Independent source snapshots versus schema-valid, potentially corrupted aggregation."""

from copy import deepcopy
from dataclasses import replace

import pytest

from app.evaluator.overview import OverviewPostconditionEvaluator, canonical_object
from app.infra.workflow.catalog import OAReadOverviewOutput
from app.ports.evaluation import EvaluationScope, OverviewEvaluationInput, StepObservation
from tests.runtime.test_production_workflow import overview_data

SCOPE = EvaluationScope("task", "trace", "session", "tenant", "user")


def evidence(*, count=1, complete=False):
    data = overview_data(complete=complete, empty=count == 0)
    if count == 2:
        pending = deepcopy(data["pending"]["workflows"][0])
        pending.update(todo_id="synthetic-second", title="待办丙")
        data["pending"]["workflows"].append(pending)
        data["pending"].update(returned_count=2, authoritative_count=2)
        message = deepcopy(data["messages"]["messages"][0])
        message.update(message_id="synthetic-second-message", title="消息丁")
        data["messages"]["messages"].append(message)
        data["messages"]["returned_count"] = 2
    observations = tuple(
        StepObservation(
            SCOPE, "oa.read_overview", "1.1.0", step, capability, 1, canonical_object(data[step])
        )
        for step, capability in (
            ("pending", "oa.list_pending_workflows"),
            ("messages", "oa.list_system_messages"),
        )
    )
    return OverviewEvaluationInput(
        SCOPE,
        "oa.read_overview",
        "1.1.0",
        "oa_read_overview_v1",
        "{}",
        observations,
        canonical_object(data),
        "passed",
    ), data


def evaluate(value, *, scope=SCOPE, version="1.1.0"):
    return OverviewPostconditionEvaluator().evaluate(scope, "oa.read_overview", version, value)


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("complete", [False, True])
def test_valid_empty_complete_and_partial_collections_pass(count, complete):
    value, data = evidence(count=count, complete=complete)
    result = evaluate(value)
    assert result.result == result.structure_result == "passed"
    assert result.reason == "postconditions_satisfied"
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("passed", "passed", "passed")
    assert data["messages"]["is_complete"] is complete


@pytest.mark.parametrize("fault", ["todo_id", "title", "swap", "drop", "empty", "count"])
def test_schema_valid_wrong_item_or_title_fails(fault):
    value, data = evidence(count=2)
    pending = data["pending"]
    if fault in {"todo_id", "title"}:
        pending["workflows"][0][fault] = "synthetic-replacement"
    elif fault == "swap":
        pending["workflows"].reverse()
    else:
        pending["workflows"] = pending["workflows"][:1] if fault != "empty" else []
        pending["returned_count"] = pending["authoritative_count"] = len(pending["workflows"])
    OAReadOverviewOutput.model_validate(data, strict=True)
    result = evaluate(replace(value, output_json=canonical_object(data)))
    assert (result.result, result.reason, result.structure_result) == (
        "failed",
        "pending_mismatch",
        "passed",
    )
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("passed", "failed", "not_checked")


@pytest.mark.parametrize("fault", ["complete", "title", "drop", "swap", "missing_null"])
def test_partial_messages_cannot_be_claimed_complete(fault):
    value, data = evidence(count=2)
    messages = data["messages"]
    if fault == "complete":
        messages["is_complete"] = True
    elif fault == "title":
        messages["messages"][0]["title"] = "synthetic-replacement"
    elif fault == "drop":
        messages["messages"] = []
        messages["returned_count"] = 0
    elif fault == "swap":
        messages["messages"].reverse()
    else:
        del messages["messages"][0]["link"]
    result = evaluate(replace(value, output_json=canonical_object(data)))
    assert (result.result, result.reason) == ("failed", "messages_mismatch")
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("passed", "passed", "failed")


@pytest.mark.parametrize("field", ["task_id", "trace_id", "session_id", "tenant_id", "ai_user_id"])
@pytest.mark.parametrize("location", ["input", "observation"])
def test_scope_fields_fail_closed(field, location):
    value, _ = evidence()
    foreign = replace(SCOPE, **{field: "foreign"})
    if location == "input":
        value = replace(value, scope=foreign)
    else:
        value = replace(
            value,
            observations=(replace(value.observations[0], scope=foreign), value.observations[1]),
        )
    result = evaluate(value)
    assert (result.result, result.reason, result.structure_result) == (
        "error",
        "evidence_scope_mismatch",
        "not_checked",
    )
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("failed", "not_checked", "not_checked")


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("version", "unsupported_version"),
        ("none", "evidence_missing"),
        ("missing", "evidence_missing"),
        ("request", "request_contract_invalid"),
        ("extra", "evidence_step_mismatch"),
        ("order", "evidence_step_mismatch"),
        ("mapping", "evidence_step_mismatch"),
        ("workflow", "evidence_scope_mismatch"),
        ("input_version", "evidence_scope_mismatch"),
        ("observation_version", "evidence_scope_mismatch"),
        ("observation_workflow", "evidence_scope_mismatch"),
    ],
)
def test_scope_version_request_and_step_contracts_fail_closed(fault, reason):
    value, _ = evidence()
    version = "1.1.0"
    if fault == "version":
        version = "1.0.0"
    elif fault == "none":
        value = None
    elif fault == "missing":
        value = replace(value, observations=value.observations[:1])
    elif fault == "request":
        value = replace(value, request_json='{"extra":true}')
    elif fault == "extra":
        value = replace(value, observations=(*value.observations, value.observations[0]))
    elif fault == "order":
        value = replace(value, observations=tuple(reversed(value.observations)))
    elif fault in {"workflow", "input_version"}:
        value = replace(
            value,
            **{
                "workflow_id" if fault == "workflow" else "workflow_version": "foreign",
            },
        )
    else:
        field = {
            "mapping": "capability_id",
            "observation_version": "workflow_version",
            "observation_workflow": "workflow_id",
        }[fault]
        value = replace(
            value,
            observations=(
                replace(value.observations[0], **{field: "foreign"}),
                value.observations[1],
            ),
        )
    result = evaluate(value, version=version)
    assert (result.result, result.reason, result.structure_result) == (
        "error",
        reason,
        "not_checked",
    )
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("failed", "not_checked", "not_checked")


def test_source_structure_failure_preserves_completed_checks():
    value, _ = evidence()
    result = evaluate(replace(value, structure_result="failed"))
    assert (result.result, result.reason, result.structure_result) == (
        "failed",
        "structure_invalid",
        "failed",
    )
    assert (
        result.checks.source_binding,
        result.checks.pending_preserved,
        result.checks.messages_preserved,
    ) == ("passed", "not_checked", "not_checked")
