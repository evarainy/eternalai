from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import logging
import re
import secrets
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import OpenerDirector, Request

import pytest
from pydantic import SecretStr, ValidationError

from app.infra.adapters.oa import provider as oa_provider
from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.contracts import (
    _LIVE_MESSAGE_CENTER_FIELD_NAMES,
    _LIVE_MESSAGE_CENTER_IGNORED_WIRE_FIELDS,
    _LIVE_PENDING_WORKFLOW_FIELD_NAMES,
    OAMessageCenterRecord,
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
    build_contract_drift_baseline_fingerprint,
    build_live_pending_workflows_fingerprint,
    build_live_system_messages_fingerprint,
    build_structural_fingerprint,
    compare_structural_fingerprints,
)
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    OAContractPackError,
    OAContractPackPayloadInvalid,
    OALiveHTTPServerError,
    OALiveIdentityExpired,
    OALiveIdentityUnbound,
    OALivePayloadInvalid,
    OALivePermissionDenied,
    OALiveProviderError,
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
    SecretProviderError,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PACK = (
    REPO_ROOT
    / "tests"
    / "contract_packs"
    / "oa"
    / "ecology9-pending-workflows-v3"
)
LEGACY_PENDING_V1_CONTRACT_PACK = (
    CONTRACT_PACK.parent / "ecology9-pending-workflows-v1"
)
LEGACY_PENDING_V2_CONTRACT_PACK = (
    CONTRACT_PACK.parent / "ecology9-pending-workflows-v2"
)
SYSTEM_MESSAGE_CONTRACT_PACK = (
    REPO_ROOT
    / "tests"
    / "contract_packs"
    / "oa"
    / "ecology9-system-messages-v1"
)
# Written out by hand, not read back from the pack under test: an expectation
# sourced from the artifact family it verifies would stay green through any
# rewrite of that artifact.
EXPECTED_LEGACY_V2_DATA: dict[str, Any] = {
    "workflows": [
        {
            "message_id": "90000001",
            "title": "统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内",
            "content": (
                "统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统"
            ),
            "source_name": "统消息合成样本",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "2",
            "link": "/oa/system-messages/desktop/001xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/001xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000002",
            "title": (
                "消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消"
                "息合成"
            ),
            "content": (
                "消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消"
            ),
            "source_name": "消息合成样本文",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "3",
            "link": "/oa/system-messages/desktop/002xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/002xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000003",
            "title": "息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系",
            "content": (
                "息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息"
            ),
            "source_name": "息合成样本文本",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "4",
            "link": "/oa/system-messages/desktop/003xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/003xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000004",
            "title": "合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提",
            "content": (
                "合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合"
            ),
            "source_name": "合成样本文本内",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "5",
            "link": "/oa/system-messages/desktop/004xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/004xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000005",
            "title": (
                "成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成"
                "样本文"
            ),
            "content": (
                "成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成"
            ),
            "source_name": "成样本文本内容",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "6",
            "link": "/oa/system-messages/desktop/005xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/005xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000006",
            "title": "样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查",
            "content": (
                "样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样"
            ),
            "source_name": "样本文本内容通",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "7",
            "link": "/oa/system-messages/desktop/006xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/006xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000007",
            "title": (
                "本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本"
                "文本内容通"
            ),
            "content": (
                "本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本"
            ),
            "source_name": "本文本内容通知",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "8",
            "link": "/oa/system-messages/desktop/007xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/007xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000008",
            "title": (
                "文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文"
                "本内容通知"
            ),
            "content": (
                "文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文"
            ),
            "source_name": "文本内容通知提",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "1",
            "link": "/oa/system-messages/desktop/008xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/008xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000009",
            "title": (
                "本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本"
                "内容通知提"
            ),
            "content": (
                "本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本"
            ),
            "source_name": "本内容通知提醒",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "2",
            "link": "/oa/system-messages/desktop/009xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/009xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000010",
            "title": "内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统",
            "content": (
                "内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内"
            ),
            "source_name": "内容通知提醒待",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "3",
            "link": "/oa/system-messages/desktop/010xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/010xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000011",
            "title": "容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本",
            "content": (
                "容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容"
            ),
            "source_name": "容通知提醒待办",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "4",
            "link": "/oa/system-messages/desktop/011xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/011xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000012",
            "title": "通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消",
            "content": (
                "通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通"
            ),
            "source_name": "通知提醒待办查",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "5",
            "link": "/oa/system-messages/desktop/012xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/012xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000013",
            "title": "知提醒待办查阅系统消息合成样本文本内容通知提醒待办查",
            "content": (
                "知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知"
            ),
            "source_name": "知提醒待办查阅",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "6",
            "link": "/oa/system-messages/desktop/013xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/013xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000014",
            "title": "提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成",
            "content": (
                "提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提"
            ),
            "source_name": "提醒待办查阅系",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "7",
            "link": "/oa/system-messages/desktop/014xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/014xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000015",
            "title": "醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通",
            "content": (
                "醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒"
            ),
            "source_name": "醒待办查阅系统",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "8",
            "link": "/oa/system-messages/desktop/015xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/015xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000016",
            "title": (
                "待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待"
                "办查阅系统消息合成样本文本内容通知提醒"
            ),
            "content": (
                "待办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待"
            ),
            "source_name": "待办查阅系统消",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "1",
            "link": "/oa/system-messages/desktop/016xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/016xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000017",
            "title": (
                "办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办"
                "查阅系统消息合成样本文本内容通知提"
            ),
            "content": (
                "办查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办"
            ),
            "source_name": "办查阅系统消息",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "2",
            "link": "/oa/system-messages/desktop/017xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/017xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000018",
            "title": "查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本",
            "content": (
                "查阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查"
            ),
            "source_name": "查阅系统消息合",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "3",
            "link": "/oa/system-messages/desktop/018xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/018xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000019",
            "title": "阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本",
            "content": (
                "阅系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅"
            ),
            "source_name": "阅系统消息合成",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "4",
            "link": "/oa/system-messages/desktop/019xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/019xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
        {
            "message_id": "90000020",
            "title": "系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文",
            "content": (
                "系统消息合成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提醒待办查阅系"
            ),
            "source_name": "系统消息合成样",
            "occurred_at": "2000-01-01 00:00:00",
            "business_state": "5",
            "link": "/oa/system-messages/desktop/020xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "mobile_link": (
                "/oa/system-messages/mobile/020xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            ),
        },
    ],
    "returned_count": 20,
    "is_complete": False,
}

EXPECTED_REPLAY_DATA: dict[str, Any] = {
    "workflows": [
        {
            "todo_id": "999991",
            "title": "统消息合成样本文本内容通知提醒待办查阅系统消",
            "status": "666",
            "received_at": "2000-01-01",
            "created_at": "2000-01-01",
            "workflow_type_id": "900",
        },
        {
            "todo_id": "999992",
            "title": "消息合成样本文本内容通知提醒待办查阅系统消息",
            "status": "777",
            "received_at": "2000-01-01",
            "created_at": "2000-01-01",
            "workflow_type_id": "900",
        },
        {
            "todo_id": "999993",
            "title": "息合成样本文本内容通知提醒待办查阅系统消息",
            "status": "888",
            "received_at": "2000-01-01",
            "created_at": "2000-01-01",
            "workflow_type_id": "900",
        },
        {
            "todo_id": "999994",
            "title": "合成样本文本内容通知提醒待办查阅系统消息合成",
            "status": "111",
            "received_at": "2000-01-01",
            "created_at": "2000-01-01",
            "workflow_type_id": "900",
        },
        {
            "todo_id": "999995",
            "title": "成样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知",
            "status": "222",
            "received_at": "2000-01-01",
            "created_at": "2000-01-01",
            "workflow_type_id": "9000",
        },
        {
            "todo_id": "999996",
            "title": "样本文本内容通知提醒待办查阅系统消息合成样本文本内容通知提",
            "status": "333",
            "received_at": "2000-01-01",
            "created_at": "2000-01-01",
            "workflow_type_id": "9000",
        },
    ],
    "returned_count": 6,
    "authoritative_count": 6,
    "is_complete": True,
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


class CountingSystemMessageReplayProvider(ReplayOAReadProvider):
    def __init__(self, contract_pack_dir: Path) -> None:
        super().__init__(contract_pack_dir)
        self.calls = 0

    async def list_system_messages(
        self,
        credential: OASessionCredential | None = None,
    ) -> OASystemMessageCollection:
        self.calls += 1
        return await super().list_system_messages(credential)


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
        self.request_forms: list[dict[str, list[str]]] = []
        self.request_paths: list[str] = []
        self.request_methods: list[str] = []
        self.request_header_names: list[frozenset[str]] = []
        self.request_headers: list[dict[str, str]] = []
        self.cookie_header_present: list[bool] = []

    def open(self, request: Request, *, timeout: float) -> FakeHTTPResponse:
        assert timeout > 0
        parsed_url = urlsplit(request.full_url)
        self.request_queries.append(parse_qs(parsed_url.query))
        self.request_paths.append(parsed_url.path)
        request_data = request.data
        self.request_forms.append(
            parse_qs(
                request_data.decode("ascii") if request_data is not None else "",
                keep_blank_values=True,
            )
        )
        self.request_methods.append(request.get_method())
        self.request_header_names.append(
            frozenset(name.casefold() for name, _value in request.header_items())
        )
        self.request_headers.append(
            {
                name.casefold(): value
                for name, value in request.header_items()
            }
        )
        self.cookie_header_present.append(request.has_header("Cookie"))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingHTTPServer(ThreadingHTTPServer):
    def __init__(self, *response_payloads: dict[str, Any]) -> None:
        super().__init__(("127.0.0.1", 0), RecordingHTTPRequestHandler)
        self.response_payloads = list(response_payloads)
        self.requests: list[tuple[str, str | None, bool]] = []


class RecordingHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._record_and_respond()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        self._record_and_respond()

    def _record_and_respond(self) -> None:
        server = cast(RecordingHTTPServer, self.server)
        server.requests.append(
            (
                self.path,
                self.headers.get("Host"),
                self.headers.get("Cookie") is not None,
            )
        )
        payload = json.dumps(server.response_payloads.pop(0)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _start_recording_server(
    *response_payloads: dict[str, Any],
) -> tuple[RecordingHTTPServer, threading.Thread]:
    server = RecordingHTTPServer(*response_payloads)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _assert_browser_like_oa_headers(
    opener: SequencedOpener,
    *,
    request_count: int,
) -> None:
    assert len(opener.request_headers) == request_count
    for headers in opener.request_headers:
        assert headers["accept"] == "*/*"
        assert headers["accept-language"] == "zh-CN,zh;q=0.9"
        assert headers["referer"] == (
            "https://oa.synthetic.invalid/wui/index.html"
        )
        assert headers["user-agent"] == (
            "Mozilla/5.0 (Windows NT 6.1; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/86.0.4240.111 Safari/537.36"
        )


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
    page_size: int = 20,
    max_pages: int = 50,
    max_records: int = 5_000,
    system_category_id: str = "202",
) -> LiveOAReadProvider:
    return LiveOAReadProvider(
        base_url="https://oa.synthetic.invalid",
        message_center_endpoint_path="/api/messages",
        pending_workflows_split_page_key_path="/api/todo/splitPageKey",
        pending_workflows_counts_path="/api/todo/counts",
        pending_workflows_datas_path="/api/todo/datas",
        pending_workflows_actiontype="todo-action",
        pending_workflows_hide_no_data_tab="todo-hide",
        pending_workflows_method="todo-method",
        pending_workflows_offical_type="todo-offical-type",
        pending_workflows_view_scope="todo-view-scope",
        pending_workflows_sort_params="todo-sort",
        system_messages_category_id=system_category_id,
        system_messages_bizstate="system-business-state",
        system_messages_select_state="system-selection-state",
        timeout_seconds=2.0,
        pending_workflows_contract_pack_dir=CONTRACT_PACK,
        system_messages_contract_pack_dir=SYSTEM_MESSAGE_CONTRACT_PACK,
        drift_reporter=drift_reporter,
        page_size=page_size,
        max_pages=max_pages,
        max_records=max_records,
        opener_factory=lambda: cast(OpenerDirector, opener),
    )


def _raw_workflow(index: int) -> dict[str, Any]:
    return {
        "requestid": f"live-workflow-{index}",
        "requestname": f"live-title-{index}",
        "status": "1",
        "receivedate": "2026-07-30",
        "createdate": "2026-07-29",
        "workflowid": f"live-workflow-type-{index}",
    }


def _matching_live_workflows() -> list[dict[str, Any]]:
    first = _raw_workflow(1)
    second = _raw_workflow(2)
    return [first, second]


def _session_key() -> str:
    sessionkey = secrets.token_hex(35)[:69]
    assert len(sessionkey) == 69
    return sessionkey


def _pending_split_payload(sessionkey: str) -> dict[str, Any]:
    return {"sessionkey": sessionkey}


def _pending_counts_payload(
    authoritative_count: Any,
    *,
    status: Any = True,
) -> dict[str, Any]:
    return {"count": authoritative_count, "status": status}


def _pending_datas_payload(
    records: Any,
    *,
    status: Any = True,
    page_size: Any = "20",
) -> dict[str, Any]:
    return {"datas": records, "pageSize": page_size, "status": status}


def _expected_pending_split_form() -> dict[str, list[str]]:
    return {
        "method": ["todo-method"],
        "offical": [""],
        "officalType": ["todo-offical-type"],
        "hideNoDataTab": ["todo-hide"],
        "viewScope": ["todo-view-scope"],
        "complete": ["0"],
        "viewcondition": ["5"],
        "defaultTabVal": ["0"],
        "requestname": [""],
        "wfcode": [""],
        "workflowid": [""],
        "createdateselect": ["0"],
        "createdatefrom": [""],
        "createdateto": [""],
        "creatertype": ["0"],
        "workcode": [""],
        "doingStatus": ["0"],
        "ownerdepartmentid": [""],
        "creatersubcompanyid": [""],
        "workflowtype": [""],
        "requestlevel": [""],
        "recievedateselect": ["0"],
        "recievedatefrom": [""],
        "recievedateto": [""],
        "wfstatu": ["1"],
        "nodetype": [""],
        "unophrmid": [""],
        "docids": [""],
        "hrmcreaterid": [""],
        "crmids": [""],
        "proids": [""],
        "menuIds": ["1,13"],
        "menuPathIds": ["1,13"],
        "actiontype": ["todo-action"],
    }


def _pending_opener(
    records: list[dict[str, Any]],
    *,
    authoritative_count: int | None = None,
    sessionkey: str | None = None,
) -> tuple[SequencedOpener, str]:
    generated_sessionkey = sessionkey or _session_key()
    count = len(records) if authoritative_count is None else authoritative_count
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(generated_sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(count)),
        FakeHTTPResponse(_pending_datas_payload(records)),
    )
    return opener, generated_sessionkey


def _raw_system_message(index: int) -> dict[str, Any]:
    return {
        "messageid": f"live-message-{index}",
        "title": f"live-title-{index}",
        "context": f"live-content-{index}",
        "name": f"live-source-{index}",
        "time": "2026-08-03 12:00:00",
        "bizstate": "1",
        "link": f"/messages/desktop/{index}",
        "linkmobileurl": f"/messages/mobile/{index}",
        "gomethod": "synthetic-desktop-method",
        "gomethodpc": "synthetic-mobile-method",
        "showimage": "synthetic-image-flag",
    }


def _matching_live_system_messages() -> list[dict[str, Any]]:
    first = _raw_system_message(1)
    first["link"] = "https://oa.synthetic.invalid/messages/desktop/1"
    first["linkmobileurl"] = (
        "https://oa.synthetic.invalid/messages/mobile/1"
    )
    second = _raw_system_message(2)
    second["link"] = ""
    second["linkmobileurl"] = ""
    return [first, second]


def _assert_anonymous_node_paths(
    nodes: tuple[Any, ...] | list[dict[str, Any]],
    *,
    root_path: str,
    expected_count: int,
) -> list[str]:
    paths = [
        node.path if hasattr(node, "path") else node["path"]
        for node in nodes
    ]
    pattern = re.compile(
        rf"{re.escape(root_path)}\.unknown_field_[a-f0-9]{{64}}$"
    )
    assert len(paths) == expected_count
    assert len(set(paths)) == expected_count
    assert all(pattern.fullmatch(path) for path in paths)
    return paths


def _assert_wire_node_paths(
    nodes: tuple[Any, ...] | list[dict[str, Any]],
    *,
    root_path: str,
    raw_keys: set[str],
) -> None:
    paths = {
        node.path if hasattr(node, "path") else node["path"]
        for node in nodes
    }
    assert paths == {f"{root_path}.wire_{key}" for key in raw_keys}


def _message_center_page(
    records: list[dict[str, Any]],
    *,
    cursor_index: int = 1,
    status: str = "1",
) -> dict[str, Any]:
    return {
        "status": status,
        "data": records,
        "maxtime": f"synthetic-upper-{cursor_index}",
        "mintime": f"synthetic-lower-{cursor_index}",
        "msgid": f"synthetic-cursor-{cursor_index}",
    }


def test_published_pending_pack_versions_stay_immutable_and_distinct() -> None:
    frozen_bytes = {
        "ecology9-pending-workflows-v1/fingerprint.json": (
            "26aa1d354cb8c6056587bf7fcccd305139059796ed9b0ed2c26927c6c81137ef"
        ),
        "ecology9-pending-workflows-v1/profile.json": (
            "605673383921f3f65b296b25041be93963c20dcdaf79059f16cd2ad898d1774c"
        ),
        "ecology9-pending-workflows-v1/sample.json": (
            "83543add6d8cc6d642638f6147c92bef8fd6a2419a5c4f91ed1225ebfe6fb252"
        ),
        "ecology9-pending-workflows-v2/fingerprint.json": (
            "86c5bd727f29111a1baa4e4c21f3cec7eb873331d6c4fabf7b20c9b664084dfa"
        ),
        "ecology9-pending-workflows-v2/profile.json": (
            "1088ab942c4c204deb15e92b89ae17e6a150a2347a9f199d0dff89d6df1edef6"
        ),
        "ecology9-pending-workflows-v2/sample.json": (
            "eac04435b6b552924ef0b0dc05bee8b6e5889801ff03a08f97219bfecb6dd740"
        ),
    }
    for relative_path, expected_sha256 in frozen_bytes.items():
        artifact = CONTRACT_PACK.parent / relative_path
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected_sha256
    assert json.loads(
        (LEGACY_PENDING_V2_CONTRACT_PACK / "sample.json").read_text(
            encoding="utf-8"
        )
    ) == EXPECTED_LEGACY_V2_DATA

    current_profile = json.loads(
        (CONTRACT_PACK / "profile.json").read_text(encoding="utf-8")
    )
    current_fingerprint = json.loads(
        (CONTRACT_PACK / "fingerprint.json").read_text(encoding="utf-8")
    )
    assert CONTRACT_PACK.name == "ecology9-pending-workflows-v3"
    assert current_profile == {
        "profile_version": CONTRACT_PACK.name,
        "capability_id": "oa.list_pending_workflows",
        "source_kind": "sanitized_capture",
        "sanitizer_version": "2",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
    }
    assert current_fingerprint == build_structural_fingerprint(
        json.loads((CONTRACT_PACK / "sample.json").read_text(encoding="utf-8"))
    )
    with pytest.raises(OAContractPackError):
        asyncio.run(
            ReplayOAReadProvider(LEGACY_PENDING_V1_CONTRACT_PACK).list_pending_workflows(
                _credential()
            )
        )


def test_message_center_record_excludes_disproved_pending_fields() -> None:
    disproved_fields = {
        "workflow_id",
        "applicant",
        "approver",
        "created_at",
        "current_step",
        "expired",
        "status",
    }

    assert disproved_fields.isdisjoint(OAMessageCenterRecord.model_fields)


def _message_center_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "message_id": "90000001",
        "title": "synthetic-title",
        "content": "synthetic-content",
        "source_name": "synthetic-source",
        "occurred_at": "2000-01-01 00:00:00",
        "business_state": "2",
        "link": "/oa/system-messages/desktop/001",
        "mobile_link": "/oa/system-messages/mobile/001",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize("field_name", ["link", "mobile_link"])
def test_message_center_record_rejects_protocol_relative_links(
    field_name: str,
) -> None:
    # "//evil.example/steal" starts with "/" but is protocol-relative: rendered
    # as an href it is a cross-origin jump, not a same-origin path.
    with pytest.raises(ValidationError, match="null or relative paths"):
        OAMessageCenterRecord.model_validate(
            _message_center_record(**{field_name: "//evil.example/steal"}),
            strict=True,
        )


@pytest.mark.parametrize("accepted_link", ["/ok/path", None])
def test_message_center_record_still_accepts_same_origin_and_absent_links(
    accepted_link: str | None,
) -> None:
    record = OAMessageCenterRecord.model_validate(
        _message_center_record(link=accepted_link),
        strict=True,
    )

    assert record.link == accepted_link
    assert record.mobile_link == "/oa/system-messages/mobile/001"


def test_message_center_ignored_wire_fields_are_exactly_enumerated() -> None:
    assert _LIVE_MESSAGE_CENTER_IGNORED_WIRE_FIELDS == frozenset(
        {"gomethod", "gomethodpc", "showimage"}
    )


def test_pending_and_system_fingerprints_use_distinct_exact_field_mappings() -> None:
    pending_mapping = build_live_pending_workflows_fingerprint.__globals__[
        "_LIVE_PENDING_WORKFLOW_FIELD_NAMES"
    ]
    system_mapping = build_live_system_messages_fingerprint.__globals__[
        "_LIVE_MESSAGE_CENTER_FIELD_NAMES"
    ]

    assert pending_mapping is _LIVE_PENDING_WORKFLOW_FIELD_NAMES
    assert system_mapping is _LIVE_MESSAGE_CENTER_FIELD_NAMES
    assert pending_mapping is not system_mapping
    assert pending_mapping == {
        "requestid": "todo_id",
        "requestname": "title",
        "status": "status",
        "receivedate": "received_at",
        "createdate": "created_at",
        "workflowid": "workflow_type_id",
    }


def test_pending_collection_rejects_mismatched_returned_count() -> None:
    with pytest.raises(ValueError, match="returned_count"):
        OAPendingWorkflowCollection.model_validate(
            {
                "workflows": [],
                "returned_count": 1,
                "authoritative_count": 0,
                "is_complete": True,
            },
            strict=True,
        )


def test_pending_collection_rejects_mismatched_authoritative_count() -> None:
    with pytest.raises(ValueError, match="authoritative_count"):
        OAPendingWorkflowCollection.model_validate(
            {
                "workflows": [],
                "returned_count": 0,
                "authoritative_count": 1,
                "is_complete": True,
            },
            strict=True,
        )


def test_pending_collection_rejects_duplicate_todo_ids() -> None:
    workflow = EXPECTED_REPLAY_DATA["workflows"][0]

    with pytest.raises(ValueError, match="todo_id"):
        OAPendingWorkflowCollection.model_validate(
            {
                "workflows": [workflow, dict(workflow)],
                "returned_count": 2,
                "authoritative_count": 2,
                "is_complete": True,
            },
            strict=True,
        )


def test_frozen_system_pack_fingerprint_remains_pack_self_consistent() -> None:
    sample = json.loads(
        (SYSTEM_MESSAGE_CONTRACT_PACK / "sample.json").read_text(encoding="utf-8")
    )
    frozen = json.loads(
        (SYSTEM_MESSAGE_CONTRACT_PACK / "fingerprint.json").read_text(
            encoding="utf-8"
        )
    )

    rebuilt = build_structural_fingerprint(sample)

    assert rebuilt["sha256"] == frozen["sha256"]
    assert rebuilt == frozen


def test_system_pack_drift_baseline_excludes_exemplar_only_nullable() -> None:
    sample = json.loads(
        (SYSTEM_MESSAGE_CONTRACT_PACK / "sample.json").read_text(encoding="utf-8")
    )

    baseline = build_contract_drift_baseline_fingerprint(sample)
    nodes = {node["path"]: node for node in baseline["nodes"]}

    assert nodes["$.messages[].link"]["nullable"] is False
    assert nodes["$.messages[].mobile_link"]["nullable"] is False


def test_live_system_shape_matches_data_derived_contract_baseline() -> None:
    sample = json.loads(
        (SYSTEM_MESSAGE_CONTRACT_PACK / "sample.json").read_text(encoding="utf-8")
    )
    baseline = build_contract_drift_baseline_fingerprint(sample)
    actual = build_live_system_messages_fingerprint(
        _matching_live_system_messages()
    )

    report = compare_structural_fingerprints(baseline, actual)

    assert report.matches is True
    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_live_pending_provider_keeps_rejecting_backslash_endpoint_path() -> None:
    with pytest.raises(ValueError, match="endpoint path"):
        LiveOAReadProvider(
            base_url="https://oa.synthetic.invalid",
            message_center_endpoint_path=r"/api\messages",
            pending_workflows_split_page_key_path="/api/todo/splitPageKey",
            pending_workflows_counts_path="/api/todo/counts",
            pending_workflows_datas_path="/api/todo/datas",
            pending_workflows_actiontype="todo-action",
            pending_workflows_hide_no_data_tab="todo-hide",
            pending_workflows_method="todo-method",
            pending_workflows_offical_type="todo-offical-type",
            pending_workflows_view_scope="todo-view-scope",
            pending_workflows_sort_params="todo-sort",
            system_messages_category_id="202",
            system_messages_bizstate="system-business-state",
            system_messages_select_state="system-selection-state",
            timeout_seconds=2.0,
            pending_workflows_contract_pack_dir=CONTRACT_PACK,
            system_messages_contract_pack_dir=SYSTEM_MESSAGE_CONTRACT_PACK,
        )


def test_live_system_message_provider_rejects_pending_pack() -> None:
    with pytest.raises(
        OAContractPackPayloadInvalid,
        match="system-message provider requires its matching Contract Pack",
    ):
        LiveOAReadProvider(
            base_url="https://oa.synthetic.invalid",
            message_center_endpoint_path="/api/messages",
            pending_workflows_split_page_key_path="/api/todo/splitPageKey",
            pending_workflows_counts_path="/api/todo/counts",
            pending_workflows_datas_path="/api/todo/datas",
            pending_workflows_actiontype="todo-action",
            pending_workflows_hide_no_data_tab="todo-hide",
            pending_workflows_method="todo-method",
            pending_workflows_offical_type="todo-offical-type",
            pending_workflows_view_scope="todo-view-scope",
            pending_workflows_sort_params="todo-sort",
            system_messages_category_id="202",
            system_messages_bizstate="system-business-state",
            system_messages_select_state="system-selection-state",
            timeout_seconds=2.0,
            pending_workflows_contract_pack_dir=CONTRACT_PACK,
            system_messages_contract_pack_dir=CONTRACT_PACK,
        )


def test_live_system_messages_use_bounded_post_and_match_contract() -> None:
    reports: list[Any] = []
    records = _matching_live_system_messages()
    opener = SequencedOpener(
        FakeHTTPResponse(
            {
                "status": "1",
                "data": records,
                "maxtime": "synthetic-upper-bound",
                "mintime": "synthetic-lower-bound",
                "msgid": "synthetic-message-cursor",
            }
        ),
        FakeHTTPResponse(_message_center_page([], cursor_index=2)),
    )
    adapter = OAReadAdapter(
        _live_provider(opener, drift_reporter=reports.append),
        secret_provider=StaticSecretProvider(_credential()),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_system_messages",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "success"
    assert result.error_code is None
    assert result.data == {
        "messages": [
            {
                "message_id": "live-message-1",
                "title": "live-title-1",
                "content": "live-content-1",
                "source_name": "live-source-1",
                "occurred_at": "2026-08-03 12:00:00",
                "business_state": "1",
                "link": "/messages/desktop/1",
                "mobile_link": "/messages/mobile/1",
            },
            {
                "message_id": "live-message-2",
                "title": "live-title-2",
                "content": "live-content-2",
                "source_name": "live-source-2",
                "occurred_at": "2026-08-03 12:00:00",
                "business_state": "1",
                "link": None,
                "mobile_link": None,
            },
        ],
        "returned_count": 2,
        "is_complete": True,
    }
    assert opener.request_methods == ["POST", "POST"]
    assert opener.request_queries == [{}, {}]
    assert opener.request_forms == [
        {
            "pagesize": ["20"],
            "selectState": ["system-selection-state"],
            "msgid": ["0"],
            "bizstate": ["system-business-state"],
            "mintime": ["0"],
            "id": ["202"],
        },
        {
            "pagesize": ["20"],
            "selectState": ["system-selection-state"],
            "msgid": ["synthetic-message-cursor"],
            "bizstate": ["system-business-state"],
            "mintime": ["synthetic-lower-bound"],
            "id": ["202"],
        },
    ]
    assert opener.cookie_header_present == [True, True]
    assert {
        "accept",
        "accept-language",
        "content-type",
        "cookie",
        "origin",
        "referer",
        "user-agent",
        "x-requested-with",
    }.issubset(opener.request_header_names[0])
    _assert_browser_like_oa_headers(opener, request_count=2)
    assert len(reports) == 1
    report = reports[0]
    expected_fingerprint = build_contract_drift_baseline_fingerprint(
        json.loads(
            (SYSTEM_MESSAGE_CONTRACT_PACK / "sample.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert report.matches is True
    assert report.expected_sha256 == expected_fingerprint["sha256"]
    assert report.actual_sha256 == expected_fingerprint["sha256"]
    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_live_system_message_full_page_at_page_limit_fails_closed() -> None:
    records = [_raw_system_message(index) for index in range(1, 21)]
    opener = SequencedOpener(
        FakeHTTPResponse(_message_center_page(records))
    )

    with pytest.raises(OALivePayloadInvalid, match="may be truncated"):
        asyncio.run(
            _live_provider(opener, max_pages=1).list_system_messages(_credential())
        )

    assert len(opener.request_forms) == 1


def test_live_system_messages_fetches_all_three_cursor_pages() -> None:
    opener = SequencedOpener(
        FakeHTTPResponse(
            _message_center_page(
                [_raw_system_message(index) for index in range(1, 21)]
            )
        ),
        FakeHTTPResponse(
            _message_center_page(
                [_raw_system_message(index) for index in range(21, 41)],
                cursor_index=2,
            )
        ),
        FakeHTTPResponse(
            _message_center_page([_raw_system_message(41)], cursor_index=3)
        ),
        FakeHTTPResponse(_message_center_page([], cursor_index=4)),
    )

    collection = asyncio.run(
        _live_provider(opener).list_system_messages(_credential())
    )

    assert collection.returned_count == 41
    assert collection.is_complete is True
    assert [message.message_id for message in collection.messages] == [
        f"live-message-{index}" for index in range(1, 42)
    ]
    assert [form["id"] for form in opener.request_forms] == [
        ["202"],
        ["202"],
        ["202"],
        ["202"],
    ]
    assert [form["msgid"] for form in opener.request_forms] == [
        ["0"],
        ["synthetic-cursor-1"],
        ["synthetic-cursor-2"],
        ["synthetic-cursor-3"],
    ]
    assert [form["mintime"] for form in opener.request_forms] == [
        ["0"],
        ["synthetic-lower-1"],
        ["synthetic-lower-2"],
        ["synthetic-lower-3"],
    ]
    assert len(opener.request_forms) == 4


@pytest.mark.parametrize(
    "payload",
    [
        _message_center_page([], status="0"),
        _message_center_page(
            [_raw_system_message(index) for index in range(1, 22)]
        ),
        _message_center_page(
            [
                {
                    **_raw_system_message(1),
                    "link": "https://other.synthetic.invalid/messages/1",
                }
            ]
        ),
    ],
)
def test_live_system_message_invalid_status_bounds_and_links_fail_closed(
    payload: dict[str, Any],
) -> None:
    opener = SequencedOpener(
        FakeHTTPResponse(payload),
        FakeHTTPResponse(_message_center_page([], cursor_index=2)),
    )
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential()),
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_system_messages",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


def test_live_system_message_drift_reports_safe_wire_names_without_values() -> None:
    reports: list[Any] = []
    records = _matching_live_system_messages()
    runtime_field_name = f"vendorField{secrets.token_hex(8)}"
    runtime_business_value = f"business-{secrets.token_hex(24)}"
    records[0][runtime_field_name] = runtime_business_value
    opener = SequencedOpener(
        FakeHTTPResponse(_message_center_page(records)),
        FakeHTTPResponse(_message_center_page([], cursor_index=2)),
    )

    collection = asyncio.run(
        _live_provider(
            opener,
            drift_reporter=reports.append,
        ).list_system_messages(_credential())
    )

    assert collection.returned_count == 2
    assert len(reports) == 1
    report = reports[0]
    rendered = report.model_dump_json()
    assert report.matches is False
    _assert_wire_node_paths(
        report.added,
        root_path="$.messages[]",
        raw_keys={runtime_field_name},
    )
    assert f"wire_{runtime_field_name}" in rendered
    assert runtime_business_value not in rendered


def test_live_system_message_error_discards_sensitive_traceback_locals() -> None:
    marker = secrets.token_hex(32)
    provider = _live_provider(SequencedOpener(TimeoutError(marker)))

    with pytest.raises(OALiveTimeout) as exc_info:
        asyncio.run(
            provider.list_system_messages(_credential(cookie_value=marker))
        )

    assert marker not in str(exc_info.value)
    _assert_provider_traceback_is_redacted(exc_info.value, marker)


def test_live_system_message_fingerprint_ignores_only_enumerated_navigation_fields(
) -> None:
    expected = build_contract_drift_baseline_fingerprint(
        json.loads(
            (SYSTEM_MESSAGE_CONTRACT_PACK / "sample.json").read_text(
                encoding="utf-8"
            )
        )
    )

    report = compare_structural_fingerprints(
        expected,
        build_live_system_messages_fingerprint(
            _matching_live_system_messages()
        ),
    )

    assert report.matches is True
    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()
    assert all(node.nullable is True for node in report.changed_expected)
    assert all(node.nullable is False for node in report.changed)


def test_empty_live_aggregates_do_not_inject_contract_record_fields() -> None:
    for fingerprint, root_path in (
        (build_live_pending_workflows_fingerprint([]), "$.workflows[]"),
        (build_live_system_messages_fingerprint([]), "$.messages[]"),
    ):
        assert all(
            not node["path"].startswith(root_path)
            for node in fingerprint["nodes"]
        )


def test_live_provider_source_has_no_hardcoded_transport_secret_literal() -> None:
    provider_file = oa_provider.__file__
    assert provider_file is not None
    source = Path(provider_file).read_text(encoding="utf-8")
    tree = ast.parse(source)
    string_literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    secret_shape = re.compile(
        r"(?i)(?:cookie|session(?:id)?|token)\s*[:=]\s*[^\s\"']{9,}"
    )

    assert [
        literal for literal in string_literals if secret_shape.search(literal)
    ] == []
    assert "Live system-message reads are not configured" not in source


def test_live_default_opener_ignores_all_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessionkey = _session_key()
    oa_server, oa_thread = _start_recording_server(
        _pending_split_payload(sessionkey),
        _pending_counts_payload(1),
        _pending_datas_payload([_raw_workflow(1)]),
    )
    proxy_server, proxy_thread = _start_recording_server(
        _message_center_page([_raw_workflow(99)])
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
        message_center_endpoint_path="/api/messages",
        pending_workflows_split_page_key_path="/api/todo/splitPageKey",
        pending_workflows_counts_path="/api/todo/counts",
        pending_workflows_datas_path="/api/todo/datas",
        pending_workflows_actiontype="todo-action",
        pending_workflows_hide_no_data_tab="todo-hide",
        pending_workflows_method="todo-method",
        pending_workflows_offical_type="todo-offical-type",
        pending_workflows_view_scope="todo-view-scope",
        pending_workflows_sort_params="todo-sort",
        system_messages_category_id="202",
        system_messages_bizstate="system-business-state",
        system_messages_select_state="system-selection-state",
        timeout_seconds=2.0,
        pending_workflows_contract_pack_dir=CONTRACT_PACK,
        system_messages_contract_pack_dir=SYSTEM_MESSAGE_CONTRACT_PACK,
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

    assert [workflow.todo_id for workflow in collection.workflows] == [
        "live-workflow-1"
    ]
    assert proxy_server.requests == []
    assert len(oa_server.requests) == 3
    request_path, request_host, cookie_present = oa_server.requests[0]
    assert request_host == oa_host
    assert request_path == "/api/todo/splitPageKey"
    assert cookie_present is True
    assert [request[0] for request in oa_server.requests] == [
        "/api/todo/splitPageKey",
        "/api/todo/counts",
        "/api/todo/datas",
    ]


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
    assert result.trace_metadata is not None
    assert result.trace_metadata.argument_keys == ()
    assert result.trace_metadata.failure_stage is None


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


def test_system_message_replay_runs_through_gateway_without_silent_truncation() -> None:
    gateway = CapabilityGateway(
        OAReadAdapter(ReplayOAReadProvider(SYSTEM_MESSAGE_CONTRACT_PACK))
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-system-message-001",
            "session-system-message-001",
            "ai-user-system-message-001",
            "oa.list_system_messages",
            {},
            RequestOrgContext(request_id="trace-system-message-001"),
        )
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.data is not None
    assert set(result.data) == {"messages", "returned_count", "is_complete"}
    messages = result.data["messages"]
    assert isinstance(messages, list)
    assert result.data["returned_count"] == 20
    assert result.data["returned_count"] == len(messages)
    assert result.data["is_complete"] is False
    assert messages[0]["message_id"] == "90000001"
    assert messages[-1]["message_id"] == "90000020"
    assert all(
        set(message)
        == {
            "message_id",
            "title",
            "content",
            "source_name",
            "occurred_at",
            "business_state",
            "link",
            "mobile_link",
        }
        for message in messages
    )


def test_system_message_capability_is_zero_argument() -> None:
    provider = CountingSystemMessageReplayProvider(SYSTEM_MESSAGE_CONTRACT_PACK)
    adapter = OAReadAdapter(provider)

    result = asyncio.run(
        adapter.execute(
            "oa.list_system_messages",
            {"page_size": 20},
            {},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None
    assert provider.calls == 0


def test_system_message_pack_requires_external_source_warning(
    tmp_path: Path,
) -> None:
    pack = tmp_path / SYSTEM_MESSAGE_CONTRACT_PACK.name
    shutil.copytree(SYSTEM_MESSAGE_CONTRACT_PACK, pack)
    profile_path = pack / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile.pop("source_warning")
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    adapter = OAReadAdapter(ReplayOAReadProvider(pack))

    result = asyncio.run(adapter.execute("oa.list_system_messages", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None


def test_system_message_call_rejects_pending_workflow_pack() -> None:
    adapter = OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK))

    result = asyncio.run(adapter.execute("oa.list_system_messages", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


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
    assert direct_result.trace_metadata is not None
    assert direct_result.trace_metadata.failure_stage == "unknown"
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
    assert result.trace_metadata is not None
    assert result.trace_metadata.argument_keys == ("page_size",)
    assert result.trace_metadata.failure_stage == "argument_validation"
    assert provider.calls == 0


@pytest.mark.parametrize(
    "capability_id",
    ["oa.list_pending_workflows", "oa.list_system_messages"],
)
def test_live_adapter_requires_server_issued_credential_reference(
    capability_id: str,
) -> None:
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
    secret_provider = StaticSecretProvider(_credential())
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=secret_provider,
    )

    result = asyncio.run(adapter.execute(capability_id, {}, {}))

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
    sample["workflows"][0]["todo_id"] = 1
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
    first = {
        "workflows": [copy.deepcopy(EXPECTED_REPLAY_DATA["workflows"][0])],
        "returned_count": 1,
        "authoritative_count": 1,
        "is_complete": True,
    }
    second_item = copy.deepcopy(first["workflows"][0])
    second_item.update(
        {
            "todo_id": "completely-different-workflow",
            "title": "completely-different-title",
            "status": "different-state",
            "received_at": "2099-12-31",
            "created_at": "2099-12-30",
            "workflow_type_id": "different-workflow-type",
        }
    )
    second = {
        "workflows": [second_item, copy.deepcopy(second_item)],
        "returned_count": 2,
        "authoritative_count": 2,
        "is_complete": True,
    }
    empty = {
        "workflows": [],
        "returned_count": 0,
        "authoritative_count": 0,
        "is_complete": True,
    }

    first_fingerprint = build_structural_fingerprint(first)
    second_fingerprint = build_structural_fingerprint(second)
    empty_fingerprint = build_structural_fingerprint(empty)

    assert first_fingerprint == second_fingerprint
    assert first_fingerprint == empty_fingerprint
    rendered = json.dumps(first_fingerprint)
    for business_value in first["workflows"][0].values():
        if business_value is not None and len(str(business_value)) >= 9:
            assert str(business_value) not in rendered


def test_replay_accepts_legal_empty_workflow_collection(tmp_path: Path) -> None:
    pack = tmp_path / CONTRACT_PACK.name
    shutil.copytree(CONTRACT_PACK, pack)
    empty_sample = {
        "workflows": [],
        "returned_count": 0,
        "authoritative_count": 0,
        "is_complete": True,
    }
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
    assert result.data == empty_sample


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
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(2)),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(1)], page_size="1")),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(2)], page_size="1")),
    )
    secret_provider = StaticSecretProvider(_credential())
    adapter = OAReadAdapter(
        _live_provider(opener, page_size=1),
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
                "todo_id": "live-workflow-1",
                "title": "live-title-1",
                "status": "1",
                "received_at": "2026-07-30",
                "created_at": "2026-07-29",
                "workflow_type_id": "live-workflow-type-1",
            },
            {
                "todo_id": "live-workflow-2",
                "title": "live-title-2",
                "status": "1",
                "received_at": "2026-07-30",
                "created_at": "2026-07-29",
                "workflow_type_id": "live-workflow-type-2",
            },
        ],
        "returned_count": 2,
        "authoritative_count": 2,
        "is_complete": True,
    }
    assert opener.request_forms == [
        _expected_pending_split_form(),
        {"dataKey": [sessionkey]},
        {"current": ["1"], "dataKey": [sessionkey], "sortParams": ["todo-sort"]},
        {"current": ["2"], "dataKey": [sessionkey], "sortParams": ["todo-sort"]},
    ]
    assert len(_expected_pending_split_form()) == 34
    assert opener.request_paths == [
        "/api/todo/splitPageKey",
        "/api/todo/counts",
        "/api/todo/datas",
        "/api/todo/datas",
    ]
    assert all(
        "pagesize" not in form and "pageSize" not in form
        for form in opener.request_forms
    )
    assert "/api/messages" not in opener.request_paths
    assert opener.cookie_header_present == [True, True, True, True]
    _assert_browser_like_oa_headers(opener, request_count=4)
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
            target_system: str,
        ) -> OASessionCredential | None:
            assert target_system == "oa"
            assert loaded_ai_user_id == trusted_ai_user_id
            return credential

    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(1)),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(1)])),
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
    rendered = rendered_trace + repr(result) + caplog.text
    assert marker not in rendered
    assert sessionkey not in rendered
    assert credential_ref not in rendered_trace


