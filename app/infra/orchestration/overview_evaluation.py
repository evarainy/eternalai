"""Construct overview evidence using strict source models, without normalizing facts."""

from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.evaluator.overview import canonical_object, load_object
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.infra.workflow.catalog import OAReadOverviewOutput
from app.ports.evaluation import EvaluationScope, OverviewEvaluationInput
from app.workflow.models import WorkflowRunResult


def build_overview_evaluation_input(
    result: WorkflowRunResult,
    scope: EvaluationScope,
    arguments: dict[str, Any],
) -> OverviewEvaluationInput:
    # This validation is also used by the adapter's existing failure mapping.
    OAReadOverviewOutput.model_validate(result.output, strict=True)
    structure: Literal["passed", "failed"] = "passed"
    for observation in result.evaluation_observations:
        models: dict[str, type[BaseModel]] = {
            "pending": OAPendingWorkflowCollection,
            "messages": OASystemMessageCollection,
        }
        model = models.get(observation.step_id)
        if model is not None:
            try:
                model.model_validate(load_object(observation.payload_json), strict=True)
            except (ValidationError, ValueError, TypeError):
                structure = "failed"
    return OverviewEvaluationInput(
        scope=scope,
        workflow_id=result.workflow_id,
        workflow_version=result.workflow_version,
        rule_id="oa_read_overview_v1",
        request_json=canonical_object(arguments),
        observations=result.evaluation_observations,
        output_json=canonical_object(result.output),
        structure_result=structure,
    )
