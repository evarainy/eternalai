"""Operation/checkpoint CAS commits before external sends; crash means UNKNOWN."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import Lock
from typing import Any, AsyncIterator

import anyio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.infra.persistence.mcp.repository import PostgreSQLMcpStore
from app.infra.persistence.mcp.schema import operations, workflow_runs
from app.mcp.models import McpFailure, OperationState, digest
from app.ports.human_gate import HumanGateRequest
from app.ports.mcp import McpAuthorizationContext
from app.ports.workflow_store import GovernedWorkflowAuthorization, WorkflowOperation

_guard_mutex = Lock()
_guard_owners: set[str] = set()
_held_guards: ContextVar[frozenset[str]] = ContextVar("mcp_execution_guards", default=frozenset())


class PostgreSQLWorkflowStore:
    def __init__(self, store: PostgreSQLMcpStore) -> None:
        self._store = store

    @asynccontextmanager
    async def execution_guard(self, operation: WorkflowOperation) -> AsyncIterator[None]:
        key = digest(self._aad(operation))
        bind = self._store.sessions.kw.get("bind")
        capacity = 4
        if isinstance(bind, AsyncEngine):
            pool_size = getattr(bind.pool, "size", lambda: 5)()
            # Reserve at least one normal connection for token/CAS/gate queries.
            capacity = min(4, max(0, pool_size - 1))
        with _guard_mutex:
            if key in _guard_owners or len(_guard_owners) >= capacity:
                raise McpFailure("mcp_operation_busy")
            _guard_owners.add(key)
        lock_id = int(key[:16], 16)
        if lock_id >= 2**63:
            lock_id -= 2**64
        session = self._store.sessions()
        acquired = False
        marker = None
        try:
            with anyio.fail_after(5):
                acquired = bool(
                    (
                        await session.execute(
                            sa.text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
                        )
                    ).scalar_one()
                )
            if not acquired:
                raise McpFailure("mcp_operation_busy")
            marker = _held_guards.set(_held_guards.get() | {key})
            yield
        finally:
            if marker is not None:
                _held_guards.reset(marker)
            with anyio.CancelScope(shield=True):
                try:
                    if acquired:
                        await session.execute(
                            sa.text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                        )
                    # A transaction-bound test session may enclose independently committed
                    # savepoints; do not roll back those durable writes when releasing a lock.
                    await session.commit()
                except BaseException:
                    await session.invalidate()
                    raise
                finally:
                    try:
                        await session.close()
                    finally:
                        with _guard_mutex:
                            _guard_owners.discard(key)

    @staticmethod
    def _aad(operation: WorkflowOperation) -> dict[str, Any]:
        return {
            "operation_id": operation.operation_id,
            "tenant_id": operation.context.tenant_id,
            "user_id": operation.context.user_id,
            "service_config_id": operation.context.service_config_id,
        }

    def _decode(self, row: Any) -> WorkflowOperation:
        aad = {
            key: row[key] for key in ("operation_id", "tenant_id", "user_id", "service_config_id")
        }
        op = WorkflowOperation.model_validate(
            self._store.decrypt(bytes(row["encrypted_payload"]), aad)
        )
        if (op.state, op.revision, op.send_started, op.attempt_id) != (
            row["state"],
            row["revision"],
            row["send_started"],
            row["attempt_id"],
        ):
            raise McpFailure("mcp_checkpoint_inconsistent")
        if row["checkpoint"] != {
            "definition": op.outer_capability_id,
            "version": op.outer_version,
            "state": op.state,
            "revision": op.revision,
            "step_index": 0,
            "gate_request_id": op.gate_request_id,
        }:
            raise McpFailure("mcp_checkpoint_inconsistent")
        return op

    async def create(self, operation: WorkflowOperation) -> WorkflowOperation:
        op = operation
        async with self._store.sessions.begin() as session:
            task = (
                await session.execute(
                    sa.text(
                        "SELECT task_id FROM tasks WHERE task_id=:task AND tenant_id=:tenant "
                        "AND ai_user_id=:user AND session_id=:chat FOR SHARE"
                    ),
                    {
                        "task": op.context.task_id,
                        "tenant": op.context.tenant_id,
                        "user": op.context.user_id,
                        "chat": op.context.chat_session_id,
                    },
                )
            ).scalar_one_or_none()
            if task is None:
                raise McpFailure("mcp_checkpoint_inconsistent")
            await session.execute(
                sa.insert(workflow_runs).values(
                    **self._aad(op),
                    task_id=op.context.task_id,
                    connection_id=op.context.connection_id,
                    registration_id=op.context.registration_id,
                    checkpoint={
                        "definition": op.outer_capability_id,
                        "version": op.outer_version,
                        "state": op.state,
                        "revision": op.revision,
                        "step_index": 0,
                        "gate_request_id": op.gate_request_id,
                    },
                )
            )
            await session.execute(
                sa.insert(operations).values(
                    **self._aad(op),
                    state=op.state,
                    revision=op.revision,
                    attempt_id=op.attempt_id,
                    send_started=op.send_started,
                    expires_at=op.expires_at,
                    encrypted_payload=self._store.encrypt(
                        op.model_dump(mode="json"), self._aad(op)
                    ),
                )
            )
        return op

    async def load(
        self, operation_id: str, *, tenant_id: str, user_id: str
    ) -> WorkflowOperation | None:
        async with self._store.sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(operations, workflow_runs.c.checkpoint)
                        .join(workflow_runs)
                        .where(
                            operations.c.operation_id == operation_id,
                            operations.c.tenant_id == tenant_id,
                            operations.c.user_id == user_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._decode(row) if row else None

    async def by_task(self, task_id: str) -> WorkflowOperation | None:
        async with self._store.sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(operations, workflow_runs.c.checkpoint)
                        .join(workflow_runs)
                        .where(
                            workflow_runs.c.task_id == task_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._decode(row) if row else None

    async def list_owned(
        self, *, tenant_id: str, user_id: str, service_config_ids: tuple[str, ...]
    ) -> list[WorkflowOperation]:
        if not service_config_ids:
            return []
        async with self._store.sessions() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(operations, workflow_runs.c.checkpoint)
                        .join(workflow_runs)
                        .where(
                            operations.c.tenant_id == tenant_id,
                            operations.c.user_id == user_id,
                            operations.c.service_config_id.in_(service_config_ids),
                        )
                        .order_by(operations.c.expires_at.desc(), operations.c.operation_id)
                        .limit(50)
                    )
                )
                .mappings()
                .all()
            )
        return [self._decode(row) for row in rows]

    async def transition(
        self,
        operation: WorkflowOperation,
        *,
        state: OperationState,
        gate_request_id: str | None = None,
        attempt_id: str | None = None,
        safe_output: dict[str, Any] | None = None,
        review_url: str | None = None,
        renewed_context: McpAuthorizationContext | None = None,
        renewed_action_digest: str | None = None,
        renewed_gate_expires_at: datetime | None = None,
    ) -> WorkflowOperation:
        op = operation
        allowed = {
            "WAITING_LOCAL_CONFIRM": {"READY", "CANCELLED", "EXPIRED", "WAITING_LOCAL_CONFIRM"},
            "READY": {"SENDING", "CANCELLED", "EXPIRED", "WAITING_LOCAL_CONFIRM"},
            "SENDING": {"UNKNOWN", "VERIFIED_SUCCESS", "FAILED", "WAITING_EXTERNAL_CONFIRM"},
            "WAITING_EXTERNAL_CONFIRM": {
                "UNKNOWN",
                "VERIFIED_SUCCESS",
                "EXPIRED",
                "WAITING_LOCAL_CONFIRM",
                "FAILED",
            },
            "UNKNOWN": {"VERIFIED_SUCCESS", "FAILED", "WAITING_LOCAL_CONFIRM", "UNKNOWN"},
            "EXPIRED": {"WAITING_LOCAL_CONFIRM"},
        }
        if state not in allowed.get(op.state, set()):
            raise McpFailure("mcp_operation_transition_invalid")
        renewal = renewed_action_digest is not None
        if renewal:
            context = renewed_context or op.context
            immutable = (
                "tenant_id",
                "user_id",
                "service_config_id",
                "service_config_version",
                "connection_id",
                "registration_id",
                "task_id",
                "chat_session_id",
                "capability_id",
                "capability_version",
                "operation_id",
                "target_system",
            )
            if (
                state not in {"WAITING_LOCAL_CONFIRM", "UNKNOWN"}
                or len(op.previous_attempts) >= 10
                or any(getattr(context, key) != getattr(op.context, key) for key in immutable)
            ):
                raise McpFailure("mcp_operation_transition_invalid")
        elif renewed_context is not None or renewed_gate_expires_at is not None:
            raise McpFailure("mcp_operation_transition_invalid")
        updated = op.model_copy(
            update={
                "state": state,
                "revision": op.revision + 1,
                "gate_request_id": gate_request_id or op.gate_request_id,
                "attempt_id": attempt_id or op.attempt_id,
                "send_started": op.send_started or state == "SENDING",
                "safe_output": op.safe_output if safe_output is None else safe_output,
                "review_url": review_url if review_url is not None else op.review_url,
                "context": op.context.model_copy(
                    update={
                        "workflow_authorization_ref": attempt_id or op.attempt_id,
                    }
                ),
            }
        )
        if renewal:
            updated = updated.model_copy(
                update={
                    "context": (renewed_context or op.context).model_copy(
                        update={"workflow_authorization_ref": None}
                    ),
                    "action_digest": renewed_action_digest,
                    "expires_at": renewed_gate_expires_at or op.expires_at,
                    "gate_request_id": None,
                    "attempt_id": None if state == "WAITING_LOCAL_CONFIRM" else op.attempt_id,
                    "send_started": False if state == "WAITING_LOCAL_CONFIRM" else op.send_started,
                    "review_url": None,
                    "previous_attempts": op.previous_attempts
                    + (
                        (op.attempt_id,)
                        if op.attempt_id and state == "WAITING_LOCAL_CONFIRM"
                        else ()
                    ),
                }
            )
        async with self._store.sessions.begin() as session:
            changed = (
                await session.execute(
                    sa.update(operations)
                    .where(
                        operations.c.operation_id == op.operation_id,
                        operations.c.tenant_id == op.context.tenant_id,
                        operations.c.user_id == op.context.user_id,
                        operations.c.service_config_id == op.context.service_config_id,
                        operations.c.revision == op.revision,
                        operations.c.state == op.state,
                    )
                    .values(
                        state=updated.state,
                        revision=updated.revision,
                        attempt_id=updated.attempt_id,
                        send_started=updated.send_started,
                        expires_at=updated.expires_at,
                        encrypted_payload=self._store.encrypt(
                            updated.model_dump(mode="json"), self._aad(op)
                        ),
                    )
                    .returning(operations.c.operation_id)
                )
            ).scalar_one_or_none()
            if changed is None:
                raise McpFailure("mcp_operation_conflict")
            await session.execute(
                sa.update(workflow_runs)
                .where(
                    workflow_runs.c.operation_id == op.operation_id,
                )
                .values(
                    checkpoint={
                        "definition": op.outer_capability_id,
                        "version": op.outer_version,
                        "state": updated.state,
                        "revision": updated.revision,
                        "step_index": 0,
                        "gate_request_id": updated.gate_request_id,
                    }
                )
            )
        return updated

    async def confirmation(self, operation: WorkflowOperation) -> HumanGateRequest | None:
        context = operation.context
        async with self._store.sessions() as session:
            rows = (
                (
                    await session.execute(
                        sa.text(
                            "SELECT request_id, task_id, requested_for_ai_user_id, r"
                            "equested_session_id, "
                            "requested_tenant_id, action_digest, request_digest, bin"
                            "ding_manifest_digest, "
                            "requested_at, expires_at FROM human_gate_requests "
                            "WHERE task_id = :task "
                            "AND action_digest = :action AND requested_for_ai_user_id = :user "
                            "AND requested_session_id = :chat AND requested_tenant_id = :tenant "
                            "ORDER BY requested_at DESC LIMIT 2"
                        ),
                        {
                            "task": context.task_id,
                            "action": operation.action_digest,
                            "user": context.user_id,
                            "chat": context.chat_session_id,
                            "tenant": context.tenant_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
        if len(rows) > 1:
            raise McpFailure("mcp_confirmation_ambiguous")
        return HumanGateRequest.model_validate(dict(rows[0])) if rows else None

    async def consume(
        self,
        authorization: GovernedWorkflowAuthorization,
        context: McpAuthorizationContext,
        *,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> WorkflowOperation:
        op = await self.load(
            authorization.operation_id, tenant_id=context.tenant_id, user_id=context.user_id
        )
        if (
            op is None
            or digest(self._aad(op)) not in _held_guards.get()
            or op.state != "READY"
            or op.revision != authorization.expected_revision
            or op.attempt_id != authorization.attempt_id
            or op.leaf_capability_id != capability_id
            or op.context != context
            or digest(arguments) != op.canonical_args_digest
            or op.expires_at <= datetime.now(UTC)
            or (op.artifact_expires_at is not None and op.artifact_expires_at <= datetime.now(UTC))
        ):
            raise McpFailure("mcp_workflow_authorization_invalid")
        async with self._store.sessions() as session:
            confirmed = (
                await session.execute(
                    sa.text(
                        "SELECT request_id FROM human_gate_requests WHERE request_id = :gate "
                        "AND task_id = :task AND requested_for_ai_user_id = :user "
                        "AND requested_tenant_id = :tenant AND requested_session_id = :chat "
                        "AND action_digest = :action AND decision = 'confirmed' "
                        "AND expires_at > :now"
                    ),
                    {
                        "gate": op.gate_request_id,
                        "task": context.task_id,
                        "user": context.user_id,
                        "tenant": context.tenant_id,
                        "chat": context.chat_session_id,
                        "action": op.action_digest,
                        "now": datetime.now(UTC),
                    },
                )
            ).scalar_one_or_none()
        if confirmed is None:
            raise McpFailure("mcp_workflow_authorization_invalid")
        # A crash after this durable CAS is conservatively unknown, even before the HTTP send.
        return await self.transition(op, state="SENDING")
