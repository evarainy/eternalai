"""Versioned, code-owned production Workflow definitions."""

from app.workflow.models import WorkflowDefinition, WorkflowStep


def production_workflow_definitions() -> dict[str, WorkflowDefinition]:
    return {
        "oa.read_overview": WorkflowDefinition(
            workflow_id="oa.read_overview",
            version="1.1.0",
            steps=(
                WorkflowStep(step_id="pending", capability_id="oa.list_pending_workflows"),
                WorkflowStep(step_id="messages", capability_id="oa.list_system_messages"),
            ),
            output_step_ids=("pending", "messages"),
        ),
    }
