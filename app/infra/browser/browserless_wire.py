"""Bounded vendor wire. Credentials and returned URLs never enter neutral models."""

from __future__ import annotations

import asyncio
import http.client
import json
import re
import ssl
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, TypeAlias, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, ValidationError

from app.browser_skill.models import BrowserFailure, Contract
from app.infra.browser.deployment_manifest import BrowserDeployment

Phase: TypeAlias = Literal[
    "acquire", "restore", "observe", "dispatch", "capture", "release", "terminate"
]
Code: TypeAlias = Literal[
    "unavailable",
    "unsupported",
    "denied",
    "stale",
    "subject_mismatch",
    "invalid_response",
    "timeout",
    "cancelled",
    "effect_unknown",
    "quarantined",
    "overloaded",
    "invalid_request",
    "resource_not_found",
]
Reason: TypeAlias = Literal[
    "disabled",
    "transport",
    "bad_input",
    "unauthenticated",
    "forbidden",
    "not_found",
    "deadline",
    "capacity",
    "malformed",
    "redirect",
    "budget",
    "source",
    "binding",
    "subject",
    "state",
    "unsupported",
    "profile_loss",
    "proof",
    "cancelled",
]


class BrowserProviderError(Exception):
    def __init__(
        self,
        code: Code,
        phase: Phase,
        reason: Reason,
        *,
        sent: bool = False,
        cleanup: bool = False,
    ) -> None:
        self.failure = BrowserFailure(
            code=code,
            phase=phase,
            dispatch_state="possibly_sent" if sent else "not_sent",
            cleanup_required=cleanup,
        )
        self.reason = reason
        super().__init__(f"browser_{code}:{phase}:{reason}")


def invalid(phase: Phase, reason: Reason = "malformed") -> BrowserProviderError:
    return BrowserProviderError("invalid_response", phase, reason, sent=True, cleanup=True)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def parse_object(data: bytes, limit: int, phase: Phase) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError("nonfinite")

    try:
        if len(data) > limit:
            raise invalid(phase, "budget")
        result = json.loads(data, object_pairs_hook=unique, parse_constant=reject_constant)
        if not isinstance(result, dict):
            raise ValueError("object")
        return cast(dict[str, object], result)
    except (ValueError, UnicodeError, RecursionError):
        raise invalid(phase) from None


@dataclass(frozen=True, repr=False)
class WireResponse:
    status: int
    body: bytes = field(repr=False)
    content_type: str = "application/json"


class HTTPTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        timeout: float,
        limit: int,
    ) -> WireResponse: ...


