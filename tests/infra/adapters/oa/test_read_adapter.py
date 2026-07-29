from __future__ import annotations

import asyncio
import copy
import json
import logging
import secrets
import shutil
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import OpenerDirector, Request

import pytest
from pydantic import SecretStr

from app.infra.adapters.oa import provider as oa_provider
from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    build_structural_fingerprint,
    compare_structural_fingerprints,
)
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    OALiveIdentityExpired,
    OALivePayloadInvalid,
    OALiveRequestError,
    OALiveTimeout,
    ReplayOAReadProvider,
    report_oa_structural_drift,
)
from app.infra.auth.secret_provider import CredentialStoreSecretProvider
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.auth import OASessionCredential
from app.ports.capability_gateway import RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.identity_mapping import IdentityCheckResult
from app.ports.secret_provider import (
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialStorageError,
    InvalidCredentialReferenceError,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PACK = (
    REPO_ROOT
    / "tests"
    / "contract_packs"
    / "oa"
    / "ecology9-pending-workflows-v1"
)
EXPECTED_REPLAY_DATA: dict[str, Any] = {
    "workflows": [
        {
            "workflow_id": "workflow-synthetic-001",
            "title": "workflow-title-synthetic-001",
            "status": "pending",
            "applicant": "applicant-synthetic-001",
            "current_step": "step-synthetic-001",
            "approver": "approver-synthetic-001",
            "created_at": "2000-01-01T00:00:00+00:00",
            "expired": False,
        },
        {
            "workflow_id": "workflow-synthetic-002",
            "title": "workflow-title-synthetic-002",
            "status": "pending",
            "applicant": "applicant-synthetic-002",
            "current_step": "step-synthetic-002",
            "approver": None,
            "created_at": None,
            "expired": True,
        },
    ]
}


class CountingReplayProvider(ReplayOAReadProvider):
    def __init__(self, contract_pack_dir: Path) -> None:
        super().__init__(contract_pack_dir)
        self.calls = 0

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        self.calls += 1
        return await super().list_pending_workflows(credential)


class StaticSecretProvider:
    def __init__(self, credential: OASessionCredential) -> None:
        self._credential = credential
        self.calls: list[str] = []

    async def resolve_oa_session(
        self,
        credential_ref: str,
    ) -> OASessionCredential:
        self.calls.append(credential_ref)
        return self._credential


class RaisingSecretProvider:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def resolve_oa_session(
        self,
        credential_ref: str,
    ) -> OASessionCredential:
        del credential_ref
        raise self._error


class FakeHTTPResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._status_code = status_code
        self._raw = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def getcode(self) -> int:
        return self._status_code

    def read(self, limit: int) -> bytes:
        del limit
        return self._raw


class SequencedOpener:
    def __init__(self, *responses: FakeHTTPResponse | Exception) -> None:
        self._responses = list(responses)
        self.request_queries: list[dict[str, list[str]]] = []
        self.cookie_header_present: list[bool] = []

    def open(self, request: Request, *, timeout: float) -> FakeHTTPResponse:
        assert timeout > 0
        self.request_queries.append(parse_qs(urlsplit(request.full_url).query))
        self.cookie_header_present.append(request.has_header("Cookie"))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingHTTPServer(ThreadingHTTPServer):
    def __init__(self, response_payload: dict[str, Any]) -> None:
        super().__init__(("127.0.0.1", 0), RecordingHTTPRequestHandler)
        self.response_payload = response_payload
        self.requests: list[tuple[str, str | None, bool]] = []


class RecordingHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = cast(RecordingHTTPServer, self.server)
        server.requests.append(
            (
                self.path,
                self.headers.get("Host"),
                self.headers.get("Cookie") is not None,
            )
        )
        payload = json.dumps(server.response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _start_recording_server(
    response_payload: dict[str, Any],
) -> tuple[RecordingHTTPServer, threading.Thread]:
    server = RecordingHTTPServer(response_payload)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _credential(*, cookie_value: str | None = None) -> OASessionCredential:
    return OASessionCredential(
        oa_user_id=SecretStr(secrets.token_hex(16)),
        cookies={
            "ecology_JSessionid": SecretStr(
                cookie_value if cookie_value is not None else secrets.token_hex(24)
            )
        },
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _live_provider(
    opener: SequencedOpener,
    *,
    drift_reporter: Any = None,
    max_pages: int = 50,
) -> LiveOAReadProvider:
    return LiveOAReadProvider(
        base_url="https://oa.synthetic.invalid",
        endpoint_path="/api/pending",
        timeout_seconds=2.0,
        contract_pack_dir=CONTRACT_PACK,
        drift_reporter=drift_reporter,
        max_pages=max_pages,
        opener_factory=lambda: cast(OpenerDirector, opener),
    )


def _raw_workflow(index: int) -> dict[str, Any]:
    return {
        "workflowId": f"live-workflow-{index}",
        "title": f"live-title-{index}",
        "status": "pending",
        "applicant": f"live-applicant-{index}",
        "currentStep": f"live-step-{index}",
        "approver": None,
        "createdAt": "2026-07-30T00:00:00+00:00",
        "expired": False,
    }


def test_live_default_opener_ignores_all_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oa_server, oa_thread = _start_recording_server(
        {
            "data": {
                "records": [_raw_workflow(1)],
                "hasMore": False,
            }
        }
    )
    proxy_server, proxy_thread = _start_recording_server(
        {
            "data": {
                "records": [_raw_workflow(99)],
                "hasMore": False,
            }
        }
    )
    oa_host = f"127.0.0.1:{oa_server.server_address[1]}"
    proxy_url = f"http://127.0.0.1:{proxy_server.server_address[1]}"
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, proxy_url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "proxy-bypass.synthetic.invalid")
    provider = LiveOAReadProvider(
        base_url=f"http://{oa_host}",
        endpoint_path="/api/pending",
        timeout_seconds=2.0,
        contract_pack_dir=CONTRACT_PACK,
    )

    try:
        collection = asyncio.run(
            provider.list_pending_workflows(
                _credential(cookie_value=secrets.token_hex(32))
            )
        )
    finally:
        oa_server.shutdown()
        proxy_server.shutdown()
        oa_server.server_close()
        proxy_server.server_close()
        oa_thread.join(timeout=2)
        proxy_thread.join(timeout=2)

    assert [workflow.workflow_id for workflow in collection.workflows] == [
        "live-workflow-1"
    ]
    assert proxy_server.requests == []
    assert len(oa_server.requests) == 1
    request_path, request_host, cookie_present = oa_server.requests[0]
    assert request_host == oa_host
    assert request_path.startswith("/api/pending?")
    assert cookie_present is True


def _assert_provider_traceback_is_redacted(
    error: BaseException,
    marker: str,
) -> None:
    assert error.__context__ is None
    assert error.__cause__ is None
    provider_frames = 0
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == oa_provider.__name__:
            provider_frames += 1
            for value in frame.f_locals.values():
                if isinstance(value, OASessionCredential):
                    raise AssertionError(
                        "provider exception frame retained plaintext credential"
                    )
                if isinstance(value, Request):
                    assert marker not in repr(value.header_items())
                assert marker not in repr(value)
        traceback = traceback.tb_next
    assert provider_frames > 0


def test_replay_adapter_returns_every_normalized_field_exactly() -> None:
    adapter = OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    assert result.error_code is None
    assert result.raw_payload_ref is None
    assert result.data == EXPECTED_REPLAY_DATA


def test_replay_adapter_runs_through_gateway_with_completed_status() -> None:
    gateway = CapabilityGateway(OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK)))

    result = asyncio.run(
        gateway.execute_capability(
            "task-replay-001",
            "session-replay-001",
            "ai-user-replay-001",
            "oa.list_pending_workflows",
            {},
            RequestOrgContext(request_id="trace-replay-001"),
        )
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.data == EXPECTED_REPLAY_DATA


def test_unknown_capability_returns_adapter_error_and_gateway_failed() -> None:
    provider = CountingReplayProvider(CONTRACT_PACK)
    adapter = OAReadAdapter(provider)
    direct_result = asyncio.run(adapter.execute("oa.unlisted_capability", {}, {}))

    gateway = CapabilityGateway(adapter)
    gateway_result = asyncio.run(
        gateway.execute_capability(
            "task-unknown-001",
            "session-unknown-001",
            "ai-user-unknown-001",
            "oa.unlisted_capability",
            {},
            RequestOrgContext(request_id="trace-unknown-001"),
        )
    )

    assert direct_result.status == "error"
    assert direct_result.error_code == "adapter_error"
    assert direct_result.data is None
    assert gateway_result.status == "failed"
    assert gateway_result.error_code == "adapter_error"
    assert gateway_result.data is None
    assert provider.calls == 0


def test_extra_capability_arguments_fail_closed_without_provider_call() -> None:
    provider = CountingReplayProvider(CONTRACT_PACK)
    adapter = OAReadAdapter(provider)

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {"page_size": 100},
            {"ignored": "context"},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None
    assert provider.calls == 0


def test_live_adapter_requires_server_issued_credential_reference() -> None:
    opener = SequencedOpener(
        FakeHTTPResponse({"data": {"records": [], "hasMore": False}})
    )
    secret_provider = StaticSecretProvider(_credential())
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=secret_provider,
    )

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "identity_unbound"
    assert result.data is None
    assert secret_provider.calls == []
    assert opener.request_queries == []


