"""Real input construction preserves source facts and strict schema failures."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.infra.orchestration.agent_adapter import _workflow_execution_result
from app.infra.orchestration.overview_evaluation import build_overview_evaluation_input
from app.infra.workflow.catalog import production_workflow_capabilities
from app.workflow.models import WorkflowRunResult
from tests.evaluator.test_overview_postconditions import SCOPE, evidence


def run_result():
    value, data = evidence()
    return WorkflowRunResult(
        "oa.read_overview",
        "1.1.0",
        "trace",
        "completed",
        data,
        {},
        evaluation_observations=value.observations,
    )


def test_strict_input_constructor_rejects_invalid_source_or_output():
    result = run_result()
    value = build_overview_evaluation_input(result, SCOPE, {})
    assert value.structure_result == "passed"
    assert value.scope == SCOPE and value.request_json == "{}"
    assert value.observations == result.evaluation_observations
    result.output["pending"]["workflows"][0]["title"] = "changed-after-snapshot"
    changed = build_overview_evaluation_input(result, SCOPE, {})
    assert changed.observations == value.observations
    assert changed.output_json != value.output_json
    malformed = replace(
        result,
        evaluation_observations=(
            replace(result.evaluation_observations[0], payload_json='{"workflows":[]}'),
            result.evaluation_observations[1],
        ),
    )
    invalid_source = build_overview_evaluation_input(malformed, SCOPE, {})
    assert invalid_source.structure_result == "failed"
    result.output["pending"]["returned_count"] = "1"
    with pytest.raises(ValidationError):
        build_overview_evaluation_input(result, SCOPE, {})


def test_adapter_preserves_upstream_structure_error_and_supplies_evidence():
    result = run_result()
    capability = production_workflow_capabilities()[0]
    success = _workflow_execution_result(result, scope=SCOPE, capability=capability, arguments={})
    assert success.status == "completed" and success.postcondition_input is not None
    assert success.postcondition_input.observations == result.evaluation_observations
    assert success.postcondition_input.scope == SCOPE
    result.output["pending"]["returned_count"] = 99
    failure = _workflow_execution_result(result, scope=SCOPE, capability=capability, arguments={})
    assert (failure.status, failure.error_code, failure.data, failure.postcondition_input) == (
        "failed",
        "adapter_payload_invalid",
        None,
        None,
    )
