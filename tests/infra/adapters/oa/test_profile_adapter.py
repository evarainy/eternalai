"""OA user-profile adapter: classification, SSRF guard and avatar admission.

Everything here is synthetic. No real name, department, identifier, cookie or
avatar path from OA appears in this file.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request

import pytest
from pydantic import SecretStr

from app.infra.adapters.oa.profile import (
    DEFAULT_MAX_AVATAR_BYTES,
    LiveOAProfileTransport,
    OAUserProfileAdapter,
    allowed_avatar_media_type,
    validate_avatar_path,
)
from app.infra.adapters.oa.provider import (
    SameOriginRedirectHandler,
    origin_tuple,
)
from app.ports.auth import OASessionCredential
from app.ports.secret_provider import (
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialStorageError,
    InvalidCredentialReferenceError,
)
from app.ports.user_profile import UserAvatar

BASE_URL = "https://oa.invalid"
AI_USER_ID = "usr_v1_" + "A" * 43
OTHER_AI_USER_ID = "usr_v1_" + "B" * 43
SESSION_COOKIE_VALUE = "synthetic-session-value"
AVATAR_PATH = "/messager/usericon/synthetic.jpg"
UNIT_ANCHOR = '<a onclick="javascript:viewSubCompany(11)">单位甲</a>'
DEPARTMENT_ANCHOR = '<a onclick="javascript:viewDepartment(22)">部门乙</a>'
IMAGE_BYTES = b"\x89PNG\r\n\x1a\nsynthetic"


def credential(oa_user_id: str = "3001") -> OASessionCredential:
    return OASessionCredential(
        oa_user_id=SecretStr(oa_user_id),
        cookies={"ecology_JSessionid": SecretStr(SESSION_COOKIE_VALUE)},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def profile_payload(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "lastname": "合成姓名",
        "messagerurl": AVATAR_PATH,
        "orginfo": f"{UNIT_ANCHOR}&gt;{DEPARTMENT_ANCHOR}",
        "workcode": "",
        "sex": {"name": "合成", "value": "0"},
    }
    result.update(overrides)
    return {"result": result, "hasRight": True, "id": "3001"}


class FakeSecretProvider:
    """Resolve one reference per user; anything else fails closed."""

    def __init__(
        self,
        credentials: dict[str, OASessionCredential] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.credentials = credentials or {}
        self.error = error
        self.references: list[str] = []

    async def resolve_secret_ref(
        self,
        credential_ref: str,
        task_id: str,
        capability_id: str,
    ) -> dict[str, Any]:
        raise AssertionError("the profile adapter must not resolve task secrets")

    async def inject_execution_secret(
        self,
        execution_context: dict[str, Any],
        credential_ref: str,
    ) -> dict[str, Any]:
        raise AssertionError("the profile adapter must not inject secrets")

    async def resolve_oa_session(self, credential_ref: str) -> OASessionCredential:
        self.references.append(credential_ref)
        if self.error is not None:
            raise self.error
        resolved = self.credentials.get(credential_ref)
        if resolved is None:
            raise CredentialNotFoundError
        return resolved


class RecordingTransport:
    """Counts calls so a rejected avatar target can be proven to send nothing."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        payload_error: Exception | None = None,
        avatar: UserAvatar | None = None,
    ) -> None:
        self.payload = payload if payload is not None else profile_payload()
        self.payload_error = payload_error
        self.avatar = avatar
        self.profile_calls: list[str] = []
        self.avatar_calls: list[str] = []

    async def fetch_profile(
        self,
        credential: OASessionCredential,
    ) -> dict[str, Any]:
        self.profile_calls.append(credential.oa_user_id.get_secret_value())
        if self.payload_error is not None:
            raise self.payload_error
        return self.payload

    async def fetch_avatar(
        self,
        credential: OASessionCredential,
        path: str,
    ) -> UserAvatar | None:
        self.avatar_calls.append(path)
        return self.avatar