class HTTPSBrowserlessTransport:
    """No redirect/proxy/env handling and no library HTTP request logging.

    A cancelled caller may have sent its request. The bounded worker closes its
    connection in finally; the provider retains the remote capacity until proof.
    """

    async def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        timeout: float,
        limit: int,
    ) -> WireResponse:
        return await asyncio.to_thread(self._request, method, url, body, timeout, limit)

    @staticmethod
    def _request(
        method: str,
        url: str,
        body: bytes | None,
        timeout: float,
        limit: int,
    ) -> WireResponse:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("browser_http_scheme")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            port=parsed.port,
            timeout=timeout,
            context=context,
        )
        connection.set_debuglevel(0)
        try:
            connection.request(
                method,
                urlunsplit(("", "", parsed.path, parsed.query, "")),
                body,
                {"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = connection.getresponse()
            data = response.read(limit + 1)
            return WireResponse(response.status, data, response.getheader("Content-Type", ""))
        finally:
            connection.close()


class SessionWire(Contract):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")] = Field(repr=False)
    connect: str = Field(repr=False, max_length=8192)
    stop: str = Field(repr=False, max_length=8192)
    browserQL: str = Field(repr=False, max_length=8192)
    ttl: Annotated[int, Field(gt=0)]
    cloudEndpointId: str | None = Field(repr=False)


class BrowserlessWire:
    def __init__(
        self,
        deployment: BrowserDeployment,
        credential: str,
        transport: HTTPTransport,
    ) -> None:
        if not credential or len(credential) > 4096 or any(c in credential for c in "\r\n\t"):
            raise BrowserProviderError("denied", "acquire", "unauthenticated")
        self.deployment = deployment
        self._credential = credential
        self._transport = transport

    def url(self, path: str, *, websocket: bool = False, profile: str | None = None) -> str:
        query = {"token": self._credential}
        if profile is not None:
            query["profile"] = profile
        base = self.deployment.endpoint_origin
        if websocket:
            base = "wss://" + urlsplit(base).netloc
        return base + path + "?" + urlencode(query)

    def validate_returned_url(self, value: str, path: str, *, websocket: bool = False) -> None:
        try:
            parsed = urlsplit(value)
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            if (
                parsed.scheme != ("wss" if websocket else "https")
                or parsed.netloc != urlsplit(self.deployment.endpoint_origin).netloc
                or parsed.path != path
                or parsed.fragment
                or parsed.username
                or parsed.password
                or any(c in value for c in "\\\r\n\t")
                or len(pairs) != 1
                or pairs != [("token", self._credential)]
            ):
                raise ValueError("url")
        except ValueError:
            raise invalid("restore") from None

    async def request(
        self,
        method: str,
        url: str,
        payload: object,
        phase: Phase,
        *,
        acknowledgement: bool = False,
    ) -> dict[str, object]:
        body = None if payload is None else json_bytes(payload)
        if body is not None and len(body) > self.deployment.max_response_bytes:
            raise BrowserProviderError("unsupported", phase, "budget")
        try:
            async with asyncio.timeout(self.deployment.timeout_seconds):
                response = await self._transport.request(
                    method,
                    url,
                    body,
                    self.deployment.timeout_seconds,
                    self.deployment.max_response_bytes,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise BrowserProviderError(
                "timeout", phase, "deadline", sent=True, cleanup=True
            ) from None
        except Exception:
            raise BrowserProviderError(
                "unavailable",
                phase,
                "transport",
                sent=True,
                cleanup=True,
            ) from None
        if 300 <= response.status < 400:
            raise invalid(phase, "redirect")
        if acknowledgement and 200 <= response.status < 300:
            # An ACK has no authority to free capacity. Only the proof callback does.
            return {}
        mappings: dict[int, tuple[Code, Reason]] = {
            400: ("invalid_request", "bad_input"),
            401: ("denied", "unauthenticated"),
            403: ("denied", "forbidden"),
            404: ("resource_not_found", "not_found"),
            408: ("timeout", "deadline"),
            429: ("overloaded", "capacity"),
            500: ("unavailable", "transport"),
            503: ("unavailable", "transport"),
        }
        if response.status != 200:
            code, reason = mappings.get(response.status, ("invalid_response", "malformed"))
            raise BrowserProviderError(code, phase, reason, sent=True, cleanup=True)
        if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise invalid(phase)
        return parse_object(response.body, self.deployment.max_response_bytes, phase)

    async def create_session(self, profile: str | None) -> SessionWire:
        payload: dict[str, object] = {"ttl": self.deployment.ttl_ms, "browser": "chromium"}
        if profile is not None:
            payload["profile"] = profile
        result = await self.request("POST", self.url("/session"), payload, "restore")
        try:
            session = SessionWire.model_validate(result)
        except ValidationError:
            raise invalid("restore") from None
        self.validate_returned_url(
            session.connect, f"/session/connect/{session.id}", websocket=True
        )
        self.validate_returned_url(session.stop, f"/session/{session.id}")
        self.validate_returned_url(session.browserQL, f"/session/bql/{session.id}")
        if session.ttl != self.deployment.ttl_ms:
            raise invalid("restore")
        return session

    async def upload(self, name: str, state: dict[str, object]) -> dict[str, object]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", name):
            raise BrowserProviderError("unsupported", "capture", "bad_input")
        return await self.request(
            "POST",
            self.url("/profile/upload"),
            {"name": name, "state": state},
            "capture",
        )

    async def stop(self, session: SessionWire) -> None:
        self.validate_returned_url(session.stop, f"/session/{session.id}")
        await self.request(
            "DELETE",
            session.stop + "&force=true",
            None,
            "terminate",
            acknowledgement=True,
        )