def test_replay_rejects_structural_fingerprint_mismatch(tmp_path: Path) -> None:
    pack = tmp_path / CONTRACT_PACK.name
    shutil.copytree(CONTRACT_PACK, pack)
    sample_path = pack / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["workflows"][0]["status"] = 1
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    adapter = OAReadAdapter(ReplayOAReadProvider(pack))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None


def test_replay_maps_model_violation_to_payload_invalid(tmp_path: Path) -> None:
    pack = tmp_path / CONTRACT_PACK.name
    shutil.copytree(CONTRACT_PACK, pack)
    sample_path = pack / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["workflows"][0]["unexpected"] = "not-allowed"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    fingerprint = build_structural_fingerprint(sample)
    (pack / "fingerprint.json").write_text(
        json.dumps(fingerprint),
        encoding="utf-8",
    )
    adapter = OAReadAdapter(ReplayOAReadProvider(pack))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


def test_structural_fingerprint_excludes_values_and_array_length() -> None:
    first = {"workflows": [copy.deepcopy(EXPECTED_REPLAY_DATA["workflows"][0])]}
    second_item = copy.deepcopy(first["workflows"][0])
    second_item.update(
        {
            "workflow_id": "completely-different-workflow",
            "title": "completely-different-title",
            "status": "different-status",
            "applicant": "completely-different-applicant",
            "current_step": "completely-different-step",
            "approver": "completely-different-approver",
            "created_at": "2099-12-31T23:59:59+00:00",
            "expired": True,
        }
    )
    second = {"workflows": [second_item, copy.deepcopy(second_item)]}
    empty = {"workflows": []}

    first_fingerprint = build_structural_fingerprint(first)
    second_fingerprint = build_structural_fingerprint(second)
    empty_fingerprint = build_structural_fingerprint(empty)

    assert first_fingerprint == second_fingerprint
    assert first_fingerprint == empty_fingerprint
    rendered = json.dumps(first_fingerprint)
    for business_value in first["workflows"][0].values():
        assert str(business_value) not in rendered


