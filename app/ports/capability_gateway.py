"""Capability gateway interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from app.ports.error_codes import ErrorCode as ErrorCode
from app.ports.evaluation import OverviewEvaluationInput
from app.ports.mcp import McpValidatedOutcome, McpValidatedRead
from app.ports.request_context import RequestChannel as RequestChannel
from app.ports.request_context import RequestOrgContext as RequestOrgContext
from app.ports.workflow_store import GovernedWorkflowAuthorization

ExecutionStatus: TypeAlias = Literal[
    "completed",
    "failed",
    "denied",
    "binding_required",
    "timeout",
    "no_capability_found",
    "waiting_user",
]


class ExecutionResult(BaseModel):
    # Internal workflow provenance, never accepted from or serialized into JSON.
    _governed_terminal: object | None = PrivateAttr(default=None)
    status: ExecutionStatus
    data: dict[str, Any] | None = None
    error_code: ErrorCode | None = None
    trace_id: str
    mcp_outcome: McpValidatedOutcome | None = Field(default=None, exclude=True, repr=False)
    mcp_read: McpValidatedRead | None = Field(default=None, exclude=True, repr=False)
    postcondition_input: OverviewEvaluationInput | None = Field(
        default=None, exclude=True, repr=False,
    )

    @field_validator("postcondition_input", mode="before")
    @classmethod
    def _typed_evidence(cls, value: object) -> object:
        if value is not None and type(value) is not OverviewEvaluationInput:
            raise ValueError("Postcondition input must be an immutable evaluation value")
        return value


class CapabilityGatewayPort(Protocol):
    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
        *,
        workflow_authorization: GovernedWorkflowAuthorization | None = None,
    ) -> ExecutionResult: ...