def test_live_provider_accepts_a_legal_empty_collection() -> None:
    opener, sessionkey = _pending_opener([], authoritative_count=0)
    collection = asyncio.run(
        _live_provider(opener).list_pending_workflows(_credential())
    )

    assert collection.model_dump(mode="json") == {
        "workflows": [],
        "returned_count": 0,
        "authoritative_count": 0,
        "is_complete": True,
    }
    assert opener.request_paths == [
        "/api/todo/splitPageKey",
        "/api/todo/counts",
        "/api/todo/datas",
    ]
    assert opener.request_forms[1:] == [
        {"dataKey": [sessionkey]},
        {"current": ["1"], "dataKey": [sessionkey], "sortParams": ["todo-sort"]},
    ]


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
                {"sessionkey": []}
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
        sessionkey = _session_key()
        opener = SequencedOpener(
            FakeHTTPResponse(_pending_split_payload(sessionkey))
        )

        def explode_payload(_payload: Any) -> Any:
            raise RuntimeError(marker)

        monkeypatch.setattr(
            oa_provider,
            "_read_pending_workflow_sessionkey",
            explode_payload,
        )
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


def test_live_pending_fails_closed_when_count_exceeds_records_at_page_limit() -> None:
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(3)),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(1)], page_size="1")),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(2)], page_size="1")),
    )
    adapter = OAReadAdapter(
        _live_provider(opener, max_pages=2),
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
    assert len(opener.request_forms) == 4
    assert opener.request_paths[-2:] == ["/api/todo/datas", "/api/todo/datas"]


def test_live_pending_fails_closed_on_early_empty_page() -> None:
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(2)),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(1)], page_size="1")),
        FakeHTTPResponse(_pending_datas_payload([], page_size="1")),
    )
    adapter = OAReadAdapter(
        _live_provider(opener, page_size=1),
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
    assert result.data is None
    assert len(opener.request_forms) == 4


@pytest.mark.parametrize(
    "failure_case",
    [
        "split_shape",
        "split_type",
        "counts_shape",
        "counts_status",
        "counts_type",
        "datas_shape",
        "datas_status",
        "datas_type",
        "datas_page_size_type",
    ],
)
def test_live_pending_split_counts_and_datas_fail_closed_on_invalid_envelopes(
    failure_case: str,
) -> None:
    sessionkey = _session_key()
    responses: list[FakeHTTPResponse] = []
    if failure_case == "split_shape":
        responses.append(FakeHTTPResponse({}))
    elif failure_case == "split_type":
        responses.append(FakeHTTPResponse({"sessionkey": 69}))
    else:
        responses.append(FakeHTTPResponse(_pending_split_payload(sessionkey)))
        if failure_case == "counts_shape":
            responses.append(FakeHTTPResponse({"status": True}))
        elif failure_case == "counts_status":
            responses.append(FakeHTTPResponse(_pending_counts_payload(1, status=False)))
        elif failure_case == "counts_type":
            responses.append(FakeHTTPResponse(_pending_counts_payload("1")))
        else:
            responses.append(FakeHTTPResponse(_pending_counts_payload(1)))
            if failure_case == "datas_shape":
                responses.append(FakeHTTPResponse({"pageSize": "20", "status": True}))
            elif failure_case == "datas_status":
                responses.append(
                    FakeHTTPResponse(
                        _pending_datas_payload([_raw_workflow(1)], status=False)
                    )
                )
            elif failure_case == "datas_type":
                responses.append(FakeHTTPResponse(_pending_datas_payload({})))
            else:
                responses.append(
                    FakeHTTPResponse(
                        _pending_datas_payload([_raw_workflow(1)], page_size=20)
                    )
                )
    opener = SequencedOpener(*responses)
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
    assert len(opener.request_forms) == len(responses)


def test_live_pending_payload_rejection_never_logs_business_or_session_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = secrets.token_hex(32)
    sessionkey = _session_key()
    record = _raw_workflow(1)
    record["requestname"] = marker
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(1)),
        FakeHTTPResponse(_pending_datas_payload([record], page_size="0")),
    )
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=StaticSecretProvider(_credential(cookie_value=marker)),
    )
    caplog.set_level(logging.WARNING, logger=oa_provider.__name__)

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    rendered = caplog.text + repr(result)
    assert marker not in rendered
    assert sessionkey not in rendered