def test_replay_accepts_legal_empty_workflow_collection(tmp_path: Path) -> None:
    pack = tmp_path / CONTRACT_PACK.name
    shutil.copytree(CONTRACT_PACK, pack)
    empty_sample = {"workflows": []}
    (pack / "sample.json").write_text(
        json.dumps(empty_sample),
        encoding="utf-8",
    )
    (pack / "fingerprint.json").write_text(
        json.dumps(build_structural_fingerprint(empty_sample)),
        encoding="utf-8",
    )
    adapter = OAReadAdapter(ReplayOAReadProvider(pack))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    assert result.error_code is None
    assert result.data == {"workflows": []}


def test_replay_never_resolves_or_uses_a_credential() -> None:
    secret_provider = RaisingSecretProvider(
        AssertionError("Replay must not resolve a credential")
    )
    adapter = OAReadAdapter(
        ReplayOAReadProvider(CONTRACT_PACK),
        secret_provider=secret_provider,
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:unused"},
        )
    )

    assert result.status == "success"
    assert result.data == EXPECTED_REPLAY_DATA


def test_live_provider_paginates_internally_and_normalizes_records() -> None:
    opener = SequencedOpener(
        FakeHTTPResponse(
            {
                "data": {
                    "records": [_raw_workflow(1)],
                    "hasMore": True,
                    "total": 2,
                    "nextCursor": "cursor-2",
                }
            }
        ),
        FakeHTTPResponse(
            {
                "data": {
                    "records": [_raw_workflow(2)],
                    "hasMore": False,
                    "total": 2,
                    "nextCursor": None,
                }
            }
        ),
    )
    secret_provider = StaticSecretProvider(_credential())
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=secret_provider,
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "success"
    assert result.error_code is None
    assert result.data == {
        "workflows": [
            {
                "workflow_id": "live-workflow-1",
                "title": "live-title-1",
                "status": "pending",
                "applicant": "live-applicant-1",
                "current_step": "live-step-1",
                "approver": None,
                "created_at": "2026-07-30T00:00:00+00:00",
                "expired": False,
            },
            {
                "workflow_id": "live-workflow-2",
                "title": "live-title-2",
                "status": "pending",
                "applicant": "live-applicant-2",
                "current_step": "live-step-2",
                "approver": None,
                "created_at": "2026-07-30T00:00:00+00:00",
                "expired": False,
            },
        ]
    }
    assert opener.request_queries == [
        {"page": ["1"], "pageSize": ["100"]},
        {"page": ["2"], "pageSize": ["100"], "cursor": ["cursor-2"]},
    ]
    assert opener.cookie_header_present == [True, True]
    assert secret_provider.calls == ["oa-session-v1:server-surrogate"]