def build_adapter(
    transport: RecordingTransport,
    secret_provider: FakeSecretProvider | None = None,
) -> OAUserProfileAdapter:
    provider = secret_provider or FakeSecretProvider(
        {f"oa-session-v1:{AI_USER_ID}": credential()}
    )
    return OAUserProfileAdapter(
        secret_provider=provider,
        transport=transport,
        base_url=BASE_URL,
    )


# --------------------------------------------------------------------------
# Success and organization classification
# --------------------------------------------------------------------------


def test_successful_read_returns_the_parsed_placement() -> None:
    transport = RecordingTransport()

    snapshot = asyncio.run(build_adapter(transport).get_profile(AI_USER_ID))

    assert snapshot.org_status == "ok"
    assert snapshot.org is not None
    assert snapshot.org.department_name == "部门乙"
    assert snapshot.org.department_id == "22"
    assert snapshot.org.unit_name == "单位甲"
    assert snapshot.org.unit_id == "11"
    assert snapshot.avatar_available is True


def test_credential_reference_is_derived_from_the_requested_user() -> None:
    provider = FakeSecretProvider(
        {
            f"oa-session-v1:{AI_USER_ID}": credential("3001"),
            f"oa-session-v1:{OTHER_AI_USER_ID}": credential("4002"),
        }
    )
    transport = RecordingTransport()

    asyncio.run(build_adapter(transport, provider).get_profile(OTHER_AI_USER_ID))

    assert provider.references == [f"oa-session-v1:{OTHER_AI_USER_ID}"]
    assert transport.profile_calls == ["4002"]


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {}, "hasRight": False},
        {"result": {}},
        {"result": {}, "hasRight": "true"},
        {"result": {}, "hasRight": 1},
        {"hasRight": True},
        {"hasRight": True, "result": "部门乙"},
        {"hasRight": True, "result": []},
    ],
)
def test_missing_right_or_result_is_unavailable(payload: dict[str, Any]) -> None:
    transport = RecordingTransport(payload)

    snapshot = asyncio.run(build_adapter(transport).get_profile(AI_USER_ID))

    assert snapshot.org_status == "unavailable"
    assert snapshot.org is None
    assert snapshot.avatar_available is False


@pytest.mark.parametrize(
    "orginfo",
    [None, "", 22, {"unit": "单位甲"}, "<a>部门乙</a>", DEPARTMENT_ANCHOR * 2],
)
def test_unusable_orginfo_is_unparsable_but_keeps_the_avatar_judgement(
    orginfo: object,
) -> None:
    transport = RecordingTransport(profile_payload(orginfo=orginfo))

    snapshot = asyncio.run(build_adapter(transport).get_profile(AI_USER_ID))

    assert snapshot.org_status == "unparsable"
    assert snapshot.org is None
    # A department we cannot read says nothing about whether a photo exists.
    assert snapshot.avatar_available is True


def test_missing_lastname_does_not_affect_the_profile() -> None:
    payload = profile_payload()
    del payload["result"]["lastname"]
    transport = RecordingTransport(payload)

    snapshot = asyncio.run(build_adapter(transport).get_profile(AI_USER_ID))

    # The display name comes from the signed ticket, never from this response.
    assert snapshot.org_status == "ok"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CredentialNotFoundError(), "unbound"),
        (CredentialExpiredError(), "expired"),
        (CredentialStorageError(), "unavailable"),
        (InvalidCredentialReferenceError(), "unavailable"),
        (RuntimeError("synthetic"), "unavailable"),
    ],
)
def test_credential_failures_are_classified(error: Exception, expected: str) -> None:
    transport = RecordingTransport()
    provider = FakeSecretProvider(error=error)

    snapshot = asyncio.run(build_adapter(transport, provider).get_profile(AI_USER_ID))

    assert snapshot.org_status == expected
    assert snapshot.org is None
    assert snapshot.avatar_available is False
    assert transport.profile_calls == []


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        socket.timeout(),
        URLError("synthetic"),
        HTTPError("https://oa.invalid/x", 500, "server", None, None),  # type: ignore[arg-type]
        HTTPError("https://oa.invalid/x", 403, "denied", None, None),  # type: ignore[arg-type]
        ValueError("OA profile response exceeds the size limit"),
        OSError("synthetic"),
        RuntimeError("synthetic"),
    ],
)
def test_transport_failures_are_unavailable(error: Exception) -> None:
    transport = RecordingTransport(payload_error=error)

    snapshot = asyncio.run(build_adapter(transport).get_profile(AI_USER_ID))

    assert snapshot.org_status == "unavailable"
    assert snapshot.org is None