def test_live_pending_fails_closed_when_sessionkey_is_not_verbatim_safe() -> None:
    sessionkey = _session_key()
    unsafe_sessionkey = f" {sessionkey[:-1]}"
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(unsafe_sessionkey))
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
    assert result.error_code == "adapter_payload_invalid"
    assert len(opener.request_forms) == 1
    assert unsafe_sessionkey not in repr(result)


def test_live_pending_fails_before_datas_when_authoritative_count_exceeds_limit() -> None:
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(2)),
    )
    adapter = OAReadAdapter(
        _live_provider(opener, max_records=1),
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
    assert len(opener.request_forms) == 2
    assert opener.request_paths == [
        "/api/todo/splitPageKey",
        "/api/todo/counts",
    ]


def test_live_pending_fails_closed_on_cross_page_duplicate() -> None:
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(2)),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(1)], page_size="1")),
        FakeHTTPResponse(_pending_datas_payload([_raw_workflow(1)], page_size="1")),
    )
    adapter = OAReadAdapter(
        _live_provider(opener, page_size=1),
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
    assert len(opener.request_forms) == 4


def test_live_pending_single_result_uses_exact_three_step_protocol() -> None:
    record = _raw_workflow(1)
    opener, sessionkey = _pending_opener([record])

    collection = asyncio.run(
        _live_provider(opener).list_pending_workflows(_credential())
    )

    assert collection.model_dump(mode="json") == {
        "workflows": [
            {
                "todo_id": "live-workflow-1",
                "title": "live-title-1",
                "status": "1",
                "received_at": "2026-07-30",
                "created_at": "2026-07-29",
                "workflow_type_id": "live-workflow-type-1",
            }
        ],
        "returned_count": 1,
        "authoritative_count": 1,
        "is_complete": True,
    }
    assert opener.request_paths == [
        "/api/todo/splitPageKey",
        "/api/todo/counts",
        "/api/todo/datas",
    ]
    assert opener.request_forms == [
        _expected_pending_split_form(),
        {"dataKey": [sessionkey]},
        {"current": ["1"], "dataKey": [sessionkey], "sortParams": ["todo-sort"]},
    ]
    assert len(opener.request_forms[0]) == 34
    assert opener.request_forms[0]["viewcondition"] == ["5"]
    assert all(
        "pagesize" not in form and "pageSize" not in form
        for form in opener.request_forms
    )
    assert "/api/messages" not in opener.request_paths
    assert sessionkey not in repr(collection)


def test_live_pending_fails_when_records_exceed_authoritative_count() -> None:
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(1)),
        FakeHTTPResponse(
            _pending_datas_payload([_raw_workflow(1), _raw_workflow(2)])
        ),
    )

    with pytest.raises(OALivePayloadInvalid, match="exceeds the authoritative"):
        asyncio.run(
            _live_provider(opener).list_pending_workflows(_credential())
        )

    assert len(opener.request_forms) == 3


