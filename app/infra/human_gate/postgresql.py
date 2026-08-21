"""PostgreSQL persistence for immutable Task bindings and human decisions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.human_gate.in_memory import _assert_bindings
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGatePort,
    HumanGateRequest,
    TaskVersionBindingManifest,
    VersionBinding,
)


class PostgreSQLHumanGate:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def bind_task(
        self,
        manifest: TaskVersionBindingManifest,
    ) -> TaskVersionBindingManifest:
        binding_payload = {
            "bindings": [
                binding.model_dump(mode="json") for binding in manifest.bindings
            ],
            "unused_resource_types": list(manifest.unused_resource_types),
        }
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "INSERT INTO task_version_binding_manifests "
                            "(task_id, manifest_digest, bindings, locked_at) VALUES "
                            "(:task_id, :manifest_digest, CAST(:bindings AS JSONB), "
                            ":locked_at) ON CONFLICT (task_id) DO NOTHING "
                            "RETURNING task_id"
                        ),
                        {
                            "task_id": manifest.task_id,
                            "manifest_digest": manifest.manifest_digest,
                            "bindings": json.dumps(
                                binding_payload,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            "locked_at": manifest.locked_at,
                        },
                    )
                ).fetchone()
                await session.commit()
        except IntegrityError as exc:
            raise HumanGateConflictError(
                "Task version binding could not be created"
            ) from exc
        if row is not None:
            return manifest
        existing = await self._get_manifest(manifest.task_id)
        if existing == manifest:
            return existing
        raise HumanGateConflictError("Task version binding is immutable")

    async def assert_task_bindings(
        self,
        task_id: str,
        bindings: tuple[VersionBinding, ...],
        *,
        exact: bool = False,
    ) -> None:
        manifest = await self._get_manifest(task_id)
        if manifest is None:
            from app.ports.human_gate import VersionBindingMismatchError

            raise VersionBindingMismatchError("Task version binding is unavailable")
        _assert_bindings(manifest, bindings, exact=exact)

    async def get_task_binding(
        self,
        task_id: str,
    ) -> TaskVersionBindingManifest | None:
        return await self._get_manifest(task_id)

    async def create_request(self, request: HumanGateRequest) -> HumanGateRequest:
        manifest = await self._get_manifest(request.task_id)
        if (
            manifest is None
            or manifest.manifest_digest != request.binding_manifest_digest
        ):
            raise HumanGateConflictError("Human gate request binding is invalid")
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "INSERT INTO human_gate_requests "
                            "(request_id, task_id, requested_for_ai_user_id, "
                            "requested_session_id, requested_tenant_id, action_digest, "
                            "request_digest, binding_manifest_digest, requested_at, "
                            "expires_at) SELECT :request_id, :task_id, "
                            ":requested_for_ai_user_id, :requested_session_id, "
                            ":requested_tenant_id, :action_digest, :request_digest, "
                            ":binding_manifest_digest, :requested_at, :expires_at "
                            "FROM tasks WHERE task_id = :task_id "
                            "AND session_id = :requested_session_id "
                            "AND ai_user_id = :requested_for_ai_user_id "
                            "ON CONFLICT (request_id) DO NOTHING RETURNING request_id"
                        ),
                        request.model_dump(),
                    )
                ).fetchone()
                await session.commit()
        except IntegrityError as exc:
            raise HumanGateConflictError(
                "Human gate request could not be created"
            ) from exc
        if row is not None:
            return request
        existing = await self.get_request(request.request_id)
        if existing == request:
            return existing
        raise HumanGateConflictError("Human gate request is immutable or subject is invalid")

    async def record_decision(
        self,
        decision: HumanGateDecisionRecord,
    ) -> HumanGateDecisionRecord:
        parameters = decision.model_dump()
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE human_gate_requests SET "
                        "decision = :decision, "
                        "decided_by_ai_user_id = :decided_by_ai_user_id, "
                        "decided_session_id = :decided_session_id, "
                        "decided_tenant_id = :decided_tenant_id, "
                        "decided_at = :decided_at "
                        "WHERE request_id = :request_id AND task_id = :task_id "
                        "AND requested_for_ai_user_id = :decided_by_ai_user_id "
                        "AND requested_session_id = :decided_session_id "
                        "AND requested_tenant_id = :decided_tenant_id "
                        "AND request_digest = :request_digest "
                        "AND binding_manifest_digest = :binding_manifest_digest "
                        "AND requested_at <= :decided_at AND expires_at >= :decided_at "
                        "AND decision IS NULL "
                        "RETURNING request_id"
                    ),
                    parameters,
                )
            ).fetchone()
            if row is not None:
                await session.commit()
                return decision
            await session.rollback()
        existing = await self.get_decision(decision.request_id)
        if existing == decision:
            return existing
        raise HumanGateConflictError("Human gate decision does not match request")

    async def get_decision(
        self,
        request_id: str,
    ) -> HumanGateDecisionRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT request_id, task_id, decided_by_ai_user_id, "
                        "decided_session_id, decided_tenant_id, decision, "
                        "request_digest, binding_manifest_digest, decided_at "
                        "FROM human_gate_requests WHERE request_id = :request_id "
                        "AND decision IS NOT NULL"
                    ),
                    {"request_id": request_id},
                )
            ).fetchone()
        if row is None:
            return None
        return HumanGateDecisionRecord(
            request_id=row.request_id,
            task_id=row.task_id,
            decided_by_ai_user_id=row.decided_by_ai_user_id,
            decided_session_id=row.decided_session_id,
            decided_tenant_id=row.decided_tenant_id,
            decision=row.decision,
            request_digest=row.request_digest,
            binding_manifest_digest=row.binding_manifest_digest,
            decided_at=row.decided_at,
        )

    async def _get_manifest(
        self,
        task_id: str,
    ) -> TaskVersionBindingManifest | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT task_id, manifest_digest, bindings, locked_at "
                        "FROM task_version_binding_manifests WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                )
            ).fetchone()
        if row is None:
            return None
        raw_manifest: Any = row.bindings
        if isinstance(raw_manifest, str):
            raw_manifest = json.loads(raw_manifest)
        if not isinstance(raw_manifest, dict):
            raise HumanGateConflictError("Task version binding payload is invalid")
        return TaskVersionBindingManifest(
            task_id=row.task_id,
            manifest_digest=row.manifest_digest,
            bindings=tuple(
                VersionBinding.model_validate(item)
                for item in raw_manifest.get("bindings", [])
            ),
            unused_resource_types=tuple(
                raw_manifest.get("unused_resource_types", [])
            ),
            locked_at=row.locked_at,
        )

    async def get_request(self, request_id: str) -> HumanGateRequest | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT request_id, task_id, requested_for_ai_user_id, "
                        "requested_session_id, requested_tenant_id, action_digest, "
                        "request_digest, binding_manifest_digest, requested_at, expires_at "
                        "FROM human_gate_requests WHERE request_id = :request_id"
                    ),
                    {"request_id": request_id},
                )
            ).fetchone()
        if row is None:
            return None
        return HumanGateRequest(
            request_id=row.request_id,
            task_id=row.task_id,
            requested_for_ai_user_id=row.requested_for_ai_user_id,
            requested_session_id=row.requested_session_id,
            requested_tenant_id=row.requested_tenant_id,
            action_digest=row.action_digest,
            request_digest=row.request_digest,
            binding_manifest_digest=row.binding_manifest_digest,
            requested_at=row.requested_at,
            expires_at=row.expires_at,
        )


if TYPE_CHECKING:

    def _protocol_check(store: PostgreSQLHumanGate) -> HumanGatePort:
        return store


__all__ = ("PostgreSQLHumanGate",)
