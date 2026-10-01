"""Durable governed external-write operation and checkpoint boundary."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.mcp.models import OperationState
from app.ports.human_gate import HumanGateRequest
from app.ports.mcp import McpAuthorizationContext


class GovernedWorkflowAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    attempt_id: str
    expected_revision: int


class WorkflowOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    context: McpAuthorizationContext
    outer_capability_id: str
    outer_version: str
    leaf_capability_id: str
    leaf_version: str
    remote_tool: str
    arguments: dict[str, Any] = Field(repr=False)
    canonical_args_digest: str
    action_digest: str
    gate_request_id: str | None = None
    state: OperationState = "WAITING_LOCAL_CONFIRM"
    revision: int = 1
    attempt_id: str | None = None
    send_started: bool = False
    expires_at: datetime
    artifact_expires_at: datetime | None = Field(default=None, repr=False)
    argument_preview: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    external_confirmation_required: bool = False
    review_url: str | None = None
    safe_output: dict[str, Any] = Field(default_factory=dict)
    public_result: dict[str, Any] | None = None
    identity_evidence_digest: str = "unconfirmed"
    previous_attempts: tuple[str, ...] = ()


class WorkflowStorePort(Protocol):
    def execution_guard(self, operation: WorkflowOperation) -> AbstractAsyncContextManager[None]:
        """Exclusive nonblocking send/recovery lock, released on process death."""
        ...

    async def create(self, operation: WorkflowOperation) -> WorkflowOperation: ...
    async def load(
        self, operation_id: str, *, tenant_id: str, user_id: str
    ) -> WorkflowOperation | None: ...
    async def by_task(self, task_id: str) -> WorkflowOperation | None: ...
    async def list_owned(
        self, *, tenant_id: str, user_id: str, service_config_ids: tuple[str, ...]
    ) -> list[WorkflowOperation]: ...
    async def confirmation(self, operation: WorkflowOperation) -> HumanGateRequest | None: ...
    async def transition(
        self,
        operation: WorkflowOperation,
        *,
        state: OperationState,
        gate_request_id: str | None = None,
        attempt_id: str | None = None,
        safe_output: dict[str, Any] | None = None,
        public_result: dict[str, Any] | None = None,
        review_url: str | None = None,
        renewed_context: McpAuthorizationContext | None = None,
        renewed_action_digest: str | None = None,
        renewed_gate_expires_at: datetime | None = None,
    ) -> WorkflowOperation: ...
    async def consume(
        self,
        authorization: GovernedWorkflowAuthorization,
        context: McpAuthorizationContext,
        *,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> WorkflowOperation: ...


class RecoveryResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["verified", "pending", "failed", "retry_original"]
    persistence: dict[str, Any] = Field(default_factory=dict)


class McpRecoveryPolicyPort(Protocol):
    approved: bool
    version: str

    def same_subject(self, previous_digest: str, current_evidence: str) -> bool: ...
    def record_id(self, operation: WorkflowOperation) -> str | None: ...
    async def artifact_deadline(
        self, context: McpAuthorizationContext, tool: str, arguments: dict[str, Any]
    ) -> datetime | None:
        """Verified original artifact/task eligibility deadline, never a local TTL."""
        ...

    async def reconcile(
        self, operation: WorkflowOperation, verified_read: dict[str, Any] | None
    ) -> RecoveryResolution:
        """Company-approved deterministic original-receipt decision; never performs HTTP."""
        ...