def test_live_pending_fails_when_page_exceeds_declared_size() -> None:
    sessionkey = _session_key()
    opener = SequencedOpener(
        FakeHTTPResponse(_pending_split_payload(sessionkey)),
        FakeHTTPResponse(_pending_counts_payload(2)),
        FakeHTTPResponse(
            _pending_datas_payload(
                [_raw_workflow(1), _raw_workflow(2)],
                page_size="1",
            )
        ),
    )

    with pytest.raises(OALivePayloadInvalid, match="declared size"):
        asyncio.run(
            _live_provider(opener).list_pending_workflows(_credential())
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "requestid",
        "requestname",
        "status",
        "receivedate",
        "createdate",
        "workflowid",
    ],
)
def test_live_pending_rejects_html_in_every_projected_text_field(
    field_name: str,
) -> None:
    record = _raw_workflow(1)
    record[field_name] = "<b>synthetic</b>"
    opener, _sessionkey = _pending_opener([record])
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
    assert result.data is None


def test_pending_sessionkey_never_reaches_exception_or_traceback_locals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessionkey = _session_key()
    provider = _live_provider(
        SequencedOpener(
            FakeHTTPResponse(_pending_split_payload(sessionkey)),
            TimeoutError(sessionkey),
        )
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OALiveTimeout) as exc_info:
        asyncio.run(provider.list_pending_workflows(_credential()))

    assert sessionkey not in (str(exc_info.value) + repr(exc_info.value) + caplog.text)
    _assert_provider_traceback_is_redacted(exc_info.value, sessionkey)


