"""Structured and Chat admission share one authenticated durable service."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.chat import BrowserChatService
from app.browser_skill.models import BrowserOwner
from app.browser_skill.run_contracts import BrowserRunRef, _mint_commit_fact
from app.browser_skill.submission import BrowserSubmissionService
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.browser_chat import BrowserChatError
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    CanonicalRequest,
    ProtectedRunEnvelope,
    RunAdmission,
    RunSnapshot,
)
from app.ports.credential_vault import BrowserAuthorizationError
from app.ports.human_gate import (
    HumanGateDecisionRecord,
    HumanGateRequest,
    VersionBinding,
    build_task_version_binding_manifest,
)


class _Store:
    def __init__(self) -> None:
        self.requests: dict[str, tuple[bytes, CanonicalRequest]] = {}
        self.accepted: list[object] = []
        self.reads = 0
        self.deny_read = False
        self.rejected: list[str] = []

    async def get_or_create_request(
        self, owner: BrowserOwner, client_request_id: str, semantic_input: bytes,
        *, processing_owner: str,
    ) -> CanonicalRequest:
        old = self.requests.get(client_request_id)
        if old is not None:
            if old[0] != semantic_input or old[1].owner != owner:
                raise BrowserRunStoreError("request_key_conflict")
            return replace(old[1], parse_winner=False)
        request = CanonicalRequest(
            owner=owner, task_id="task_1", trace_id="trace_1",
            client_request_id=client_request_id, request_digest_key_id="fixture",
            request_digest=b"d" * 32, processing_owner=processing_owner,
            processing_deadline=None, parse_winner=True, task_status="parsing",
            error_code=None, run=None,
        )
        self.requests[client_request_id] = (semantic_input, request)
        return request

    async def reject_request(self, request: CanonicalRequest, error_code: str) -> None:
        self.rejected.append(error_code)
        semantic, _ = self.requests[request.client_request_id]
        self.requests[request.client_request_id] = (
            semantic, replace(request, task_status="failed", error_code=error_code),
        )

    async def accept(self, request: CanonicalRequest, admission: object) -> object:
        self.accepted.append(admission)
        reference = BrowserRunRef(
            owner=request.owner, task_id=request.task_id,
            run_id=admission.run_id, state_revision=1,
        )
        run = replace(
            _write_run(request.owner), admission=admission, state_revision=1, phase="queued",
            worker_id=None, worker_epoch=None, lease_epoch=None, effect="not_sent",
        )
        semantic, _ = self.requests[request.client_request_id]
        self.requests[request.client_request_id] = (
            semantic, replace(request, task_status="running", run=run),
        )
        return _mint_commit_fact(reference)

    async def get(self, owner: BrowserOwner, task_id: str, run_id: str) -> object:
        self.reads += 1
        if self.deny_read:
            raise BrowserRunStoreError("browser_run_not_found")
        for _, request in self.requests.values():
            if request.owner == owner and request.run is not None:
                if request.task_id == task_id and request.run.run_id == run_id:
                    return request.run
        raise BrowserRunStoreError("browser_run_not_found")


class _Cipher:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def encrypt_input(self, _identity: object, value: dict[str, object]) -> ProtectedRunEnvelope:
        self.payload = value
        return ProtectedRunEnvelope("aes256gcm-browser-input-v1", "fixture", b"n" * 12, b"c" * 16)


class _Parser:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(
        self, _principal: Principal, _message: str, _capability: object,
    ) -> dict[str, Any]:
        self.calls += 1
        return {"business_key": "CASE_1"}


def _service(
    *, target_system: str = "oa",
    source_kind: str = "registered_synthetic_fixture",
) -> tuple[BrowserSubmissionService, _Store, _Cipher, Principal]:
    principal = Principal(
        ai_user_id="user_1", display_name="User", roles=("reader",),
        org_ctx=PrincipalOrgContext(tenant_id="tenant_1"),
    )
    store = _Store()
    cipher = _Cipher()
    capability = SimpleNamespace(
        capability_id="query_1", type="query", status="active",
        target_system=target_system, binding_required=True, execution_identity="user_delegated",
        input_schema={
            "type": "object", "properties": {"business_key": {"type": "string"}},
            "required": ["business_key"], "additionalProperties": False,
        },
    )
    manifest = SimpleNamespace(
        skill=SimpleNamespace(skill_id="skill_1", version="v1"), effect="read_only",
        capability=capability, digest="a" * 64,
        source_kind=source_kind,
    )

    class Publications:
        async def get_active(self, _owner: BrowserOwner, _skill: str) -> object:
            return SimpleNamespace(manifest=manifest, state="active")

        async def get_frozen(self, _owner: BrowserOwner, digest: str) -> object:
            return (SimpleNamespace(manifest=manifest, state="active")
                    if digest == manifest.digest else None)

        async def assert_current(self, _owner: BrowserOwner, _manifest: object) -> None:
            return None

    class Bindings:
        async def resolve(
            self, _principal: Principal, _session: str, _capability: object,
        ) -> object:
            return SimpleNamespace(
                tenant_id="tenant_1", ai_user_id="user_1", target_system=target_system,
                binding_id="binding_1", binding_revision=1,
            )

    class Sessions:
        async def create_session(self, record: object) -> object:
            return record

    class Trace:
        async def record_step(self, *args: object, **kwargs: object) -> None:
            return None

    service = BrowserSubmissionService(
        store, Publications(), Bindings(), cipher, lambda _principal, session: session,
        Trace(), sessions=Sessions(), skill_id="skill_1", input_digest_key=b"k" * 32,
        enabled=True,
    )
    return service, store, cipher, principal


def _authentication(principal: Principal, *, expired: bool = False) -> object:
    expiry = datetime.now(UTC) + timedelta(minutes=-1 if expired else 5)
    return authenticated_session.set(AuthenticatedSessionContext(principal, b"f" * 32, expiry))


def _write_run(owner: BrowserOwner, *, completed: bool = False) -> RunSnapshot:
    admission = RunAdmission(
        owner=owner, task_id="task_1", run_id="run_1", target_system="oa",
        binding_id="binding_1", binding_revision=1, auth_fingerprint=b"f" * 32,
        auth_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        publication_digest=b"a" * 32, input_revision=1, input_digest=b"i" * 32,
        protected_input=ProtectedRunEnvelope(
            "aes256gcm-browser-input-v1", "fixture", b"n" * 12, b"c" * 16,
        ),
    )
    return RunSnapshot(
        admission=admission, state_revision=9,
        status="completed" if completed else "running", phase=None if completed else "running",
        worker_id="fixture_worker", worker_epoch=1, worker_deadline=None,
        provider_key=None, provider_manifest_digest=None, lease_epoch=1,
        profile_generation_id=None, capture_operation_id=None, capture_status="not_requested",
        cancel_requested=False, cancel_acknowledged=False, effect="acknowledged",
        verification="verified" if completed else None,
        verification_evidence_digest=b"v" * 32 if completed else None,
        cleanup="released" if completed else "pending", error_code=None,
        dispatch_failure_code=None, terminal_revision=9 if completed else None,
        terminal_event_id="event_1" if completed else None,
        result_digest=None, protected_result=None,
    )


def _recorded_write(service: BrowserSubmissionService, principal: Principal, *, completed: bool):
    owner = service._owner(principal, "session_1")
    run = _write_run(owner, completed=completed)
    now = datetime.now(UTC)
    request = HumanGateRequest(
        request_id="confirmation_1", task_id=run.task_id,
        requested_for_ai_user_id=owner.user_id, requested_session_id=owner.session_id,
        requested_tenant_id=owner.tenant_id, action_digest="a" * 64,
        request_digest="b" * 64, binding_manifest_digest="c" * 64,
        requested_at=now - timedelta(minutes=20), expires_at=now - timedelta(minutes=5),
    )
    record = HumanGateDecisionRecord(
        request_id=request.request_id, task_id=run.task_id,
        decided_by_ai_user_id=owner.user_id, decided_session_id=owner.session_id,
        decided_tenant_id=owner.tenant_id, decision="confirmed",
        request_digest=request.request_digest,
        binding_manifest_digest=request.binding_manifest_digest,
        decided_at=request.requested_at + timedelta(minutes=1),
    )
    preparation = Mock(
        task_id=run.task_id, run_id=run.run_id,
        publication_digest=run.admission.publication_digest.hex(), request_id=request.request_id,
    )
    preparation.gate_request.return_value = request
    store = Mock(get=AsyncMock(return_value=run),
                 load_write_preparation=AsyncMock(return_value=preparation))
    gate = Mock(get_request=AsyncMock(return_value=request),
                get_decision=AsyncMock(return_value=record), record_decision=AsyncMock())
    service._store, service._write_gate = store, gate
    return run, request, record, store, gate


@pytest.mark.parametrize("completed", [False, True])
def test_recorded_confirmation_retry_is_read_only_after_dispatch_and_expiry(completed) -> None:
    async def scenario() -> None:
        service, _, _, principal = _service()
        token = _authentication(principal)
        try:
            run, _, _, store, gate = _recorded_write(service, principal, completed=completed)
            response = await service.decide_write(
                principal, "session_1", run.task_id, run.run_id, "confirmed",
            )
            assert response.run.run_id == run.run_id
            assert response.run.status == run.status
            gate.record_decision.assert_not_awaited()
            assert store.get.await_count == 2
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("bad", ["opposite", "forged_request", "forged_subject", "late_decision"])
def test_recorded_confirmation_does_not_hide_conflicting_or_forged_decisions(bad) -> None:
    async def scenario() -> None:
        service, _, _, principal = _service()
        token = _authentication(principal)
        try:
            run, request, record, _, gate = _recorded_write(service, principal, completed=True)
            decision = "rejected" if bad == "opposite" else "confirmed"
            changes = {
                "forged_request": {"request_digest": "d" * 64},
                "forged_subject": {"decided_by_ai_user_id": "another_user"},
                "late_decision": {"decided_at": request.expires_at},
            }
            if bad in changes:
                gate.get_decision.return_value = record.model_copy(update=changes[bad])
            with pytest.raises(BrowserChatError) as caught:
                await service.decide_write(
                    principal, "session_1", run.task_id, run.run_id, decision,
                )
            assert caught.value.code == "browser_write_confirmation_conflict"
            gate.record_decision.assert_not_awaited()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


def test_confirmation_retry_rechecks_current_run_access_before_exposing_state() -> None:
    async def scenario() -> None:
        service, _, _, principal = _service()
        token = _authentication(principal)
        try:
            run, _, _, store, gate = _recorded_write(service, principal, completed=True)
            store.get.side_effect = BrowserRunStoreError("browser_run_not_found")
            with pytest.raises(BrowserChatError) as caught:
                await service.decide_write(
                    principal, "session_1", run.task_id, run.run_id, "confirmed",
                )
            assert caught.value.code == "browser_run_not_found"
            gate.get_decision.assert_not_awaited()
            gate.record_decision.assert_not_awaited()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


def test_prepared_write_validation_uses_sealed_admission_and_existing_write_gate() -> None:
    async def scenario() -> None:
        service, store, _, principal = _service(source_kind="registered_site")
        token = _authentication(principal)
        try:
            publication = await service._publications.get_active(
                service._owner(principal, "session_1"), "skill_1",
            )
            manifest = publication.manifest
            manifest.effect, manifest.capability.type = "controlled_write", "action"
            service._write_gate = Mock()
            service._confirmed_key_digest = lambda _digest, _key: "b" * 64
            service._teaching_candidate_digest = manifest.digest
            service._teaching_publication = AsyncMock(return_value=publication)
            plan = Mock()
            service._teaching_plan = AsyncMock(return_value=plan)
            service._ensure_write_preparation = AsyncMock()

            async def accept_teaching(request, admission, actual_plan):
                assert actual_plan is plan
                return await store.accept(request, admission)

            store.accept_teaching = AsyncMock(side_effect=accept_teaching)
            response = await service.submit_initial_validation(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                skill_version="v1", arguments={"business_key": "CASE_1"},
                client_request_id="initial_write_1",
            )
            assert response.data["kind"] == "accepted"
            assert len(store.accepted) == 1
            store.accept_teaching.assert_awaited_once()
            service._ensure_write_preparation.assert_awaited_once()
            assert store.rejected == []
            # Teaching new combinations remains read-only: it cannot borrow the
            # first-version write validation exception.
            service._teaching_base_digest = "c" * 64
            second = await service.submit_teaching(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                skill_version="v1", arguments={"business_key": "CASE_1"},
                client_request_id="write_teaching_1",
            )
            assert second.status == "failed"
            assert second.data["error_code"] == "browser_capability_unavailable"
            assert len(store.accepted) == 1
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("gap", ["request", "preparation"])
def test_committed_write_retry_recovers_same_gate_and_preparation_without_readmission(initial, gap):
    async def scenario() -> None:
        from tests.infra.browser.test_initial_write_publication import write_manifest

        service, store, _, principal = _service(source_kind="registered_site")
        token = _authentication(principal)
        try:
            manifest = write_manifest()
            record = SimpleNamespace(manifest=manifest, state="prepared" if initial else "active")
            service._skill_id = manifest.skill.skill_id
            service._publications = Mock(
                get_active=AsyncMock(return_value=record),
                get_frozen=AsyncMock(return_value=None if initial else record),
                assert_current=AsyncMock(),
            )
            service._confirmed_key_digest = lambda _digest, _key: "b" * 64
            if initial:
                service._teaching_candidate_digest = manifest.digest
                service._teaching_candidate_version = manifest.skill.version
                service._teaching_publication = AsyncMock(return_value=record)
                service._teaching_plan = AsyncMock(return_value=object())

                async def accept_teaching(request, admission, _plan):
                    return await store.accept(request, admission)

                store.accept_teaching = AsyncMock(side_effect=accept_teaching)

            def plan_for(_owner, task, run):
                return SimpleNamespace(task_id=task, run_id=run, purpose="initial_validation",
                                       publication_digest=manifest.digest)

            store.load_teaching_plan = AsyncMock(side_effect=plan_for)
            binding = build_task_version_binding_manifest(
                task_id="task_1", bindings=(VersionBinding(
                    resource_type="skill", resource_id=manifest.skill.skill_id,
                    version=manifest.skill.version, digest=manifest.skill.digest,
                ),), locked_at=datetime.now(UTC),
            )
            gate_requests, preparations = [], []
            failed = False

            async def create_request(request):
                nonlocal failed
                if gap == "request" and not failed:
                    failed = True
                    raise RuntimeError("synthetic post-commit gate failure")
                gate_requests.append(request)
                return request

            async def record_preparation(run, preparation):
                nonlocal failed
                if gap == "preparation" and not failed:
                    failed = True
                    raise RuntimeError("synthetic post-gate preparation failure")
                preparations.append(preparation)
                return run

            gate = Mock(
                get_task_binding=AsyncMock(return_value=binding), bind_task=AsyncMock(),
                get_request=AsyncMock(side_effect=lambda _id: gate_requests[0]
                                     if gate_requests else None),
                create_request=AsyncMock(side_effect=create_request),
            )
            service._write_gate = gate
            store.load_write_preparation = AsyncMock(side_effect=lambda *_args:
                                                    preparations[0] if preparations else None)
            store.record_write_preparation = AsyncMock(side_effect=record_preparation)
            submit = service.submit_initial_validation if initial else service.submit
            params = dict(principal=principal, bound_session="session_1",
                          skill_id=manifest.skill.skill_id, skill_version=manifest.skill.version,
                          arguments={"business_key": "CASE_1"}, client_request_id="retry_write")
            with pytest.raises(BrowserChatError) as interrupted:
                await submit(**params)
            assert interrupted.value.code == "browser_write_preparation_unavailable"
            assert len(store.accepted) == 1 and store.rejected == []
            committed = store.requests["retry_write"][1].run
            assert committed.phase == "queued" and committed.effect == "not_sent"
            assert preparations == []
            original_gate = gate_requests[0] if gate_requests else None

            # Canonical semantic MAC and current Run authority precede recovery.
            calls = gate.create_request.await_count
            for changed in ({"arguments": {"business_key": "CASE_2"}}, {"skill_version": "v2"}):
                with pytest.raises(BrowserChatError) as conflict:
                    await submit(**{**params, **changed})
                assert conflict.value.code == "request_key_conflict"
            store.deny_read = True
            with pytest.raises(BrowserChatError) as denied:
                await submit(**params)
            assert denied.value.code == "browser_run_not_found"
            assert gate.create_request.await_count == calls
            store.deny_read = False

            recovered = await submit(**params)
            assert recovered.data["run_id"] == committed.run_id
            assert len(store.accepted) == 1 and len(preparations) == 1 and len(gate_requests) == 1
            assert preparations[0].gate_request(committed.owner) == gate_requests[0]
            if original_gate is not None:
                assert gate_requests[0] == original_gate
                assert preparations[0].requested_at == original_gate.requested_at
                assert preparations[0].expires_at == original_gate.expires_at
            counts = (gate.create_request.await_count, store.record_write_preparation.await_count)
            repeated = await submit(**params)
            assert repeated.data["run_id"] == committed.run_id
            assert counts == (gate.create_request.await_count,
                              store.record_write_preparation.await_count)

            # Dispatch/consumption advances the same Run atomically. Retries
            # after acquisition, sending or completion perform no preparation IO.
            for phase in ("acquiring", "running", None):
                advanced = replace(committed, phase=phase, worker_id="worker", worker_epoch=1,
                                   lease_epoch=1, effect="acknowledged",
                                   status="completed" if phase is None else "running")
                semantic, canonical = store.requests["retry_write"]
                store.requests["retry_write"] = (semantic, replace(canonical, run=advanced))
                actual = await submit(**params)
                assert actual.data["run_id"] == committed.run_id
                assert counts == (gate.create_request.await_count,
                                  store.record_write_preparation.await_count)
            assert len(store.accepted) == 1 and store.rejected == []
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["matching", "missing", "storage_error"])
def test_preparation_cas_requires_exact_record_and_propagates_storage_failure(case):
    async def scenario() -> None:
        from tests.infra.browser.test_initial_write_publication import write_manifest

        service, _, _, principal = _service(source_kind="registered_site")
        token = _authentication(principal)
        try:
            manifest = write_manifest()
            owner = service._owner(principal, "session_1")
            run = _write_run(owner)
            run = replace(run, admission=replace(
                run.admission, publication_digest=bytes.fromhex(manifest.digest),
            ), phase="queued", worker_id=None, lease_epoch=None, effect="not_sent")
            binding = build_task_version_binding_manifest(
                task_id=run.task_id, bindings=(VersionBinding(
                    resource_type="skill", resource_id=manifest.skill.skill_id,
                    version=manifest.skill.version, digest=manifest.skill.digest,
                ),), locked_at=datetime.now(UTC),
            )
            persisted = []

            async def record(_run, preparation):
                if case == "matching":
                    persisted.append(preparation)
                raise BrowserRunStoreError("browser_preparation_storage_unavailable"
                                           if case == "storage_error" else "browser_run_stale")

            store = Mock(
                get=AsyncMock(return_value=run),
                load_write_preparation=AsyncMock(side_effect=lambda *_args:
                                                persisted[0] if persisted else None),
                record_write_preparation=AsyncMock(side_effect=record),
            )
            gate = Mock(get_task_binding=AsyncMock(return_value=binding),
                        get_request=AsyncMock(return_value=None), create_request=AsyncMock())
            service._store, service._write_gate = store, gate
            service._confirmed_key_digest = lambda _digest, _key: "b" * 64
            params = (owner, run.task_id, run.run_id, manifest, {"business_key": "CASE_1"})
            if case == "matching":
                recovered = await service._ensure_write_preparation(*params)
                assert recovered == persisted[0]
                assert store.load_write_preparation.await_count == 2
            else:
                with pytest.raises(BrowserRunStoreError) as caught:
                    await service._ensure_write_preparation(*params)
                assert caught.value.code == ("browser_preparation_storage_unavailable"
                                             if case == "storage_error" else "browser_run_stale")
                assert store.load_write_preparation.await_count == (
                    1 if case == "storage_error" else 2
                )
            store.record_write_preparation.assert_awaited_once()
            gate.create_request.assert_awaited_once()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("denied", [None, "source", "candidate"])
def test_exploration_capture_uses_source_read_authority_and_candidate_capture_grant(
    monkeypatch, denied,
) -> None:
    async def scenario() -> None:
        service, _, _, principal = _service()
        token = _authentication(principal)
        try:
            owner = service._owner(principal, "session_1")
            run = _write_run(owner, completed=True)
            digest = run.admission.publication_digest.hex()
            base = SimpleNamespace(
                digest=digest, site=SimpleNamespace(exploration=object(), primitive_rules=()),
                source_kind="registered_site", effect="read_only",
            )
            candidate = SimpleNamespace(digest="b" * 64)
            source = Mock(
                get=AsyncMock(return_value=run), load_teaching_plan=AsyncMock(return_value=None),
                read_teaching_steps=AsyncMock(return_value=()),
                load_traversal_terminal=AsyncMock(return_value=0),
            )
            if denied == "source":
                source.get.side_effect = BrowserRunStoreError("browser_run_not_found")
            source_publications = Mock(get_frozen=AsyncMock(return_value=SimpleNamespace(
                state="active", manifest=base,
            )))
            service._store = Mock(get=AsyncMock(side_effect=AssertionError("candidate Run read")))
            service._teaching_base_digest = digest
            service._teaching_candidate_digest = candidate.digest
            service._teaching_candidate_version = "v2"
            service._teaching_publication = AsyncMock(return_value=SimpleNamespace(
                state="prepared", manifest=candidate,
            ))
            service._teaching_capture_stores = AsyncMock(return_value=(source, source_publications))
            service._teaching_capture_grant = AsyncMock(return_value=denied != "candidate")
            captured = object()
            capture = Mock(return_value=captured)
            monkeypatch.setattr("app.browser_skill.submission.capture_authorized_run", capture)
            if denied is None:
                actual = await service.capture_teaching(principal, "session_1", "task_1", "run_1")
                assert actual is captured
                assert source.get.await_count == 2
                source_publications.get_frozen.assert_awaited_once_with(owner, digest)
                assert service._teaching_capture_grant.await_count == 2
                capture.assert_called_once()
            else:
                with pytest.raises(BrowserChatError) as caught:
                    await service.capture_teaching(principal, "session_1", "task_1", "run_1")
                assert caught.value.code == (
                    "browser_teaching_capture_denied" if denied == "candidate"
                    else "browser_teaching_capture_unavailable"
                )
                capture.assert_not_called()
            service._store.get.assert_not_awaited()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["prepared", "missing_plan", "wrong_plan", "revoked"])
def test_verified_prepared_validation_result_requires_exact_plan_and_current_read(case) -> None:
    async def scenario() -> None:
        service, _, _, principal = _service(source_kind="registered_site")
        token = _authentication(principal)
        try:
            owner = service._owner(principal, "session_1")
            run = _write_run(owner, completed=True)
            digest = run.admission.publication_digest.hex()
            plan = SimpleNamespace(task_id=run.task_id, run_id=run.run_id,
                                   publication_digest=digest)
            if case == "wrong_plan":
                plan.publication_digest = "c" * 64
            schema = ('{"type":"object","properties":{"business_key":{"type":"string"}},'
                      '"required":["business_key"],"additionalProperties":false}')
            manifest = SimpleNamespace(digest=digest, output=SimpleNamespace(
                output_schema_json=schema, maximum_bytes=4096,
            ))
            service._teaching_candidate_digest = digest
            service._teaching_candidate_version = "v1"
            service._teaching_publication = AsyncMock(return_value=SimpleNamespace(
                state="prepared", manifest=manifest,
            ))
            store = Mock(get=AsyncMock(return_value=run), load_teaching_plan=AsyncMock(
                return_value=None if case == "missing_plan" else plan,
            ))
            if case == "revoked":
                store.get.side_effect = [run, BrowserRunStoreError("browser_run_not_found")]
            service._store = store
            service._publications = Mock(get_frozen=AsyncMock(return_value=None))
            service._cipher = Mock(decrypt_result=Mock(return_value={"business_key": "CASE_1"}))
            if case == "prepared":
                response = await service.get(principal, "session_1", run.task_id, run.run_id)
                assert response.value == {"business_key": "CASE_1"}
                assert response.run.status == "completed"
                assert store.get.await_count == 2
                service._teaching_publication.assert_awaited_once_with(owner, "skill_1", "v1")
            else:
                with pytest.raises(BrowserChatError) as caught:
                    await service.get(principal, "session_1", run.task_id, run.run_id)
                assert caught.value.code == (
                    "browser_run_not_found" if case == "revoked"
                    else "browser_teaching_candidate_unavailable"
                )
                if case != "revoked":
                    service._cipher.decrypt_result.assert_not_called()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


def test_structured_submission_is_same_subject_and_replay_checks_current_run() -> None:
    async def scenario() -> None:
        service, store, cipher, principal = _service()
        token = _authentication(principal)
        try:
            params = dict(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                skill_version="v1", arguments={"business_key": "CASE_1"},
                client_request_id="request_1",
            )
            first = await service.submit(**params)
            assert first.data["kind"] == "accepted"
            assert cipher.payload is not None
            assert cipher.payload["arguments"] == {"business_key": "CASE_1"}
            assert cipher.payload["invocation"] == {
                "caller": "authenticated_session_v1",
                "entrypoint": "structured_internal",
                "operator_user_id": "user_1", "represented_user_id": "user_1",
            }
            repeated = await service.submit(**params)
            assert repeated.data["run_id"] == first.data["run_id"]
            assert len(store.accepted) == 1 and store.reads == 1
            with pytest.raises(BrowserChatError) as conflict:
                await service.submit(**{**params, "arguments": {"business_key": "CASE_2"}})
            assert conflict.value.code == "request_key_conflict"
            store.deny_read = True
            with pytest.raises(BrowserChatError) as revoked:
                await service.submit(**params)
            assert revoked.value.code == "browser_run_not_found"
        finally:
            authenticated_session.reset(token)

    asyncio.run(scenario())


def test_chat_adapter_uses_same_submission_without_reparsing_a_replay() -> None:
    async def scenario() -> None:
        service, store, _, principal = _service()
        parser = _Parser()
        chat = BrowserChatService(service, parser)
        token = _authentication(principal)
        try:
            params = dict(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                channel="web", message="read CASE_1",
                client_capabilities={"browser_async_v1": True},
                client_request_id="chat_1",
            )
            await chat.start(**params)
            await chat.start(**params)
            assert parser.calls == 1 and len(store.accepted) == 1
            with pytest.raises(BrowserChatError) as conflict:
                await service.submit(
                    principal=principal, bound_session="session_1", skill_id="skill_1",
                    skill_version="v1", arguments={"business_key": "CASE_1"},
                    client_request_id="chat_1",
                )
            assert conflict.value.code == "request_key_conflict"
        finally:
            authenticated_session.reset(token)

    asyncio.run(scenario())


def test_foreign_or_expired_identity_and_unpublished_version_fail_closed() -> None:
    async def scenario() -> None:
        service, store, _, principal = _service()
        params = dict(
            principal=principal, bound_session="session_1", skill_id="skill_1",
            skill_version="v1", arguments={"business_key": "CASE_1"},
            client_request_id="request_1",
        )
        foreign = Principal(
            ai_user_id="other_user", display_name="Other", roles=("reader",),
            org_ctx=PrincipalOrgContext(tenant_id="tenant_1"),
        )
        token = _authentication(foreign)
        try:
            with pytest.raises(BrowserChatError) as denied:
                await service.submit(**params)
            assert denied.value.code == "authentication_required"
            with pytest.raises(BrowserChatError):
                await service.get(principal, "session_1", "task_1", "run_1")
            with pytest.raises(BrowserChatError):
                await service.cancel(principal, "session_1", "task_1", "run_1")
            assert store.requests == {}
        finally:
            authenticated_session.reset(token)
        token = _authentication(principal, expired=True)
        try:
            with pytest.raises(BrowserChatError) as denied:
                await service.submit(**params)
            assert denied.value.code == "authentication_required"
        finally:
            authenticated_session.reset(token)
        token = _authentication(principal)
        try:
            wrong = await service.submit(**{**params, "skill_version": "v2"})
            assert wrong.data["error_code"] == "browser_publication_unavailable"
            assert store.accepted == []
        finally:
            authenticated_session.reset(token)

    asyncio.run(scenario())


def test_parser_cannot_replace_session_proof_before_admission() -> None:
    async def scenario() -> None:
        service, store, _, principal = _service()

        class SwappingParser:
            async def parse(
                self, _principal: Principal, _message: str, _capability: object,
            ) -> dict[str, Any]:
                authenticated_session.set(AuthenticatedSessionContext(
                    principal, b"other-proof".ljust(32, b"x"),
                    datetime.now(UTC) + timedelta(minutes=5),
                ))
                return {"business_key": "CASE_1"}

        chat = BrowserChatService(service, SwappingParser())
        token = _authentication(principal)
        try:
            response = await chat.start(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                channel="web", message="read CASE_1",
                client_capabilities={"browser_async_v1": True},
                client_request_id="chat_1",
            )
            assert response.data["error_code"] == "authentication_required"
            assert store.accepted == []
        finally:
            authenticated_session.reset(token)

    asyncio.run(scenario())


def test_represented_identity_cannot_be_smuggled_through_business_arguments() -> None:
    async def scenario() -> None:
        service, store, _, principal = _service()
        token = _authentication(principal)
        try:
            response = await service.submit(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                skill_version="v1",
                arguments={"business_key": "CASE_1", "represented_user_id": "other_user"},
                client_request_id="request_1",
            )
            assert response.data["error_code"] == "browser_request_input_invalid"
            assert len(store.rejected) == 1 and store.accepted == []
        finally:
            authenticated_session.reset(token)

    asyncio.run(scenario())


def test_non_oa_target_requires_a_registered_source_and_matching_binding() -> None:
    async def scenario() -> None:
        params = dict(
            principal=None, bound_session="session_1", skill_id="skill_1",
            skill_version="v1", arguments={"business_key": "CASE_1"},
            client_request_id="request_1",
        )
        synthetic, synthetic_store, _, principal = _service(target_system="u8")
        token = _authentication(principal)
        try:
            denied = await synthetic.submit(**{**params, "principal": principal})
            assert denied.data["error_code"] == "browser_capability_unavailable"
            assert synthetic_store.accepted == []
        finally:
            authenticated_session.reset(token)

        # This fake represents an already trusted, published Site. The real
        # manifest verifier and publication store remain independent gates.
        registered, registered_store, _, principal = _service(
            target_system="u8", source_kind="registered_site",
        )
        token = _authentication(principal)
        try:
            accepted = await registered.submit(**{**params, "principal": principal})
            assert accepted.data["kind"] == "accepted"
            assert registered_store.accepted[0].target_system == "u8"
        finally:
            authenticated_session.reset(token)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("stage", "failure", "expected_type", "expected_code", "trusted_code"),
    [
        ("publication", RuntimeError("TEST_ONLY_PRIVATE_EXCEPTION_TEXT"), "RuntimeError",
         "browser_request_admission_failed", None),
        ("binding", BrowserAuthorizationError("browser_binding_denied"),
         "BrowserAuthorizationError", "browser_binding_denied", "browser_binding_denied"),
        ("encryption", ValueError("TEST_ONLY_PRIVATE_EXCEPTION_TEXT"), "ValueError",
         "browser_request_admission_failed", None),
        ("teaching_plan", BrowserRunStoreError("browser_initial_plan_invalid"),
         "BrowserRunStoreError", "browser_initial_plan_invalid", "browser_initial_plan_invalid"),
        ("encryption", type("Unsafe\nTEST_ONLY_PRIVATE_CLASS", (ValueError,),
                            {"code": "browser_untrusted_detail"})(
                                "TEST_ONLY_PRIVATE_EXCEPTION_TEXT"),
         "UnknownException", "browser_request_admission_failed", None),
    ],
)
def test_initial_admission_diagnostic_is_safe_and_preserves_failure(
    caplog, stage, failure, expected_type, expected_code, trusted_code,
) -> None:
    async def scenario() -> None:
        service, store, cipher, principal = _service(source_kind="registered_site")
        token = _authentication(principal)
        try:
            publication = await service._publications.get_active(
                service._owner(principal, "session_1"), "skill_1",
            )
            service._teaching_candidate_digest = publication.manifest.digest
            service._teaching_publication = AsyncMock(return_value=publication)
            service._teaching_plan = AsyncMock(return_value=Mock())
            if stage == "publication":
                service._teaching_publication.side_effect = failure
            elif stage == "binding":
                service._bindings.resolve = AsyncMock(side_effect=failure)
            elif stage == "encryption":
                cipher.encrypt_input = Mock(side_effect=failure)
            else:
                service._teaching_plan.side_effect = failure
            response = await service.submit_initial_validation(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                skill_version="v1", arguments={"business_key": "CASE_1"},
                client_request_id="initial_diagnostic_1",
            )
            assert response.status == "failed"
            assert response.data == {"kind": "failed", "error_code": expected_code}
            assert store.rejected == [expected_code]
            assert store.accepted == []
        finally:
            authenticated_session.reset(token)

    with caplog.at_level(logging.ERROR, logger="app.browser_skill.submission"):
        asyncio.run(scenario())
    records = [record for record in caplog.records
               if record.name == "app.browser_skill.submission"]
    assert len(records) == 1
    record = records[0]
    prefix = "browser_request_admission_diagnostic "
    assert record.getMessage().startswith(prefix)
    diagnostic = json.loads(record.getMessage()[len(prefix):])
    assert diagnostic == {
        "stage": stage, "exception_type": expected_type,
        "trusted_error_code": trusted_code, "aggregate_code": expected_code,
        "task_id": "task_1",
    }
    assert record.exc_info is None
    assert record.stack_info is None
    for private in ("TEST_ONLY_PRIVATE_EXCEPTION_TEXT", "TEST_ONLY_PRIVATE_CLASS",
                    "browser_untrusted_detail", "CASE_1", "user_1", "tenant_1", "session_1"):
        assert private not in caplog.text
