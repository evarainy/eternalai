from __future__ import annotations

import asyncio
import ssl
import threading
from uuid import uuid4

import pytest

from app.infra.browser.browserless_wire import (
    BrowserlessWire,
    BrowserProviderError,
    HTTPSBrowserlessTransport,
    WireResponse,
    parse_object,
)
from tests.infra.browser.provider_factories import HTTP, deployment


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_request"),
        (401, "denied"),
        (403, "denied"),
        (404, "resource_not_found"),
        (408, "timeout"),
        (429, "overloaded"),
        (500, "unavailable"),
        (503, "unavailable"),
        (302, "invalid_response"),
    ],
)
def test_http_failure_codes_and_no_retries(status, code) -> None:
    async def run():
        credential = uuid4().hex
        http = HTTP(credential)
        http.status = status
        wire = BrowserlessWire(deployment(), credential, http)
        with pytest.raises(BrowserProviderError) as error:
            await wire.create_session(None)
        assert error.value.failure.code == code and len(http.calls) == 1
        assert credential not in str(error.value)

    asyncio.run(run())


def test_real_https_transport_exact_destination_limit_and_finally_close(monkeypatch) -> None:
    events = []

    class Response:
        status = 200

        def read(self, count):
            events.append(("read", count))
            return b"{}"

        def getheader(self, name, default):
            return "application/json"

    class Connection:
        def __init__(self, host, *, port, timeout, context):
            assert isinstance(context, ssl.SSLContext)
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            assert context.keylog_filename is None
            events.append(("connect", host, port, timeout))

        def set_debuglevel(self, level):
            assert level == 0

        def request(self, method, path, body, headers):
            events.append(("request", method, path, body))

        def getresponse(self):
            return Response()

        def close(self):
            events.append(("close",))

    monkeypatch.setattr("http.client.HTTPSConnection", Connection)
    result = asyncio.run(
        HTTPSBrowserlessTransport().request(
            "POST",
            "https://fixture.invalid:8443/session?x=synthetic",
            b"{}",
            1.0,
            10,
        )
    )
    assert result.status == 200 and result.body == b"{}"
    assert events == [
        ("connect", "fixture.invalid", 8443, 1.0),
        ("request", "POST", "/session?x=synthetic", b"{}"),
        ("read", 11),
        ("close",),
    ]


def test_transport_timeout_closes_connection_and_cancel_does_not_claim_worker_finished(monkeypatch):
    started, finish, closed = threading.Event(), threading.Event(), threading.Event()

    class Connection:
        def __init__(self, *args, **kwargs):
            return None

        def set_debuglevel(self, level):
            assert level == 0

        def request(self, *args):
            started.set()
            if not finish.wait(timeout=2):
                raise AssertionError("test_worker_not_released")
            raise TimeoutError()

        def close(self):
            closed.set()

    monkeypatch.setattr("http.client.HTTPSConnection", Connection)

    async def run():
        task = asyncio.create_task(
            HTTPSBrowserlessTransport().request(
                "POST",
                "https://fixture.invalid/session",
                b"{}",
                1.0,
                10,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not closed.is_set()
        finish.set()
        assert await asyncio.to_thread(closed.wait, 1)

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "host",
        "userinfo",
        "duplicate_token",
        "wrong_id",
        "unknown_field",
        "wrong_ttl",
        "fragment",
    ],
)
def test_returned_session_handles_reject_untrusted_routes(mutation) -> None:
    async def run():
        credential = uuid4().hex
        http = HTTP(credential)

        def corrupt(result):
            if mutation == "host":
                result["connect"] = result["connect"].replace(
                    "production-sfo.browserless.io", "evil.test"
                )
            elif mutation == "userinfo":
                result["connect"] = result["connect"].replace("wss://", "wss://user@")
            elif mutation == "duplicate_token":
                result["connect"] += "&token=" + credential
            elif mutation == "wrong_id":
                result["id"] = uuid4().hex
            elif mutation == "unknown_field":
                result["unexpected"] = True
            elif mutation == "wrong_ttl":
                result["ttl"] = True
            else:
                result["connect"] += "#fragment"

        http.corrupt = corrupt
        with pytest.raises(BrowserProviderError, match="invalid_response"):
            await BrowserlessWire(deployment(), credential, http).create_session(None)
        assert len(http.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("body", [b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b"{", b"\xff"])
def test_strict_json_rejects_ambiguity_without_body_leak(body) -> None:
    with pytest.raises(BrowserProviderError, match="malformed"):
        parse_object(body, 100, "restore")


def test_body_limit_and_content_type_checked() -> None:
    class Large:
        async def request(self, *args):
            return WireResponse(200, b" " * 40 + b"{}")

    async def run():
        wire = BrowserlessWire(
            deployment().model_copy(update={"max_response_bytes": 40}),
            uuid4().hex,
            Large(),
        )
        with pytest.raises(BrowserProviderError, match="budget"):
            await wire.request("GET", wire.url("/profile/fixture"), None, "capture")

    asyncio.run(run())
