from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.session import make_async_engine, make_async_session_factory
from app.infra.human_gate.postgresql import PostgreSQLHumanGate
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGateRequest,
    VersionBinding,
    VersionBindingMismatchError,
    build_task_version_binding_manifest,
)
from app.ports.task_store import TaskRecord
from app.version_binding import immutable_request_digest

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return DATABASE_URL


def test_postgresql_human_gate_is_immutable_actor_bound_and_value_free() -> None:
    database_url = _require_db()
    task_id = f"human-gate-task-{uuid4().hex}"
    request_id = f"human-gate-request-{uuid4().hex}"
    now = datetime.now(UTC)
    private_argument = "sensitive-canary-not-for-storage"

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        task_store = PostgreSQLTaskStore(factory)
        gate = PostgreSQLHumanGate(factory)
        try:
            await task_store.create_task(
                TaskRecord(
                    task_id=task_id,
                    session_id="human-gate-session",
                    ai_user_id="human-gate-user",
                    status="running",
                )
            )
            binding = VersionBinding(
                resource_type="tool",
                resource_id="oa.approve",
                version="1.0.0",
                digest="a" * 64,
            )
            manifest = build_task_version_binding_manifest(
                task_id=task_id,
                bindings=(binding,),
                locked_at=now,
            )
            peer_gate = PostgreSQLHumanGate(factory)
            bound_results = await asyncio.gather(
                gate.bind_task(manifest),
                peer_gate.bind_task(manifest),
            )
            assert bound_results == [manifest, manifest]
            assert await gate.get_task_binding(task_id) == manifest

            drifted = binding.model_copy(
                update={"version": "2.0.0", "digest": "b" * 64}
            )
            with pytest.raises(VersionBindingMismatchError):
                await gate.assert_task_bindings(task_id, (drifted,))

            request_digest = immutable_request_digest(
                task_id=task_id,
                action_digest="d" * 64,
                preview={"private_argument": private_argument},
                binding_manifest_digest=manifest.manifest_digest,
            )
            request = HumanGateRequest(
                request_id=request_id,
                task_id=task_id,
                requested_for_ai_user_id="human-gate-user",
                requested_session_id="human-gate-session",
                requested_tenant_id="tenant-1",
                action_digest="d" * 64,
                request_digest=request_digest,
                binding_manifest_digest=manifest.manifest_digest,
                requested_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            request_results = await asyncio.gather(
                gate.create_request(request),
                peer_gate.create_request(request),
            )
            assert request_results == [request, request]
            wrong_actor = HumanGateDecisionRecord(
                request_id=request_id,
                task_id=task_id,
                decided_by_ai_user_id="different-user",
                decided_session_id="human-gate-session",
                decided_tenant_id="tenant-1",
                decision="confirmed",
                request_digest=request_digest,
                binding_manifest_digest=manifest.manifest_digest,
                decided_at=now + timedelta(seconds=1),
            )
            with pytest.raises(HumanGateConflictError):
                await gate.record_decision(wrong_actor)

            wrong_session = wrong_actor.model_copy(
                update={
                    "decided_by_ai_user_id": "human-gate-user",
                    "decided_session_id": "different-session",
                }
            )
            with pytest.raises(HumanGateConflictError):
                await gate.record_decision(wrong_session)

            decision = wrong_actor.model_copy(
                update={"decided_by_ai_user_id": "human-gate-user"}
            )
            wrong_tenant = decision.model_copy(
                update={"decided_tenant_id": "tenant-2"}
            )
            with pytest.raises(HumanGateConflictError):
                await gate.record_decision(wrong_tenant)

            assert await gate.record_decision(decision) == decision
            assert await gate.get_decision(request_id) == decision

            expired_request = request.model_copy(
                update={
                    "request_id": f"{request_id}-expired",
                    "requested_at": now - timedelta(minutes=10),
                    "expires_at": now - timedelta(minutes=5),
                }
            )
            await gate.create_request(expired_request)
            expired_decision = decision.model_copy(
                update={
                    "request_id": expired_request.request_id,
                    "decided_at": now,
                }
            )
            with pytest.raises(HumanGateConflictError):
                await gate.record_decision(expired_decision)

            async with factory() as session:
                stored = (
                    await session.execute(
                        text(
                            "SELECT CAST(bindings AS TEXT) AS bindings, request_digest, "
                            "action_digest, expires_at "
                            "FROM task_version_binding_manifests JOIN human_gate_requests "
                            "USING (task_id) WHERE request_id = :request_id"
                        ),
                        {"request_id": request_id},
                    )
                ).one()
            assert private_argument not in stored.bindings
            assert private_argument not in stored.request_digest
            assert stored.action_digest == "d" * 64
            assert stored.expires_at == request.expires_at
        finally:
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM human_gate_requests WHERE task_id = :task_id"),
                    {"task_id": task_id},
                )
                await session.execute(
                    text(
                        "DELETE FROM task_version_binding_manifests "
                        "WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                )
                await session.execute(
                    text("DELETE FROM tasks WHERE task_id = :task_id"),
                    {"task_id": task_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())