def test_upstream_failure_never_reaches_the_caller_as_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = RecordingTransport(payload_error=RuntimeError("synthetic-oa-detail"))

    with caplog.at_level("DEBUG"):
        snapshot = asyncio.run(build_adapter(transport).get_profile(AI_USER_ID))

    assert snapshot.org_status == "unavailable"
    assert "synthetic-oa-detail" not in caplog.text


# --------------------------------------------------------------------------
# messagerurl is an upstream-controlled request target: SSRF guard
# --------------------------------------------------------------------------


REJECTED_AVATAR_VALUES = [
    None,
    "",
    22,
    b"/messager/usericon/x.jpg",
    ["/messager/usericon/x.jpg"],
    "//evil.invalid/x.jpg",
    "https://evil.invalid/x.jpg",
    "http://oa.invalid/messager/usericon/x.jpg",
    "/messager/../../etc/passwd.jpg",
    "/messager/./x.jpg",
    "/messager//x.jpg",
    "/messager/usericon/x.jpg?size=200",
    "/messager/usericon/x.jpg#fragment",
    "/messager/usericon/x.svg",
    "/messager/usericon/x.txt",
    "/messager/usericon/x",
    "/messager/usericon/%2e%2e/x.jpg",
    "/messager/usericon/x.jpg\\..\\y.jpg",
    "/messager/usericon/名字.jpg",
    "/messager/usericon/ x.jpg",
    "/messager/usericon/x.jpg\n",
    "/user@evil.invalid/x.jpg",
    "messager/usericon/x.jpg",
    "/" + "a" * 300 + ".jpg",
]


@pytest.mark.parametrize("value", REJECTED_AVATAR_VALUES)
def test_rejected_messagerurl_makes_no_outbound_avatar_request(value: object) -> None:
    transport = RecordingTransport(profile_payload(messagerurl=value))
    adapter = build_adapter(transport)

    snapshot = asyncio.run(adapter.get_profile(AI_USER_ID))
    avatar = asyncio.run(adapter.get_avatar(AI_USER_ID))

    assert snapshot.avatar_available is False
    assert avatar is None
    # The guard has teeth only if it stops the request, not merely the result.
    assert transport.avatar_calls == []


@pytest.mark.parametrize("value", REJECTED_AVATAR_VALUES)
def test_validate_avatar_path_rejects_every_unsafe_shape(value: object) -> None:
    assert validate_avatar_path(value, base_url=BASE_URL) is None


@pytest.mark.parametrize(
    "value",
    [
        "/messager/usericon/synthetic.jpg",
        "/messager/usericon/synthetic.JPEG",
        "/messager/usericon/synthetic.png",
        "/messager/usericon/synthetic.gif",
        "/messager/usericon/synthetic-1_2.webp",
    ],
)
def test_validate_avatar_path_accepts_host_relative_images(value: str) -> None:
    assert validate_avatar_path(value, base_url=BASE_URL) == value


def test_missing_messagerurl_key_behaves_like_every_other_rejection() -> None:
    payload = profile_payload()
    del payload["result"]["messagerurl"]
    transport = RecordingTransport(payload)
    adapter = build_adapter(transport)

    assert asyncio.run(adapter.get_profile(AI_USER_ID)).avatar_available is False
    assert asyncio.run(adapter.get_avatar(AI_USER_ID)) is None
    assert transport.avatar_calls == []