def test_invalid_pending_sessionkey_is_removed_from_traceback_locals() -> None:
    sessionkey = f" {_session_key()[:-1]}"
    provider = _live_provider(
        SequencedOpener(FakeHTTPResponse(_pending_split_payload(sessionkey)))
    )

    with pytest.raises(OALivePayloadInvalid) as exc_info:
        asyncio.run(provider.list_pending_workflows(_credential()))

    assert sessionkey not in (str(exc_info.value) + repr(exc_info.value))
    _assert_provider_traceback_is_redacted(exc_info.value, sessionkey)


def test_pending_sessionkey_never_reaches_adapter_result_or_trace_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sessionkey = _session_key()
    adapter = OAReadAdapter(
        _live_provider(
            SequencedOpener(
                FakeHTTPResponse(_pending_split_payload(sessionkey)),
                TimeoutError(sessionkey),
            )
        ),
        secret_provider=StaticSecretProvider(_credential()),
    )
    caplog.set_level(logging.DEBUG)

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    rendered = repr(result) + repr(result.trace_metadata) + caplog.text
    assert result.error_code == "adapter_timeout"
    assert result.data is None
    assert sessionkey not in rendered


@pytest.mark.parametrize(
    ("error", "expected_error_code"),
    [
        (CredentialNotFoundError(), "identity_unbound"),
        (CredentialExpiredError(), "identity_expired"),
        (InvalidCredentialReferenceError(), "adapter_error"),
        (CredentialStorageError(), "adapter_error"),
        (SecretProviderError(), "adapter_error"),
    ],
)
@pytest.mark.parametrize(
    "capability_id",
    ["oa.list_pending_workflows", "oa.list_system_messages"],
)
def test_secret_resolution_errors_are_safely_mapped(
    capability_id: str,
    error: Exception,
    expected_error_code: str,
) -> None:
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
    adapter = OAReadAdapter(
        _live_provider(opener),
        secret_provider=RaisingSecretProvider(error),
    )

    result = asyncio.run(
        adapter.execute(
            capability_id,
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert result.status == "error"
    assert result.error_code == expected_error_code
    assert result.trace_metadata is not None
    assert result.trace_metadata.failure_stage == "credential_read"
    assert opener.request_queries == []


def test_live_reports_matching_normalized_contract_structure() -> None:
    reports: list[Any] = []
    records = _matching_live_workflows()
    opener, _sessionkey = _pending_opener(records)

    collection = asyncio.run(
        _live_provider(
            opener,
            drift_reporter=reports.append,
        ).list_pending_workflows(_credential())
    )

    expected_fingerprint = build_contract_drift_baseline_fingerprint(
        json.loads((CONTRACT_PACK / "sample.json").read_text(encoding="utf-8"))
    )
    assert len(collection.workflows) == len(records)
    assert len(reports) == 1
    report = reports[0]
    assert report.matches is True
    assert report.expected_sha256 == expected_fingerprint["sha256"]
    assert report.actual_sha256 == expected_fingerprint["sha256"]
    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


@pytest.mark.parametrize(
    ("change_kind", "expected_bucket", "expected_path", "expected_status"),
    [
        (
            "added",
            "added",
            None,
            "success",
        ),
        ("removed", "removed", "$.workflows[].title", "error"),
        ("type", "changed", "$.workflows[].title", "error"),
        ("nullable", "changed", "$.workflows[].title", "error"),
        ("array_shape", "changed", "$.workflows[].title", "error"),
    ],
)
def test_live_reports_reachable_structural_drift_without_values(
    change_kind: str,
    expected_bucket: str,
    expected_path: str | None,
    expected_status: str,
) -> None:
    reports: list[Any] = []
    records = _matching_live_workflows()
    runtime_business_value = f"business-{secrets.token_hex(24)}"
    runtime_field_name = f"vendorField{secrets.token_hex(8)}"
    if change_kind == "added":
        records[0][runtime_field_name] = runtime_business_value
    elif change_kind == "removed":
        for record in records:
            record.pop("requestname")
    elif change_kind == "type":
        for record in records:
            record["requestname"] = 7
    elif change_kind == "nullable":
        records[0]["requestname"] = None
    elif change_kind == "array_shape":
        for record in records:
            record["requestname"] = [runtime_business_value]
    else:  # pragma: no cover - parameter table is closed above
        raise AssertionError("unknown structural change kind")

    opener, _sessionkey = _pending_opener(records)
    adapter = OAReadAdapter(
        _live_provider(opener, drift_reporter=reports.append),
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
    if expected_status == "error":
        assert result.error_code == "adapter_payload_invalid"
    assert len(reports) == 1
    report = reports[0]
    rendered = report.model_dump_json()
    assert report.matches is False
    bucket = getattr(report, expected_bucket)
    if change_kind == "added":
        _assert_anonymous_node_paths(
            bucket,
            root_path="$.workflows[]",
            expected_count=1,
        )
    else:
        assert [node.path for node in bucket] == [expected_path]
    if change_kind == "array_shape":
        assert report.changed[0].array_shape == "items:string"
    assert runtime_business_value not in rendered
    if change_kind == "added":
        assert runtime_business_value not in repr(result)
        assert runtime_field_name not in rendered


def test_live_actual_fingerprint_is_independent_of_contract_exemplar() -> None:
    records = _matching_live_workflows()
    expected_fingerprint = build_contract_drift_baseline_fingerprint(
        json.loads((CONTRACT_PACK / "sample.json").read_text(encoding="utf-8"))
    )
    records[0]["requestname"] = None

    report = compare_structural_fingerprints(
        expected_fingerprint,
        build_live_pending_workflows_fingerprint(records),
    )

    assert report.matches is False
    assert [node.path for node in report.changed] == [
        "$.workflows[].title"
    ]
    assert report.changed[0].nullable is True


@pytest.mark.parametrize(
    ("builder", "root_path", "records_factory"),
    [
        (
            build_live_pending_workflows_fingerprint,
            "$.workflows[]",
            _matching_live_workflows,
        ),
        (
            build_live_system_messages_fingerprint,
            "$.messages[]",
            _matching_live_system_messages,
        ),
    ],
)
def test_live_fingerprint_covers_union_of_actual_record_fields_without_values(
    builder: Callable[[list[Any]], dict[str, Any]],
    root_path: str,
    records_factory: Callable[[], list[dict[str, Any]]],
) -> None:
    records = records_factory()
    first_field_canary = f"sensitiveField{secrets.token_hex(8)}"
    second_field_canary = f"sensitiveField{secrets.token_hex(8)}"
    first_value_canary = f"sensitive-value-{secrets.token_hex(24)}"
    second_value_canary = f"sensitive-value-{secrets.token_hex(24)}"
    records[0][first_field_canary] = first_value_canary
    records[1][second_field_canary] = second_value_canary

    fingerprint = builder(records)
    rendered = json.dumps(fingerprint, sort_keys=True)
    unknown_paths = [
        node["path"]
        for node in fingerprint["nodes"]
        if node["path"].startswith(
            (
                f"{root_path}.unknown_field_"
                if builder is build_live_pending_workflows_fingerprint
                else f"{root_path}.wire_"
            )
        )
    ]

    expected_raw_keys = {first_field_canary, second_field_canary}
    unknown_nodes = [
        node for node in fingerprint["nodes"] if node["path"] in unknown_paths
    ]
    if builder is build_live_pending_workflows_fingerprint:
        _assert_anonymous_node_paths(
            unknown_nodes,
            root_path=root_path,
            expected_count=2,
        )
        for raw_key in expected_raw_keys:
            assert raw_key not in rendered
    else:
        _assert_wire_node_paths(
            unknown_nodes,
            root_path=root_path,
            raw_keys=expected_raw_keys,
        )
    for canary in (first_value_canary, second_value_canary):
        assert canary not in rendered

    records[0][first_field_canary] = f"changed-{secrets.token_hex(24)}"
    records[1][second_field_canary] = f"changed-{secrets.token_hex(24)}"

    assert builder(records) == fingerprint


def test_live_pending_unknown_name_is_anonymous_and_stable_when_key_is_added() -> None:
    earlier_field_canary = f"aSensitiveField{secrets.token_hex(8)}"
    retained_field_canary = f"zSensitiveField{secrets.token_hex(8)}"
    value_canary = f"sensitive-value-{secrets.token_hex(24)}"
    retained_record = _raw_workflow(1)
    retained_record[retained_field_canary] = value_canary
    earlier_record = _raw_workflow(2)
    earlier_record[earlier_field_canary] = 73

    baseline = build_live_pending_workflows_fingerprint([retained_record])
    candidate = build_live_pending_workflows_fingerprint(
        [earlier_record, retained_record]
    )
    report = compare_structural_fingerprints(baseline, candidate)

    _assert_anonymous_node_paths(
        report.added,
        root_path="$.workflows[]",
        expected_count=1,
    )
    assert report.added[0].json_type == "integer"
    assert report.removed == ()
    assert report.changed == ()
    rendered = report.model_dump_json()
    assert earlier_field_canary not in rendered
    assert retained_field_canary not in rendered
    assert value_canary not in rendered


def test_live_invalid_wire_key_uses_stable_digest_without_value() -> None:
    record = _raw_workflow(1)
    value_canary = f"sensitive-value-{secrets.token_hex(24)}"
    record["invalid-wire-key"] = value_canary

    fingerprint = build_live_pending_workflows_fingerprint([record])
    rendered = json.dumps(fingerprint, sort_keys=True)
    digest_paths = [
        node["path"]
        for node in fingerprint["nodes"]
        if node["path"].startswith("$.workflows[].unknown_field_")
    ]

    _assert_anonymous_node_paths(
        [
            node
            for node in fingerprint["nodes"]
            if node["path"] in digest_paths
        ],
        root_path="$.workflows[]",
        expected_count=1,
    )
    assert "invalid-wire-key" not in rendered
    assert value_canary not in rendered


def test_live_nested_unknown_fields_do_not_fold_and_are_order_independent() -> None:
    outer_field_canary = f"sensitiveOuter{secrets.token_hex(8)}"
    first_child_canary = f"sensitiveChildA{secrets.token_hex(8)}"
    second_child_canary = f"sensitiveChildZ{secrets.token_hex(8)}"
    value_canary = f"sensitive-value-{secrets.token_hex(24)}"
    first = _raw_workflow(1)
    second = _raw_workflow(2)
    first[outer_field_canary] = {first_child_canary: value_canary}
    second[outer_field_canary] = {second_child_canary: 73}

    forward = build_live_pending_workflows_fingerprint([first, second])
    reversed_order = build_live_pending_workflows_fingerprint([second, first])
    nested_paths = [
        node["path"]
        for node in forward["nodes"]
        if node["path"].count(".unknown_field_") == 2
    ]

    assert forward == reversed_order
    assert len(nested_paths) == 2
    assert len(set(nested_paths)) == 2
    nested_pattern = re.compile(
        r"^\$\.workflows\[\]\.unknown_field_[a-f0-9]{64}"
        r"\.unknown_field_[a-f0-9]{64}$"
    )
    assert all(nested_pattern.fullmatch(path) for path in nested_paths)
    rendered = json.dumps(forward, sort_keys=True)
    assert outer_field_canary not in rendered
    assert first_child_canary not in rendered
    assert second_child_canary not in rendered
    assert value_canary not in rendered


def test_live_provider_unknown_field_union_is_stable_across_page_and_record_order(
) -> None:
    first_field_canary = f"sensitiveAField{secrets.token_hex(8)}"
    second_field_canary = f"sensitiveZField{secrets.token_hex(8)}"
    first_value_canary = f"sensitive-value-{secrets.token_hex(24)}"
    second_value_canary = 73
    first_page = _matching_live_workflows()
    second_page = [_raw_workflow(3), _raw_workflow(4)]
    first_page[0][first_field_canary] = second_value_canary
    second_page[1][second_field_canary] = first_value_canary

    def run_pages(pages: list[list[dict[str, Any]]]) -> Any:
        reports: list[Any] = []
        sessionkey = _session_key()
        opener = SequencedOpener(
            FakeHTTPResponse(_pending_split_payload(sessionkey)),
            FakeHTTPResponse(_pending_counts_payload(4)),
            FakeHTTPResponse(_pending_datas_payload(pages[0], page_size="2")),
            FakeHTTPResponse(_pending_datas_payload(pages[1], page_size="2")),
        )

        collection = asyncio.run(
            _live_provider(
                opener,
                drift_reporter=reports.append,
            ).list_pending_workflows(_credential())
        )

        assert len(collection.workflows) == 4
        assert len(reports) == 1
        return reports[0]

    forward = run_pages(copy.deepcopy([first_page, second_page]))
    reversed_order = run_pages(
        [
            list(reversed(copy.deepcopy(second_page))),
            list(reversed(copy.deepcopy(first_page))),
        ]
    )

    assert forward.actual_sha256 == reversed_order.actual_sha256
    assert forward.added == reversed_order.added
    _assert_anonymous_node_paths(
        forward.added,
        root_path="$.workflows[]",
        expected_count=2,
    )
    assert {node.json_type for node in forward.added} == {
        "integer",
        "string",
    }
    assert forward.removed == ()
    assert forward.changed == ()
    rendered = forward.model_dump_json() + reversed_order.model_dump_json()
    assert first_field_canary not in rendered
    assert second_field_canary not in rendered
    assert first_value_canary not in rendered


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


class ExplodingProvider:
    requires_credential = False

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        del credential
        raise RuntimeError("SECRET-CANARY-12345")


class RaisingProvider:
    requires_credential = False

    def __init__(self, error: Exception, *, requires_credential: bool = False) -> None:
        self._error = error
        self.requires_credential = requires_credential

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        if self.requires_credential:
            assert credential is not None
        raise self._error

    async def list_system_messages(
        self,
        credential: OASessionCredential | None = None,
    ) -> OASystemMessageCollection:
        if self.requires_credential:
            assert credential is not None
        raise self._error


class ExplodingSerializationResult:
    def model_dump(self, *, mode: str) -> dict[str, Any]:
        del mode
        raise RuntimeError("SERIALIZATION-SECRET-CANARY")


class ExplodingSerializationProvider:
    requires_credential = False

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> Any:
        del credential
        return ExplodingSerializationResult()

    async def list_system_messages(
        self,
        credential: OASessionCredential | None = None,
    ) -> Any:
        del credential
        return ExplodingSerializationResult()


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_error_code", "expected_stage"),
    [
        (
            OAContractPackPayloadInvalid("payload-value-must-not-persist"),
            "error",
            "adapter_payload_invalid",
            "normalization",
        ),
        (
            OAContractPackError("pack-value-must-not-persist"),
            "error",
            "adapter_error",
            "unknown",
        ),
        (
            OALiveIdentityUnbound("identity-value-must-not-persist"),
            "error",
            "identity_unbound",
            "provider_transport",
        ),
        (
            OALiveIdentityExpired("identity-value-must-not-persist"),
            "error",
            "identity_expired",
            "provider_transport",
        ),
        (
            OALivePermissionDenied("transport-value-must-not-persist"),
            "permission_denied",
            "upstream_permission_denied",
            "provider_transport",
        ),
        (
            OALiveTimeout("transport-value-must-not-persist"),
            "timeout",
            "adapter_timeout",
            "provider_transport",
        ),
        (
            OALiveHTTPServerError("transport-value-must-not-persist"),
            "error",
            "adapter_http_500",
            "provider_transport",
        ),
        (
            OALiveRequestError("transport-value-must-not-persist"),
            "error",
            "adapter_error",
            "provider_transport",
        ),
        (
            OALivePayloadInvalid("payload-value-must-not-persist"),
            "error",
            "adapter_payload_invalid",
            "normalization",
        ),
        (
            OALiveProviderError("provider-value-must-not-persist"),
            "error",
            "adapter_error",
            "unknown",
        ),
        (
            RuntimeError("runtime-value-must-not-persist"),
            "error",
            "adapter_error",
            "unknown",
        ),
    ],
)
def test_provider_failure_branches_have_fixed_safe_stage_mapping(
    error: Exception,
    expected_status: str,
    expected_error_code: str,
    expected_stage: str,
) -> None:
    result = asyncio.run(
        OAReadAdapter(RaisingProvider(error)).execute(
            "oa.list_pending_workflows",
            {},
            {},
        )
    )

    assert result.status == expected_status
    assert result.error_code == expected_error_code
    assert result.trace_metadata is not None
    assert result.trace_metadata.argument_keys == ()
    assert result.trace_metadata.failure_stage == expected_stage
    assert str(error) not in repr(result.trace_metadata)
    assert str(error) not in repr(result)


@pytest.mark.parametrize(
    ("error", "expected_error_code"),
    [
        (OALiveIdentityUnbound("provider-identity-unbound"), "identity_unbound"),
        (OALiveIdentityExpired("provider-identity-expired"), "identity_expired"),
    ],
)
def test_provider_identity_failure_after_credential_read_is_transport_stage(
    error: Exception,
    expected_error_code: str,
) -> None:
    secret_provider = StaticSecretProvider(_credential())
    adapter = OAReadAdapter(
        RaisingProvider(error, requires_credential=True),
        secret_provider=secret_provider,
    )

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {},
            {"credential_ref": "oa-session-v1:server-surrogate"},
        )
    )

    assert secret_provider.calls == ["oa-session-v1:server-surrogate"]
    assert result.status == "error"
    assert result.error_code == expected_error_code
    assert result.trace_metadata is not None
    assert result.trace_metadata.failure_stage == "provider_transport"
    assert str(error) not in repr(result)


