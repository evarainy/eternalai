"""Deterministic in-memory HumanGatePort implementation for local runtimes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.infra.human_gate.bindings import assert_bindings
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGatePort,
    HumanGateRequest,
    TaskVersionBindingManifest,
    VersionBinding,
    VersionBindingMismatchError,
)


class InMemoryHumanGate:
    def __init__(self) -> None:
        self._manifests: dict[str, TaskVersionBindingManifest] = {}
        self._requests: dict[str, HumanGateRequest] = {}
        self._decisions: dict[str, HumanGateDecisionRecord] = {}
        self._lock = asyncio.Lock()

    async def bind_task(
        self,
        manifest: TaskVersionBindingManifest,
    ) -> TaskVersionBindingManifest:
        async with self._lock:
            existing = self._manifests.get(manifest.task_id)
            if existing is None:
                self._manifests[manifest.task_id] = manifest
                return manifest
            if existing == manifest:
                return existing
            raise HumanGateConflictError("Task version binding is immutable")

    async def assert_task_bindings(
        self,
        task_id: str,
        bindings: tuple[VersionBinding, ...],
        *,
        exact: bool = False,
        allow_unbound: bool = False,
    ) -> None:
        manifest = self._manifests.get(task_id)
        if manifest is None:
            if allow_unbound:
                return
            raise VersionBindingMismatchError("Task version binding is unavailable")
        assert_bindings(manifest, bindings, exact=exact)

    async def get_task_binding(
        self,
        task_id: str,
    ) -> TaskVersionBindingManifest | None:
        return self._manifests.get(task_id)

    async def create_request(self, request: HumanGateRequest) -> HumanGateRequest:
        async with self._lock:
            manifest = self._manifests.get(request.task_id)
            if (
                manifest is None
                or manifest.manifest_digest != request.binding_manifest_digest
            ):
                raise HumanGateConflictError("Human gate request binding is invalid")
            existing = self._requests.get(request.request_id)
            if existing is None:
                self._requests[request.request_id] = request
                return request
            if existing == request:
                return existing
            raise HumanGateConflictError("Human gate request is immutable")

    async def get_request(self, request_id: str) -> HumanGateRequest | None:
        return self._requests.get(request_id)

    async def record_decision(
        self,
        decision: HumanGateDecisionRecord,
    ) -> HumanGateDecisionRecord:
        async with self._lock:
            request = self._requests.get(decision.request_id)
            if request is None or not _decision_matches_request(decision, request):
                raise HumanGateConflictError("Human gate decision does not match request")
            existing = self._decisions.get(decision.request_id)
            if existing is None:
                self._decisions[decision.request_id] = decision
                return decision
            if existing == decision:
                return existing
            raise HumanGateConflictError("Human gate decision is immutable")

    async def get_decision(
        self,
        request_id: str,
    ) -> HumanGateDecisionRecord | None:
        return self._decisions.get(request_id)


def _decision_matches_request(
    decision: HumanGateDecisionRecord,
    request: HumanGateRequest,
) -> bool:
    return bool(
        decision.task_id == request.task_id
        and decision.decided_by_ai_user_id == request.requested_for_ai_user_id
        and decision.decided_session_id == request.requested_session_id
        and decision.decided_tenant_id == request.requested_tenant_id
        and decision.request_digest == request.request_digest
        and decision.binding_manifest_digest == request.binding_manifest_digest
        and request.requested_at <= decision.decided_at <= request.expires_at
    )


if TYPE_CHECKING:

    def _protocol_check(store: InMemoryHumanGate) -> HumanGatePort:
        return store


__all__ = ("InMemoryHumanGate",)