def test_accepted_messagerurl_reaches_the_transport_once() -> None:
    transport = RecordingTransport(
        avatar=UserAvatar(media_type="image/png", content=IMAGE_BYTES)
    )
    adapter = build_adapter(transport)

    avatar = asyncio.run(adapter.get_avatar(AI_USER_ID))

    assert avatar is not None
    assert avatar.content == IMAGE_BYTES
    assert transport.avatar_calls == [AVATAR_PATH]


def test_avatar_read_requires_the_requesting_users_own_credential() -> None:
    provider = FakeSecretProvider(
        {f"oa-session-v1:{AI_USER_ID}": credential("3001")}
    )
    transport = RecordingTransport()

    assert asyncio.run(build_adapter(transport, provider).get_avatar(OTHER_AI_USER_ID)) is None
    assert provider.references == [f"oa-session-v1:{OTHER_AI_USER_ID}"]
    assert transport.avatar_calls == []


@pytest.mark.parametrize("ai_user_id", ["", "not-a-reference"])
def test_malformed_user_identifier_fails_closed(ai_user_id: str) -> None:
    provider = FakeSecretProvider(error=InvalidCredentialReferenceError())
    transport = RecordingTransport()
    adapter = build_adapter(transport, provider)

    assert asyncio.run(adapter.get_profile(ai_user_id)).org_status == "unavailable"
    assert asyncio.run(adapter.get_avatar(ai_user_id)) is None
    assert transport.profile_calls == []


# --------------------------------------------------------------------------
# Live transport: only 200 plus a whitelisted image type is an avatar
# --------------------------------------------------------------------------


class _FakeHeaders:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = {key.casefold(): value for key, value in values.items()}

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._values.get(name.casefold(), default)


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self._status = status
        self.headers = _FakeHeaders(headers)
        self._body = body

    def getcode(self) -> int:
        return self._status

    def read(self, amount: int) -> bytes:
        return self._body[:amount]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float | None = None) -> _FakeResponse:
        self.requests.append(request)
        return self._response


def build_transport(response: _FakeResponse) -> tuple[LiveOAProfileTransport, _FakeOpener]:
    opener = _FakeOpener(response)
    transport = LiveOAProfileTransport(
        base_url=BASE_URL,
        timeout_seconds=3.0,
        opener_factory=lambda: opener,
    )
    return transport, opener


def test_transport_fetches_the_profile_with_the_user_cookie() -> None:
    response = _FakeResponse(
        200,
        {"Content-Type": "application/json"},
        b'{"result": {}, "hasRight": true}',
    )
    transport, opener = build_transport(response)

    payload = asyncio.run(transport.fetch_profile(credential()))

    assert payload == {"result": {}, "hasRight": True}
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert urlsplit(request.full_url).netloc == "oa.invalid"
    assert request.get_method() == "GET"
    assert SESSION_COOKIE_VALUE in (request.get_header("Cookie") or "")
    assert "id=3001" in request.full_url


@pytest.mark.parametrize(
    "content_type",
    [
        "text/html; charset=utf-8",
        "application/octet-stream",
        "image/svg+xml",
        "image/bmp",
        "application/json",
        "",
    ],
)
def test_avatar_content_type_outside_the_whitelist_is_refused(
    content_type: str,
) -> None:
    response = _FakeResponse(200, {"Content-Type": content_type}, IMAGE_BYTES)
    transport, opener = build_transport(response)

    assert asyncio.run(transport.fetch_avatar(credential(), AVATAR_PATH)) is None
    assert len(opener.requests) == 1


def test_avatar_without_a_content_type_is_refused() -> None:
    response = _FakeResponse(200, {}, IMAGE_BYTES)
    transport, _ = build_transport(response)

    assert asyncio.run(transport.fetch_avatar(credential(), AVATAR_PATH)) is None


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("image/jpeg", "image/jpeg"),
        ("IMAGE/JPEG; charset=binary", "image/jpeg"),
        ("image/png ", "image/png"),
        ("image/gif;", "image/gif"),
        ("image/webp", "image/webp"),
        ("image/svg+xml", None),
        (None, None),
        (b"image/png", None),
    ],
)
def test_media_type_whitelist_ignores_parameters_and_case(
    declared: object,
    expected: str | None,
) -> None:
    assert allowed_avatar_media_type(declared) == expected