def test_serialization_failure_is_unknown_and_does_not_persist_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="app.infra.adapters.oa.adapter")
    result = asyncio.run(
        OAReadAdapter(ExplodingSerializationProvider()).execute(
            "oa.list_pending_workflows",
            {},
            {},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.trace_metadata is not None
    assert result.trace_metadata.failure_stage == "unknown"
    rendered = "\n".join(
        (
            caplog.text,
            repr([record.__dict__ for record in caplog.records]),
            repr([record.args for record in caplog.records]),
            repr(result),
            repr(result.trace_metadata),
            repr(result.model_dump(mode="json")),
        )
    )
    assert "exception_type=RuntimeError" in caplog.text
    assert "SERIALIZATION-SECRET-CANARY" not in rendered


def test_unexpected_failure_log_result_and_repr_do_not_leak_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime_secret = "SECRET-CANARY-12345"
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
            repr([record.__dict__ for record in caplog.records]),
            repr(result),
            repr(adapter),
            repr(provider),
            repr(secret_provider),
            repr(credential),
        )
    )

    assert result.model_dump(mode="json") == {
        "status": "error",
        "data": None,
        "error_code": "adapter_error",
        "raw_payload_ref": None,
    }
    assert "capability_id=oa.list_pending_workflows" in caplog.text
    assert "stage=unknown" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "classification=adapter_error" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert result.trace_metadata is not None
    assert result.trace_metadata.failure_stage == "unknown"
    assert runtime_secret not in rendered