def test_server_mapped_live_cookie_never_enters_gateway_trace(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = secrets.token_hex(32)
    trusted_ai_user_id = f"usr_v1_{'a' * 43}"
    credential_ref = f"oa-session-v1:{trusted_ai_user_id}"
    credential = _credential(cookie_value=marker)
    capability = CapabilitySpec(
        capability_id="oa.list_pending_workflows",
        name="OA pending workflows",
        type="query",
        input_schema_digest="input-digest",
        output_schema_digest="output-digest",
        risk_level="low",
        owner="phase2",
        version="1.0.0",
        status="active",
        short_description="OA pending workflows",
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=True,
    )

    class Registry:
        async def get(self, capability_id: str) -> CapabilitySpec | None:
            assert capability_id == capability.capability_id
            return capability

    class IdentityMapping:
        async def resolve_execution_identity(
            self,
            ai_user_id: str,
            target_system: str,
            execution_identity: str,
            request_context: RequestOrgContext,
        ) -> IdentityCheckResult:
            assert ai_user_id == trusted_ai_user_id
            assert target_system == "oa"
            assert execution_identity == "user_delegated"
            del request_context
            return IdentityCheckResult(
                bind_status="active",
                binding_id=credential_ref,
                target_system="oa",
                execution_identity="user_delegated",
            )

    class CredentialStore:
        async def load(
            self,
            loaded_ai_user_id: str,
        ) -> OASessionCredential | None:
            assert loaded_ai_user_id == trusted_ai_user_id
            return credential

    opener = SequencedOpener(
        FakeHTTPResponse(
            {
                "data": {
                    "records": [_raw_workflow(1)],
                    "hasMore": False,
                    "total": 1,
                }
            }
        )
    )
    secret_provider = CredentialStoreSecretProvider(
        credential_store=cast(Any, CredentialStore()),
    )
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=secret_provider,
    )
    monkeypatch.setenv("ENV", "testing")
    trace_logger = logging.getLogger("tests.oa.live_trace")
    trace = NoopTraceWriter(logger=trace_logger)
    caplog.set_level(logging.DEBUG, logger=trace_logger.name)
    gateway = CapabilityGateway(
        capability_registry=cast(Any, Registry()),
        identity_mapping=cast(Any, IdentityMapping()),
        policy_guard=MinimalPolicyGuard(),
        trace_port=trace,
        adapters={"oa": adapter},
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-live-trace-001",
            "session-live-trace-001",
            trusted_ai_user_id,
            capability.capability_id,
            {},
            RequestOrgContext(request_id="trace-live-cookie-001"),
        )
    )
    rendered_trace = "\n".join(
        repr(record.__dict__) for record in caplog.records
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert marker not in (rendered_trace + repr(result) + caplog.text)
    assert credential_ref not in rendered_trace


def test_live_provider_accepts_a_legal_empty_collection() -> None:
    opener = SequencedOpener(
        FakeHTTPResponse(
            {
                "data": {
                    "records": [],
                    "hasMore": False,
                    "total": 0,
                    "nextCursor": None,
                }
            }
        )
    )
    collection = asyncio.run(
        _live_provider(opener).list_pending_workflows(_credential())
    )

    assert collection.model_dump(mode="json") == {"workflows": []}
    assert len(opener.request_queries) == 1


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error_code"),
    [
        (401, "error", "identity_expired"),
        (403, "permission_denied", "upstream_permission_denied"),
        (500, "error", "adapter_http_500"),
    ],
)
def test_live_http_status_is_safely_classified(
    status_code: int,
    expected_status: str,
    expected_error_code: str,
) -> None:
    opener = SequencedOpener(
        FakeHTTPResponse({}, status_code=status_code)
    )
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential()),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == expected_status
    assert result.error_code == expected_error_code
    assert result.data is None