def test_avatar_is_served_with_our_own_whitelisted_media_type() -> None:
    response = _FakeResponse(
        200,
        {"Content-Type": "IMAGE/JPEG; charset=binary"},
        IMAGE_BYTES,
    )
    transport, _ = build_transport(response)

    avatar = asyncio.run(transport.fetch_avatar(credential(), AVATAR_PATH))

    assert avatar is not None
    # The value we hand back is ours, not the upstream header echoed through.
    assert avatar.media_type == "image/jpeg"
    assert avatar.content == IMAGE_BYTES


@pytest.mark.parametrize("status_code", [201, 204, 302, 304, 400, 401, 403, 404, 500])
def test_avatar_requires_exactly_200(status_code: int) -> None:
    response = _FakeResponse(status_code, {"Content-Type": "image/png"}, IMAGE_BYTES)
    transport, _ = build_transport(response)

    assert asyncio.run(transport.fetch_avatar(credential(), AVATAR_PATH)) is None


def test_oversized_avatar_body_is_refused() -> None:
    body = b"\x00" * (DEFAULT_MAX_AVATAR_BYTES + 1)
    response = _FakeResponse(200, {"Content-Type": "image/png"}, body)
    transport, _ = build_transport(response)

    assert asyncio.run(transport.fetch_avatar(credential(), AVATAR_PATH)) is None


def test_empty_avatar_body_is_refused() -> None:
    response = _FakeResponse(200, {"Content-Type": "image/png"}, b"")
    transport, _ = build_transport(response)

    assert asyncio.run(transport.fetch_avatar(credential(), AVATAR_PATH)) is None


def test_oversized_profile_body_is_refused() -> None:
    body = b'{"result": {"orginfo": "' + b"a" * (256 * 1024) + b'"}}'
    response = _FakeResponse(200, {"Content-Type": "application/json"}, body)
    transport, _ = build_transport(response)

    with pytest.raises(ValueError):
        asyncio.run(transport.fetch_profile(credential()))


def test_transport_revalidates_the_avatar_path_before_opening_anything() -> None:
    response = _FakeResponse(200, {"Content-Type": "image/png"}, IMAGE_BYTES)
    transport, opener = build_transport(response)

    result = asyncio.run(
        transport.fetch_avatar(credential(), "https://evil.invalid/x.jpg")
    )

    assert result is None
    assert opener.requests == []


def test_isolated_opener_disables_proxies_and_cross_host_redirects() -> None:
    transport = LiveOAProfileTransport(base_url=BASE_URL, timeout_seconds=3.0)

    opener = transport._build_isolated_opener()  # noqa: SLF001

    handlers = opener.handlers
    # An empty ProxyHandler is passed in so build_opener skips the default one;
    # the observable effect is that no proxy handler is installed at all, so an
    # ambient *_proxy environment variable cannot redirect an OA request.
    assert not any(isinstance(handler, ProxyHandler) for handler in handlers)
    same_origin = [
        handler
        for handler in handlers
        if isinstance(handler, SameOriginRedirectHandler)
    ]
    assert len(same_origin) == 1
    # The permissive stock redirect handler must not also be present, or a
    # cross-host hop would still carry the Cookie header.
    assert not any(type(handler) is HTTPRedirectHandler for handler in handlers)


def test_cookie_bearing_redirect_to_another_host_is_refused() -> None:
    handler = SameOriginRedirectHandler(origin_tuple(urlsplit(BASE_URL)))
    request = Request(f"{BASE_URL}{AVATAR_PATH}", headers={"Cookie": "k=v"})

    redirected = handler.redirect_request(
        request,
        None,  # type: ignore[arg-type]
        302,
        "Found",
        None,  # type: ignore[arg-type]
        "https://evil.invalid/steal.jpg",
    )

    assert redirected is None