def test_unexpected_failure_preserves_gateway_response_and_trace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trace_logger = logging.getLogger("tests.oa.adapter_error_trace")
    caplog.set_level(logging.DEBUG, logger=trace_logger.name)
    gateway = CapabilityGateway(
        adapter=OAReadAdapter(ExplodingProvider()),
        trace_port=NoopTraceWriter(logger=trace_logger),
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-adapter-error-001",
            "session-adapter-error-001",
            "ai-user-adapter-error-001",
            "oa.list_pending_workflows",
            {},
            RequestOrgContext(request_id="trace-adapter-error-001"),
        )
    )
    trace_events = [
        record.trace_event
        for record in caplog.records
        if record.name == trace_logger.name
    ]

    assert result.model_dump(mode="json") == {
        "status": "failed",
        "data": None,
        "error_code": "adapter_error",
        "trace_id": "trace-adapter-error-001",
    }
    assert [
        (event["event_type"], event["status"], event["error_code"])
        for event in trace_events
    ] == [
        ("gateway_pre_recorded", "ok", None),
        ("adapter_called", "ok", "adapter_error"),
        ("gateway_post_recorded", "failed", "adapter_error"),
        ("adapter_error_mapped", "failed", "adapter_error"),
    ]
    adapter_called = next(
        event for event in trace_events if event["event_type"] == "adapter_called"
    )
    assert adapter_called["attributes"] == {
        "argument_keys": [],
        "failure_stage": "unknown",
    }
    assert "SECRET-CANARY-12345" not in repr(trace_events)