def test_live_business_session_expiry_requires_reauthentication() -> None:
    opener = SequencedOpener(
        FakeHTTPResponse({"errorCode": "session_expired"})
    )
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential()),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "error"
    assert result.error_code == "identity_expired"


@pytest.mark.parametrize(
    ("response", "expected_error_code"),
    [
        (TimeoutError(), "adapter_timeout"),
        (FakeHTTPResponse(b"{not-json"), "adapter_payload_invalid"),
        (
            FakeHTTPResponse(
                {
                    "data": {
                        "records": [_raw_workflow(1)],
                    }
                }
            ),
            "adapter_payload_invalid",
        ),
        (
            FakeHTTPResponse(
                {
                    "data": {
                        "records": [
                            {
                                **_raw_workflow(1),
                                "status": "not-pending",
                            }
                        ],
                        "hasMore": False,
                    }
                }
            ),
            "adapter_payload_invalid",
        ),
    ],
)
def test_live_timeout_and_payload_failures_are_classified(
    response: FakeHTTPResponse | Exception,
    expected_error_code: str,
) -> None:
    opener = SequencedOpener(response)
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential()),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.error_code == expected_error_code
    assert result.data is None


@pytest.mark.parametrize(
    ("failure_kind", "expected_error_type"),
    [
        ("timeout", OALiveTimeout),
        ("http_error", OALiveIdentityExpired),
        ("invalid_json", OALivePayloadInvalid),
        ("invalid_cookie", OALiveRequestError),
    ],
)
def test_live_typed_errors_discard_sensitive_context_and_traceback_locals(
    failure_kind: str,
    expected_error_type: type[Exception],
) -> None:
    marker = secrets.token_hex(32)
    credential = _credential(
        cookie_value=(
            f"{marker};invalid"
            if failure_kind == "invalid_cookie"
            else marker
        )
    )
    response: FakeHTTPResponse | Exception
    if failure_kind == "timeout":
        response = TimeoutError(marker)
    elif failure_kind == "http_error":
        response = HTTPError(
            "https://oa.synthetic.invalid/api/pending",
            401,
            marker,
            None,
            None,
        )
    elif failure_kind == "invalid_json":
        response = FakeHTTPResponse(f'{{"marker":"{marker}"'.encode())
    else:
        response = AssertionError("invalid Cookie must fail before HTTP")
    provider = _live_provider(SequencedOpener(response))

    with pytest.raises(expected_error_type) as exc_info:
        asyncio.run(provider.list_pending_workflows(credential))

    assert marker not in str(exc_info.value)
    _assert_provider_traceback_is_redacted(exc_info.value, marker)


@pytest.mark.parametrize("stage", ["http_request", "payload_processing"])
def test_provider_programming_errors_log_only_safe_classification(
    stage: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = secrets.token_hex(32)
    if stage == "http_request":
        opener = SequencedOpener(RuntimeError(marker))
    else:
        opener = SequencedOpener(
            FakeHTTPResponse(
                {
                    "data": {
                        "records": [_raw_workflow(1)],
                        "hasMore": False,
                    }
                }
            )
        )

        def explode_payload(_payload: Any) -> Any:
            raise RuntimeError(marker)

        monkeypatch.setattr(oa_provider, "_read_page", explode_payload)
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential(cookie_value=marker)),
    )
    caplog.set_level(logging.ERROR, logger=oa_provider.__name__)

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.error_code == "adapter_error"
    assert f"stage={stage}" in caplog.text
    assert "classification=adapter_error" in caplog.text
    assert marker not in (caplog.text + repr(result))


