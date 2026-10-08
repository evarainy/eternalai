"""Structured and Chat admission share one authenticated durable service."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

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
        run = SimpleNamespace(
            owner=request.owner, task_id=request.task_id,
            run_id=admission.run_id, state_revision=1,
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
            return SimpleNamespace(manifest=manifest)

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
            assert denied.data["error_code"] == "browser_capability_not_readonly"
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