def test_argument_keys_are_sorted_and_values_never_reach_logs_or_trace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    password_value_canary = "PASSWORD-VALUE-CANARY-12345"
    cookie_value_canary = "COOKIE-VALUE-CANARY-12345"
    token_value_canary = "TOKEN-VALUE-CANARY-12345"
    loginid_value_canary = "LOGINID-VALUE-CANARY-12345"
    cookie_nested_key_canary = "cookie_nested_key_canary"
    token_nested_key_canary = "token_nested_key_canary"
    loginid_nested_key_canary = "loginid_nested_key_canary"
    trace_logger = logging.getLogger("tests.oa.argument_key_trace")
    caplog.set_level(logging.DEBUG, logger=trace_logger.name)
    adapter = OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK))
    gateway = CapabilityGateway(
        adapter=adapter,
        trace_port=NoopTraceWriter(logger=trace_logger),
    )
    arguments = {
        "password": password_value_canary,
        "cookie": {cookie_nested_key_canary: cookie_value_canary},
        "token": [{token_nested_key_canary: token_value_canary}],
        "loginid": {loginid_nested_key_canary: loginid_value_canary},
    }

    direct_result = asyncio.run(
        adapter.execute("oa.list_pending_workflows", arguments, {})
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-argument-keys-001",
            "session-argument-keys-001",
            "ai-user-argument-keys-001",
            "oa.list_pending_workflows",
            arguments,
            RequestOrgContext(request_id="trace-argument-keys-001"),
        )
    )
    trace_events = [
        record.trace_event
        for record in caplog.records
        if record.name == trace_logger.name
    ]
    adapter_called = next(
        event for event in trace_events if event["event_type"] == "adapter_called"
    )
    rendered = "\n".join(
        (
            caplog.text,
            repr([record.__dict__ for record in caplog.records]),
            repr([record.args for record in caplog.records]),
            repr(direct_result),
            repr(direct_result.model_dump(mode="json")),
            repr(direct_result.trace_metadata),
            repr(
                direct_result.trace_metadata.model_dump(mode="json")
                if direct_result.trace_metadata is not None
                else None
            ),
            repr(result),
            repr(trace_events),
            json.dumps(trace_events, sort_keys=True),
        )
    )

    assert result.status == "failed"
    assert direct_result.trace_metadata is not None
    assert direct_result.trace_metadata.argument_keys == (
        "cookie",
        "loginid",
        "password",
        "token",
    )
    assert adapter_called["attributes"] == {
        "argument_keys": ["cookie", "loginid", "password", "token"],
        "failure_stage": "argument_validation",
    }
    for canary in (
        password_value_canary,
        cookie_value_canary,
        token_value_canary,
        loginid_value_canary,
        cookie_nested_key_canary,
        token_nested_key_canary,
        loginid_nested_key_canary,
    ):
        assert canary not in rendered
    assert [
        event["event_type"] for event in trace_events if event["attributes"]
    ] == ["adapter_called"]


def test_success_trace_persists_empty_keys_and_explicit_none_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trace_logger = logging.getLogger("tests.oa.success_trace_metadata")
    caplog.set_level(logging.DEBUG, logger=trace_logger.name)
    gateway = CapabilityGateway(
        adapter=OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK)),
        trace_port=NoopTraceWriter(logger=trace_logger),
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-success-metadata-001",
            "session-success-metadata-001",
            "ai-user-success-metadata-001",
            "oa.list_pending_workflows",
            {},
            RequestOrgContext(request_id="trace-success-metadata-001"),
        )
    )
    adapter_called = next(
        record.trace_event
        for record in caplog.records
        if record.name == trace_logger.name
        and record.trace_event["event_type"] == "adapter_called"
    )

    assert result.status == "completed"
    assert adapter_called["attributes"] == {
        "argument_keys": [],
        "failure_stage": None,
    }
    trace_events = [
        record.trace_event
        for record in caplog.records
        if record.name == trace_logger.name
    ]
    assert [
        event["event_type"] for event in trace_events if event["attributes"]
    ] == ["adapter_called"]