def test_live_pagination_fails_closed_on_repeated_cursor() -> None:
    page = FakeHTTPResponse(
        {
            "data": {
                "records": [_raw_workflow(1)],
                "hasMore": True,
                "nextCursor": "same-cursor",
            }
        }
    )
    opener = SequencedOpener(page, page)
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential()),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert len(opener.request_queries) == 2


@pytest.mark.parametrize(
    ("error", "expected_error_code"),
    [
        (CredentialNotFoundError(), "identity_unbound"),
        (CredentialExpiredError(), "identity_expired"),
        (InvalidCredentialReferenceError(), "adapter_error"),
        (CredentialStorageError(), "adapter_error"),
    ],
)
def test_secret_resolution_errors_are_safely_mapped(
    error: Exception,
    expected_error_code: str,
) -> None:
    opener = SequencedOpener(
        FakeHTTPResponse({"data": {"records": [], "hasMore": False}})
    )
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=RaisingSecretProvider(error),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "error"
    assert result.error_code == expected_error_code
    assert opener.request_queries == []


def test_live_reports_matching_normalized_contract_structure() -> None:
    reports: list[Any] = []
    opener = SequencedOpener(
        FakeHTTPResponse(
            {
                "data": {
                    "records": [_raw_workflow(1)],
                    "hasMore": False,
                    "total": 1,
                }
            }
        )
    )

    collection = asyncio.run(
        _live_provider(
            opener,
            drift_reporter=reports.append,
        ).list_pending_workflows(_credential())
    )

    assert len(collection.workflows) == 1
    assert len(reports) == 1
    report = reports[0]
    assert report.matches is True
    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_structural_drift_report_contains_no_business_values_or_lengths() -> None:
    expected_payload = copy.deepcopy(EXPECTED_REPLAY_DATA)
    actual_payload = copy.deepcopy(EXPECTED_REPLAY_DATA)
    runtime_business_value = secrets.token_hex(24)
    actual_payload["workflows"][0]["newShape"] = runtime_business_value
    actual_payload["workflows"].append(copy.deepcopy(actual_payload["workflows"][0]))

    report = compare_structural_fingerprints(
        build_structural_fingerprint(expected_payload),
        build_structural_fingerprint(actual_payload),
    )
    rendered = report.model_dump_json()

    assert report.matches is False
    assert [node.path for node in report.added] == ["$.workflows[].newShape"]
    assert runtime_business_value not in rendered
    assert "raw" not in rendered.casefold()
    assert '"length"' not in rendered


def test_production_drift_reporter_logs_only_value_free_structure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expected_payload = copy.deepcopy(EXPECTED_REPLAY_DATA)
    actual_payload = copy.deepcopy(EXPECTED_REPLAY_DATA)
    runtime_business_value = secrets.token_hex(24)
    actual_payload["workflows"][0]["newShape"] = runtime_business_value
    report = compare_structural_fingerprints(
        build_structural_fingerprint(expected_payload),
        build_structural_fingerprint(actual_payload),
    )
    caplog.set_level(logging.WARNING, logger=oa_provider.__name__)

    report_oa_structural_drift(report)

    assert "oa_live_structural_drift" in caplog.text
    assert "$.workflows[].newShape" in caplog.text
    assert runtime_business_value not in caplog.text


class ExplodingCredentialProvider:
    requires_credential = True

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        assert credential is not None
        secret = next(iter(credential.cookies.values())).get_secret_value()
        raise RuntimeError(secret)


def test_unexpected_failure_log_result_and_repr_do_not_leak_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_secret = secrets.token_hex(32)
    credential = _credential(cookie_value=runtime_secret)
    secret_provider = StaticSecretProvider(credential)
    provider = ExplodingCredentialProvider()
    adapter = OAReadAdapter(provider, secret_provider=secret_provider)
    caplog.set_level(
        logging.ERROR,
        logger="app.infra.adapters.oa.adapter",
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )
    rendered = "\n".join(
        (
            caplog.text,
            repr(result),
            repr(adapter),
            repr(provider),
            repr(secret_provider),
            repr(credential),
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert "classification=adapter_error" in caplog.text
    assert runtime_secret not in rendered
