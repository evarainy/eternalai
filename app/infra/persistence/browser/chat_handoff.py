"""Atomic admission-only intent accounting; never mutates the canonical browser task."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.task_store.transactional import (
    append_owned_event, lock_owned_task, transition_owned_task,
)
from app.ports.browser_gateway import (
    BrowserGatewayAdmission, BrowserGatewayInvocation, BrowserRequestScope,
)
from app.ports.error_codes import ErrorCode
from app.ports.task_store import TaskEventRecord


class PostgreSQLBrowserIntentHandoff:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _owner(scope: BrowserRequestScope) -> dict[str, str]:
        principal = scope.verified.principal
        return {"tenant_id": principal.org_ctx.tenant_id, "ai_user_id": principal.ai_user_id,
                "session_id": scope.bound_session}

    @staticmethod
    def _event_id(task_id: str) -> str:
        return uuid5(NAMESPACE_URL, "browser.intent_handoff.v1/" + task_id).hex

    @asynccontextmanager
    async def try_request_route(self, scope: BrowserRequestScope) -> AsyncIterator[bool]:
        principal = scope.verified.principal
        if not scope.matches(principal, scope.bound_session):
            raise ValueError("browser_request_context_mismatch")
        key = int.from_bytes(hashlib.sha256(json.dumps([
            "browser.ordinary.route.v1", principal.org_ctx.tenant_id, principal.ai_user_id,
            scope.bound_session, scope.client_request_id,
        ], separators=(",", ":")).encode("utf-8")).digest()[:8], "big", signed=True)
        # Existing PostgreSQL transaction-scoped locks coordinate all app
        # workers. They need no persistent table and release at context exit.
        async with self._sessions() as session, session.begin():
            acquired = (await session.execute(text(
                "SELECT pg_try_advisory_xact_lock(:key)",
            ), {"key": key})).scalar_one()
            if type(acquired) is not bool or not scope.matches(principal, scope.bound_session):
                raise ValueError("browser_request_route_lock_invalid")
            yield acquired

    async def commit(
        self, invocation: BrowserGatewayInvocation, admission: BrowserGatewayAdmission,
    ) -> None:
        if admission.owner != invocation.owner or admission.task_id == invocation.task_id:
            raise ValueError("browser_handoff_owner_invalid")
        owner = self._owner(invocation.scope)
        payload = {"phase": "admission_only", "capability_id": invocation.capability_id,
                   "capability_version": invocation.capability_version,
                   "canonical_task_id": admission.task_id, "canonical_run_id": admission.run_id,
                   "canonical_trace_id": admission.trace_id, "admission_state": admission.kind,
                   "request_key_digest": hashlib.sha256(
                       invocation.scope.client_request_id.encode("ascii")).hexdigest()}
        async with self._sessions() as session, session.begin():
            # Canonical ownership/read comes before locking the private intent task.
            canonical = (await session.execute(text(
                "SELECT trace_id,client_request_id FROM tasks WHERE task_id=:task_id"
                " AND tenant_id=:tenant_id AND ai_user_id=:ai_user_id AND session_id=:session_id"
            ), {**owner, "task_id": admission.task_id})).mappings().one_or_none()
            if (canonical is None or canonical["trace_id"] != admission.trace_id
                    or canonical["client_request_id"] != invocation.scope.client_request_id):
                raise ValueError("browser_handoff_canonical_invalid")
            if admission.run_id is not None:
                run = (await session.execute(text(
                    "SELECT run_id FROM browser_runs WHERE task_id=:task_id AND run_id=:run_id"
                    " AND tenant_id=:tenant_id AND ai_user_id=:ai_user_id AND session_id=:session_id"
                ), {**owner, "task_id": admission.task_id, "run_id": admission.run_id})).scalar_one_or_none()
                if run is None:
                    raise ValueError("browser_handoff_run_invalid")
            intent = await lock_owned_task(session, **owner, task_id=invocation.task_id)
            if intent is None or intent.trace_id != invocation.trace_id:
                raise ValueError("browser_handoff_intent_invalid")
            existing = (await session.execute(text(
                "SELECT payload FROM task_events WHERE task_id=:task_id AND event_id=:event_id"
            ), {"task_id": intent.task_id, "event_id": self._event_id(intent.task_id)})).scalar_one_or_none()
            if intent.status == "completed" and existing == payload:
                return
            if intent.status != "running" or existing is not None:
                raise ValueError("browser_handoff_intent_closed")
            await append_owned_event(session, **owner, task_id=intent.task_id, event=TaskEventRecord(
                event_id=self._event_id(intent.task_id), task_id=intent.task_id,
                event_type="browser.intent_handoff.v1", timestamp=datetime.now(UTC), payload=payload,
            ))
            changed = await transition_owned_task(
                session, **owner, task_id=intent.task_id, expected_statuses=("running",), status="completed",
            )
            if changed is None:
                raise ValueError("browser_handoff_cas_failed")

    async def fail_if_open(
        self, scope: BrowserRequestScope, error_code: ErrorCode,
    ) -> Literal["failed", "handed_off", "terminal", "absent"]:
        task_id = scope.attempt.task_id
        if task_id is None:
            return "absent"
        owner = self._owner(scope)
        async with self._sessions() as session, session.begin():
            intent = await lock_owned_task(session, **owner, task_id=task_id)
            if intent is None:
                return "absent"
            existing = (await session.execute(text(
                "SELECT event_id FROM task_events WHERE task_id=:task_id AND event_id=:event_id"
            ), {"task_id": task_id, "event_id": self._event_id(task_id)})).scalar_one_or_none()
            if intent.status == "completed" and existing is not None:
                return "handed_off"
            if intent.status != "running":
                return "terminal"
            changed = await transition_owned_task(
                session, **owner, task_id=task_id, expected_statuses=("running",),
                status="failed", error_code=error_code,
            )
            if changed is None:
                return "terminal"
            await append_owned_event(session, **owner, task_id=task_id, event=TaskEventRecord(
                event_id=uuid5(NAMESPACE_URL, "browser.intent_failure.v1/" + task_id).hex,
                task_id=task_id, event_type="browser.intent_failure.v1", timestamp=datetime.now(UTC),
                payload={"phase": "admission_only", "reason": "admission_uncertain",
                         "request_key_digest": hashlib.sha256(scope.client_request_id.encode("ascii")).hexdigest()},
            ))
            return "failed"
