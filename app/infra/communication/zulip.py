"""The only backend adapter that understands Zulip 12.3 APIs and identifiers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import re
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from time import monotonic
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, quote, unquote, urlencode
from uuid import UUID

import anyio
import httpx2
from pydantic import SecretStr

from app.config import ZulipSettings
from app.infra.mcp.transport import seal_vendor_logs
from app.ports.communication import (
    AuthenticatedInboundMessage,
    DeliveryMessage,
    DeliveryResult,
    IMAccountBinding,
    IMLoginForm,
    IMMessage,
    IMReadContext,
    IMReadIdentity,
    IMSelection,
    InboundEvent,
    VerifiedInboundMessage,
)
from app.ports.im_client import IMClientError, IMClientRequest, IMClientResponse
from app.ports.im_context import IMContextError

_CLIENT_LOG = logging.getLogger("uvicorn.error.im_client")
_CLIENT_ENDPOINTS = (
    ("/register", "POST"),
    ("/events", "GET DELETE"),
    ("/messages", "GET POST"),
    ("/messages/{id}", "PATCH DELETE"),
    ("/messages/flags", "POST"),
    ("/messages/{id}/reactions", "POST DELETE"),
    ("/messages/{id}/read_receipts", "GET"),
    ("/drafts", "GET POST"),
    ("/drafts/{id}", "PATCH DELETE"),
    ("/users", "GET"),
    ("/users/{id}", "GET"),
    ("/users/{id}/channels", "GET"),
    ("/realm/profile_fields", "GET"),
    ("/streams/{id}", "GET"),
    ("/streams/{id}/members", "GET"),
    ("/users/me/{id}/topics", "GET"),
    ("/realm/emoji", "GET"),
    ("/user_groups", "GET"),
    ("/channels/create", "POST"),
    ("/users/me/subscriptions", "POST DELETE"),
    ("/users/me/subscriptions/properties", "POST"),
    ("/users/me/presence", "POST"),
    ("/user_uploads", "POST"),
)
_CLIENT_PATTERNS = tuple(
    (template, re.compile(re.escape(template).replace(r"\{id\}", r"[1-9][0-9]*")),
     frozenset(methods.split())) for template, methods in _CLIENT_ENDPOINTS
)
_FILE_HEADERS = frozenset({
    "content-range", "accept-ranges", "content-type", "content-disposition", "content-length",
})
_EMOJI_PATH = re.compile(r"/user_avatars/([1-9][0-9]*)/emoji/images/(?:still/)?[^/]+")
_THUMBNAIL_FORMATS = frozenset({
    "840x560.webp", "840x560-anim.webp", "4032x3024.webp", "3024x4032.webp",
})


def _endpoint_denied() -> IMClientError:
    return IMClientError("im_endpoint_not_allowed", 404, "IM endpoint is not allowed.")


def _client_protocol_error() -> IMClientError:
    return IMClientError("im_upstream:SERVER_ERROR", 502, "Invalid IM upstream response.")


def _client_path(path: str) -> str:
    """Validate before URL libraries normalize dot segments or encoded separators."""
    current = path
    while True:
        if (not current.startswith("/") or "//" in current
                or any(ch in current for ch in "\\?#")
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in current)
                or any(segment in (".", "..") for segment in current.split("/"))):
            raise _endpoint_denied()
        decoded = unquote(current)
        if decoded == current:
            return decoded
        if decoded.count("/") != current.count("/"):
            raise _endpoint_denied()
        current = decoded


def _match_client_endpoint(method: str, path: str) -> str:
    decoded = _client_path(path)
    for template, pattern, methods in _CLIENT_PATTERNS:
        if method in methods and pattern.fullmatch(decoded):
            return template
    raise _endpoint_denied()


def _client_form(raw: bytes) -> dict[str, str]:
    # Django QueryDict: UTF-8 bytes, ISO-8859-1 fallback, then UTF-8 parse_qsl.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("iso-8859-1")
    return dict(parse_qsl(text, keep_blank_values=True, encoding="utf-8"))


async def _client_bytes(body: bytes) -> AsyncIterator[bytes]:
    yield body


@dataclass(slots=True)
class _ClientScope:
    queues: dict[str, float] = field(default_factory=dict, repr=False)
    avatars: set[tuple[str, bytes]] = field(default_factory=set, repr=False)
    emoji_realms: set[str] = field(default_factory=set, repr=False)


@dataclass(slots=True)
class _ClientCredentials:
    email: str = field(repr=False)
    key: str = field(repr=False)
    user_id: int
    scopes: dict[str, _ClientScope] = field(default_factory=dict, repr=False)


def _match_file_path(method: str, path: str, query: bytes, scope: _ClientScope) -> str:
    decoded = _client_path(path)
    if method != "GET":
        raise _endpoint_denied()
    if re.fullmatch(r"/user_uploads/(?:download/)?[1-9][0-9]*/.+", decoded):
        return "file_upload"
    thumbnail = re.fullmatch(r"/user_uploads/thumbnail/[1-9][0-9]*/.+/([^/]+)", decoded)
    if thumbnail is not None and thumbnail[1] in _THUMBNAIL_FORMATS:
        return "file_thumbnail"
    if decoded == "/thumbnail":
        target = _client_form(query).get("url", "")
        if re.fullmatch(r"user_uploads/[1-9][0-9]*/.+", target):
            _client_path("/" + target)
            return "file_thumbnail_legacy"
    if re.fullmatch(r"/avatar/[1-9][0-9]*(?:/medium)?", decoded):
        return "file_avatar"
    if decoded.startswith("/user_avatars/"):
        if (decoded, query) in scope.avatars:
            return "file_avatar_resource"
        emoji = _EMOJI_PATH.fullmatch(decoded)
        if emoji is not None and emoji[1] in scope.emoji_realms:
            return "file_emoji"
    raise _endpoint_denied()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _source_conversation(raw: dict[str, Any]) -> tuple[str, Literal["private", "group"]]:
    """Use the same exact topic and full participant set for callback and rereads."""
    if raw.get("type") == "stream":
        if type(raw.get("stream_id")) is not int or not isinstance(raw.get("subject"), str):
            raise IMContextError("im_source_changed")
        return _canonical({
            "type": "stream", "stream_id": raw["stream_id"], "topic": raw["subject"],
        }), "group"
    recipients = raw.get("display_recipient")
    if (raw.get("type") != "private" or not isinstance(recipients, list)
            or not recipients or any(
                not isinstance(user, dict) or type(user.get("id")) is not int
                or user["id"] <= 0 for user in recipients
            )):
        raise IMContextError("im_source_changed")
    users = sorted({user["id"] for user in recipients})
    if raw.get("sender_id") not in users:
        raise IMContextError("im_source_changed")
    return _canonical({"type": "private", "user_ids": users}), (
        "private" if len(users) == 2 else "group"
    )


def _inbound_event(
    raw: object, *, instance: str, realm: str, target_bot_id: str,
    trigger: object,
) -> InboundEvent:
    if (not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] <= 0
            or type(raw.get("sender_id")) is not int or raw["sender_id"] <= 0
            or not isinstance(raw.get("content"), str)
            or raw.get("content_type") != "text/x-markdown"
            or trigger not in ("direct_message", "mention")):
        raise IMContextError("im_source_changed")
    if ((raw.get("type") == "stream" and trigger != "mention")
            or (raw.get("type") == "private" and trigger != "direct_message")):
        raise IMContextError("im_source_changed")
    conversation, kind = _source_conversation(raw)
    if raw["type"] == "private" and int(target_bot_id) not in json.loads(conversation)["user_ids"]:
        raise IMContextError("im_source_changed")
    content = raw["content"].replace("\r\n", "\n").replace("\r", "\n")
    fingerprint = hashlib.sha256(_canonical({
        "provider": "zulip", "instance": instance, "realm": realm,
        "target_bot": target_bot_id, "source_message_id": str(raw["id"]),
        "trigger": trigger, "sender": str(raw["sender_id"]),
        "conversation": conversation,
        "source_version": {name: raw.get(name) for name in (
            "timestamp", "last_edit_timestamp", "last_moved_timestamp", "edit_history",
        )},
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }).encode()).digest()
    return InboundEvent(
        provider="zulip", im_instance_id=instance, realm_id=realm,
        target_bot_id=target_bot_id, source_message_id=str(raw["id"]),
        trigger_kind=trigger,
        sender_id=str(raw["sender_id"]), conversation_ref=conversation,
        conversation_kind=kind, payload_fingerprint=fingerprint,
    )


@dataclass(slots=True)
class _Batch:
    client: httpx2.AsyncClient = field(repr=False)
    user_id: int
    human_active: bool


class _DelegationMarkup(HTMLParser):
    """Read executable text and real user mentions from Zulip-rendered HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[tuple[str, bool]] = []
        self._first_paragraph = False
        self._paragraph_done = False
        self._text: list[str] = []
        self.mentions: set[str] = set()
        self.mention_text: dict[str, str] = {}
        self._mention_id: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        excluded = (any(hidden for _, hidden in self._stack)
                    or tag in ("blockquote", "pre", "code"))
        mention = tag == "span" and "user-mention" in classes
        if mention and not excluded and "silent" not in classes:
            uid = attributes.get("data-user-id")
            if uid is not None and uid.isascii() and uid.isdecimal() and int(uid) > 0:
                self.mentions.add(uid)
                self._mention_id = uid
                self.mention_text[uid] = ""
        if tag == "p" and not self._stack and not self._paragraph_done:
            self._first_paragraph = True
        if tag == "br" and self._first_paragraph and not excluded:
            self._text.append("\n")
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"):
            self._stack.append((tag, excluded or mention))

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self._mention_id = None
        if self._stack and self._stack[-1][0] == tag:
            self._stack.pop()
            if tag == "p" and not self._stack and self._first_paragraph:
                self._first_paragraph = False
                self._paragraph_done = True

    def handle_data(self, data: str) -> None:
        if self._mention_id is not None:
            self.mention_text[self._mention_id] += data
        if self._first_paragraph and not any(hidden for _, hidden in self._stack):
            self._text.append(data)

    def delegation_mentions(self) -> frozenset[str] | None:
        text = "".join(self._text).strip()
        if (not text.startswith("委托") or len(text) < 3
                or not (text[2].isspace() or text[2] in (":", "："))
                or not text[3:].strip()):
            return None
        return frozenset(self.mentions)

    def question(self, content: str, bot_ids: tuple[str, str]) -> str:
        routes = "|".join(
            r"@\*\*" + re.escape(self.mention_text[bot_id].removeprefix("@"))
            + r"(?:\|" + bot_id + r")?\*\*" for bot_id in bot_ids
        )
        route = "(?:" + routes + ")"
        prefix = (
            rf"^\s*(?:{route}\s*)*"
            rf"(?:委托(?=\s|[:：]|{route})[:：]?\s*(?:{route}\s*)*)?"
        )
        return re.sub(prefix, "", content, count=1)


class ZulipCommunicationAdapter:
    def __init__(self, settings: ZulipSettings) -> None:
        self._settings = settings
        self._tls = ssl.create_default_context(cafile=settings.tls_ca_file)
        seal_vendor_logs()
        self._client = httpx2.AsyncClient(
            verify=self._tls, follow_redirects=False, trust_env=False, timeout=15,
            headers={"Accept-Encoding": "identity"},
        )

    def _client_url(self, path: str, query: bytes = b"") -> httpx2.URL:
        return httpx2.URL(self._settings.origin + path).copy_with(query=query)

    async def _client_send(
        self, method: str, url: httpx2.URL, *,
        credentials: _ClientCredentials | None, headers: Mapping[str, str] | None = None,
        content: bytes | AsyncIterator[bytes] = b"", timeout: httpx2.Timeout | float = 15,
    ) -> httpx2.Response:
        request = self._client.build_request(
            method, url, headers=headers, content=content, timeout=timeout,
        )
        # A shared pool may receive Set-Cookie; none may reach any subsequent request.
        request.headers.pop("cookie", None)
        auth = (httpx2.BasicAuth(credentials.email, credentials.key)
                if credentials is not None else None)
        return await self._client.send(request, stream=True, auth=auth, follow_redirects=False)

    @staticmethod
    async def _client_close(response: httpx2.Response) -> None:
        with anyio.CancelScope(shield=True):
            await response.aclose()

    @staticmethod
    def _client_json(response: httpx2.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise _client_protocol_error() from None
        if not isinstance(payload, dict):
            raise _client_protocol_error()
        return cast(dict[str, Any], payload)

    def _client_error(
        self, response: httpx2.Response, credentials: _ClientCredentials | None, *,
        secrets: tuple[str, ...] = (),
    ) -> IMClientError:
        status = response.status_code
        default = "UNAUTHORIZED" if status == 401 else (
            "SERVER_ERROR" if status >= 500 else "BAD_REQUEST"
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        code, message = default, "IM upstream request failed."
        if isinstance(payload, dict):
            if isinstance(payload.get("code"), str) and payload["code"]:
                code = payload["code"]
            if isinstance(payload.get("msg"), str) and payload["msg"]:
                message = payload["msg"]
        if credentials is not None:
            basic = base64.b64encode(
                (credentials.email + ":" + credentials.key).encode()
            ).decode()
            secrets += (credentials.key, basic, "Basic " + basic)
        for secret in secrets:
            for value in (secret, quote(secret, safe="")):
                if value:
                    code, message = code.replace(value, "[redacted]"), message.replace(
                        value, "[redacted]",
                    )
        headers = {
            name: value for name, value in response.headers.items()
            if name in _FILE_HEADERS - {"content-type", "content-length"}
        }
        return IMClientError(
            "im_upstream:" + code, 502 if status == 401 or status >= 500 else status,
            message, invalidate_credentials=status == 401, headers=headers,
        )

    async def client_identity(self, binding: IMAccountBinding) -> IMReadIdentity:
        token, _ = self._token(binding)
        try:
            # The only JWT/key handshake in the new client. It is never replayed.
            response = await self._client_send(
                "POST", self._client_url("/api/v1/jwt/fetch_api_key"), credentials=None,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urlencode({"token": token}).encode(),
            )
            try:
                await response.aread()
                if response.status_code >= 300:
                    if response.status_code < 400:
                        raise _client_protocol_error()
                    raise self._client_error(response, None, secrets=(token,))
                payload = self._client_json(response)
                key, email = payload.get("api_key"), payload.get("email")
                if not isinstance(key, str) or not key or not isinstance(email, str) or not email:
                    raise _client_protocol_error()
                credentials = _ClientCredentials(email, key, 0)
            finally:
                await self._client_close(response)
            response = await self._client_send(
                "GET", self._client_url("/api/v1/users/me"), credentials=credentials,
            )
            try:
                await response.aread()
                if response.status_code >= 300:
                    if response.status_code < 400:
                        raise _client_protocol_error()
                    raise self._client_error(response, credentials, secrets=(token,))
                me = self._client_json(response)
                uid = me.get("user_id")
                if (type(uid) is not int or uid <= 0 or str(uid) != binding.im_user_id
                        or me.get("is_bot") is not False or me.get("is_active") is not True):
                    raise IMClientError("im_identity_mismatch", 409, "IM identity does not match.")
                credentials.user_id = uid
                return IMReadIdentity(
                    "zulip", self._settings.im_instance_id, self._settings.realm_id,
                    str(uid), credentials,
                )
            finally:
                await self._client_close(response)
        except (httpx2.TimeoutException, httpx2.NetworkError):
            raise IMClientError("im_unavailable", 504, "IM upstream is unavailable.") from None
        except (httpx2.RemoteProtocolError, httpx2.DecodingError):
            raise _client_protocol_error() from None

    def _client_resource(self, value: object) -> tuple[str, bytes] | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            url = httpx2.URL(value)
            if url.is_relative_url:
                url = httpx2.URL(self._settings.origin + "/").join(url)
            origin = httpx2.URL(self._settings.origin)
            if (url.scheme, url.host, url.port) != (origin.scheme, origin.host, origin.port):
                return None
            raw_path = url.raw_path.split(b"?", 1)[0].decode("ascii")
            return _client_path(raw_path), url.query
        except (httpx2.InvalidURL, UnicodeDecodeError, IMClientError):
            return None

    def _client_resources(
        self, endpoint: str, payload: dict[str, Any], scope: _ClientScope,
    ) -> None:
        def avatar(user: object) -> None:
            if not isinstance(user, dict):
                return
            for name in ("avatar_url", "avatar_url_medium"):
                resource = self._client_resource(user.get(name))
                if resource is not None and resource[0].startswith("/user_avatars/"):
                    scope.avatars.add(resource)

        def emoji(items: object) -> None:
            if not isinstance(items, dict):
                return
            for item in items.values():
                if not isinstance(item, dict):
                    continue
                for name in ("source_url", "still_url"):
                    resource = self._client_resource(item.get(name))
                    match = _EMOJI_PATH.fullmatch(resource[0]) if resource is not None else None
                    if match is not None:
                        scope.emoji_realms.add(match[1])

        if endpoint == "/register":
            for user in payload.get("realm_users", []):
                avatar(user)
            emoji(payload.get("realm_emoji"))
        elif endpoint == "/users":
            for user in payload.get("members", []):
                avatar(user)
        elif endpoint == "/users/{id}":
            avatar(payload.get("user"))
        elif endpoint == "/realm/emoji":
            emoji(payload.get("emoji"))
        if endpoint in ("/register", "/events"):
            for event in payload.get("events", []):
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "realm_user":
                    avatar(event.get("person"))
                elif event.get("type") == "realm_emoji":
                    emoji(event.get("realm_emoji"))

    @staticmethod
    async def _client_file_body(response: httpx2.Response) -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        except (httpx2.TimeoutException, httpx2.NetworkError):
            raise IMClientError("im_unavailable", 504, "IM upstream is unavailable.") from None
        except (httpx2.RemoteProtocolError, httpx2.DecodingError):
            raise _client_protocol_error() from None

    @asynccontextmanager
    async def _client_file(
        self, request: IMClientRequest, credentials: _ClientCredentials,
        revalidate: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[IMClientResponse]:
        url = self._client_url(request.path, request.query)
        origin = httpx2.URL(self._settings.origin)
        headers = {k: v for k, v in request.headers.items() if k in ("range", "accept")}
        response: httpx2.Response | None = None
        try:
            for hop in range(self._client.max_redirects + 1):
                same_origin = (url.scheme, url.host, url.port) == (
                    origin.scheme, origin.host, origin.port,
                )
                response = await self._client_send(
                    "GET", url, credentials=credentials if same_origin else None,
                    headers=headers, timeout=httpx2.Timeout(60, connect=10, pool=10),
                )
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                location = response.headers.get("location")
                if location is None or hop == self._client.max_redirects:
                    raise _client_protocol_error()
                target = url.join(location)
                if target.scheme != "https" or target.userinfo:
                    raise _client_protocol_error()
                target_path = _client_path(target.raw_path.split(b"?", 1)[0].decode("ascii"))
                if target_path.startswith(("/static/", "/accounts/", "/login")):
                    raise _client_protocol_error()
                if (target.scheme, target.host, target.port) == (
                    origin.scheme, origin.host, origin.port,
                ) and not (
                    re.fullmatch(r"/user_uploads/(?:temporary/|download/|thumbnail/)?[^/]+/.+",
                                 target_path)
                    or target_path.startswith("/user_avatars/")
                    or re.fullmatch(r"/avatar/[1-9][0-9]*(?:/medium)?", target_path)
                    or target_path == "/thumbnail"
                ):
                    raise _client_protocol_error()
                await revalidate()
                await self._client_close(response)
                response = None
                url = target
            assert response is not None
            if response.status_code not in (200, 206):
                await response.aread()
                if response.status_code < 400:
                    raise _client_protocol_error()
                raise self._client_error(response, credentials)
            if (response.headers.get("content-type", "").split(";", 1)[0] == "text/html"
                    and request.path.startswith("/avatar/")):
                raise _client_protocol_error()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise _client_protocol_error()
            yield IMClientResponse(
                response.status_code,
                {k: v for k, v in response.headers.items() if k in _FILE_HEADERS},
                self._client_file_body(response),
            )
        except (httpx2.InvalidURL, UnicodeDecodeError):
            raise _client_protocol_error() from None
        finally:
            if response is not None:
                await self._client_close(response)

    @asynccontextmanager
    async def request(
        self, identity: IMReadIdentity, request: IMClientRequest, *,
        revalidate: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[IMClientResponse]:
        credentials = cast(_ClientCredentials, identity.handle)
        scope = credentials.scopes.setdefault(request.session_scope, _ClientScope())
        endpoint = "rejected"
        started = monotonic()
        status = mapped_status = 0
        code = "ok"
        try:
            if request.kind == "file":
                endpoint = _match_file_path(request.method, request.path, request.query, scope)
                async with self._client_file(
                    request, credentials, revalidate,
                ) as result:
                    status = mapped_status = result.status
                    yield result
                return
            endpoint = _match_client_endpoint(request.method, request.path)
            media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            body: bytes | AsyncIterator[bytes] = request.body
            form: dict[str, str] = {}
            if media == "application/x-www-form-urlencoded":
                body = b"".join([part async for part in request.body])
                form = _client_form(body)
                if request.method == "POST" and "method" in form:
                    _match_client_endpoint(form["method"], request.path)
            elif media == "multipart/form-data" and endpoint != "/user_uploads":
                raise _endpoint_denied()
            effective_method = form.get("method", request.method) if request.method == "POST" else (
                request.method
            )
            queue_id = ""
            read, deadline = 15.0, 15.0
            upload = endpoint == "/user_uploads"
            if endpoint == "/events":
                params = _client_form(request.query) if request.method == "GET" else {
                    **_client_form(request.query), **form,
                }
                queue_id = params.get("queue_id", "")
                if queue_id not in scope.queues:
                    raise IMClientError(
                        "im_upstream:BAD_EVENT_QUEUE_ID", 400, "Event queue is unavailable.",
                    )
                if request.method == "GET":
                    actual = scope.queues[queue_id]
                    read, deadline = actual + 15, actual + 45
                    _CLIENT_LOG.info(
                        "im_client stage=upstream endpoint=events poll=%s read=%s deadline=%s",
                        actual, read, deadline,
                    )
            if upload:
                read = 60.0
            headers = {k: v for k, v in request.headers.items()
                       if k in ("content-type", "content-length", "accept")}
            timeout = httpx2.Timeout(read=read, write=60 if upload else 10, connect=10, pool=10)
            async with asyncio.timeout(None if upload else deadline):
                # Recheck after reading a form or waiting for any upstream work.
                await revalidate()
                response = await self._client_send(
                    request.method, self._client_url("/api/v1" + request.path, request.query),
                    credentials=credentials, headers=headers, content=body, timeout=timeout,
                )
                try:
                    status = response.status_code
                    await response.aread()
                    if status >= 400:
                        if upload and status == 405:
                            try:
                                dispatch = response.json()
                            except ValueError:
                                dispatch = None
                            if (isinstance(dispatch, dict) and dispatch.get("result") == "error"
                                    and dispatch.get("msg") == "Method Not Allowed"
                                    and dispatch.get("allowed_methods") == ["POST"]):
                                raise _endpoint_denied()
                        error = self._client_error(response, credentials)
                        if endpoint == "/events" and error.code == "im_upstream:BAD_EVENT_QUEUE_ID":
                            scope.queues.pop(queue_id, None)
                        raise error
                    if status >= 300:
                        raise _client_protocol_error()
                    payload = self._client_json(response)
                    if endpoint == "/register":
                        qid = payload.get("queue_id")
                        poll_timeout = payload.get("event_queue_longpoll_timeout_seconds")
                        if (str(payload.get("user_id")) != identity.im_user_id):
                            raise IMClientError(
                                "im_identity_mismatch", 409, "IM identity does not match.",
                            )
                        if (not isinstance(qid, str) or not qid
                                or not isinstance(poll_timeout, (int, float))
                                or isinstance(poll_timeout, bool)
                                or not math.isfinite(poll_timeout) or poll_timeout <= 0):
                            raise _client_protocol_error()
                        scope.queues[qid] = float(poll_timeout)
                    if endpoint == "/events" and effective_method == "DELETE":
                        scope.queues.pop(queue_id, None)
                    if upload and not isinstance(payload.get("url"), str):
                        raise _client_protocol_error()
                    self._client_resources(endpoint, payload, scope)
                    mapped_status = status
                    result = IMClientResponse(
                        status, {"content-type": response.headers.get(
                            "content-type", "application/json",
                        )}, _client_bytes(response.content),
                    )
                finally:
                    await self._client_close(response)
            yield result
        except (httpx2.TimeoutException, httpx2.NetworkError, TimeoutError) as error:
            code = ("timeout" if isinstance(error, (httpx2.TimeoutException, TimeoutError))
                    else "network")
            mapped_status = 504
            raise IMClientError("im_unavailable", 504, "IM upstream is unavailable.") from None
        except (httpx2.RemoteProtocolError, httpx2.DecodingError):
            code, mapped_status = "protocol", 502
            raise _client_protocol_error() from None
        except IMClientError as error:
            code = "im_upstream" if error.code.startswith("im_upstream:") else error.code
            mapped_status = error.http_status
            raise
        finally:
            _CLIENT_LOG.info(
                "im_client stage=upstream endpoint=%s method=%s status=%s mapped_status=%s "
                "code=%s duration_ms=%s", endpoint, request.method, status,
                mapped_status, code, int((monotonic() - started) * 1000),
            )

    def release_scope(self, identity: IMReadIdentity, session_scope: str) -> None:
        cast(_ClientCredentials, identity.handle).scopes.pop(session_scope, None)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _token(self, binding: IMAccountBinding) -> tuple[str, datetime]:
        expires = datetime.now(UTC) + timedelta(seconds=60)
        header = _b64(b'{"alg":"HS256","typ":"JWT"}')
        payload = _b64(
            json.dumps(
                {
                    "email": binding.login_subject,
                    "iat": int(datetime.now(UTC).timestamp()),
                    "exp": int(expires.timestamp()),
                },
                separators=(",", ":"),
            ).encode()
        )
        unsigned = header + "." + payload
        signature = hmac.new(self._settings.jwt_secret, unsigned.encode(), hashlib.sha256).digest()
        return unsigned + "." + _b64(signature), expires

    def build_login_form(self, binding: IMAccountBinding) -> IMLoginForm:
        token, expires = self._token(binding)
        return IMLoginForm(
            self._settings.origin + "/accounts/login/jwt/", {"token": token}, expires
        )

    @asynccontextmanager
    async def current_identity(self, binding: IMAccountBinding) -> AsyncIterator[IMReadIdentity]:
        async with httpx2.AsyncClient(
            base_url=self._settings.origin,
            verify=self._tls,
            follow_redirects=False,
            trust_env=False,
            timeout=15,
        ) as client:
            token, _ = self._token(binding)
            credentials = await self._request(
                client, "POST", "/api/v1/jwt/fetch_api_key", data={"token": token}
            )
            key = credentials.get("api_key")
            email = credentials.get("email")
            if not isinstance(key, str) or not key or not isinstance(email, str) or not email:
                raise IMContextError("im_identity_unavailable", 503)
            client.auth = httpx2.BasicAuth(email, key)
            me = await self._request(client, "GET", "/api/v1/users/me")
            uid = me.get("user_id")
            if type(uid) is not int or uid <= 0:
                raise IMContextError("im_identity_unavailable", 503)
            yield IMReadIdentity(
                "zulip",
                self._settings.im_instance_id,
                self._settings.realm_id,
                str(uid),
                _Batch(client, uid, me.get("is_bot") is False and me.get("is_active") is True),
            )

    async def _request(
        self,
        client: httpx2.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, path, **kwargs)
            if response.status_code in (400, 401, 403, 404):
                raise IMContextError("im_source_not_found", 404)
            if response.status_code != 200:
                raise IMContextError("im_upstream_unavailable", 503)
            result = response.json()
        except (httpx2.RequestError, ValueError):
            raise IMContextError("im_upstream_unavailable", 503) from None
        if not isinstance(result, dict) or result.get("result") != "success":
            raise IMContextError("im_upstream_unavailable", 503)
        return cast(dict[str, Any], result)

    @staticmethod
    def _number(value: str) -> int:
        if not value.isascii() or not value.isdecimal() or len(value) > 20 or int(value) <= 0:
            raise IMContextError("im_source_not_found", 404)
        return int(value)

    def _conversation(self, raw: dict[str, Any], uid: int) -> str:
        if raw.get("type") == "stream":
            if type(raw.get("stream_id")) is not int or not isinstance(raw.get("subject"), str):
                raise IMContextError("im_source_changed")
            conversation = {
                "type": "stream",
                "stream_id": raw["stream_id"],
                "topic": raw["subject"],
            }
        elif raw.get("type") == "private":
            recipients = raw.get("display_recipient")
            if (
                not isinstance(recipients, list)
                or not recipients
                or any(not isinstance(r, dict) or type(r.get("id")) is not int for r in recipients)
            ):
                raise IMContextError("im_source_changed")
            ids = sorted(r["id"] for r in recipients if r["id"] != uid) or [uid]
            conversation = {"type": "private", "to_user_ids": ids}
        else:
            raise IMContextError("im_source_changed")
        # Canonical JSON is opaque to EternalAI; the IM-side v2 adapter uses the same encoding.
        return json.dumps(conversation, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _message(self, raw: Any, selection: IMSelection, uid: int) -> IMMessage:
        if (
            not isinstance(raw, dict)
            or type(raw.get("id")) is not int
            or not isinstance(raw.get("content"), str)
        ):
            raise IMContextError("im_source_changed")
        conversation = self._conversation(raw, uid)
        try:
            provided = json.dumps(json.loads(selection.conversation_ref), sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=False)
        except ValueError:
            raise IMContextError("im_source_not_found", 404) from None
        if conversation != provided:
            raise IMContextError("im_source_not_found", 404)
        return IMMessage(
            str(raw["id"]),
            selection.conversation_ref,
            raw["content"].replace("\r\n", "\n").replace("\r", "\n"),
        )

    async def read_anchor(self, identity: IMReadIdentity, selection: IMSelection) -> IMMessage:
        batch = cast(_Batch, identity.handle)
        mid = self._number(selection.message_id)
        result = await self._request(
            batch.client, "GET", f"/api/v1/messages/{mid}", params={"apply_markdown": "false"}
        )
        return self._message(result.get("message"), selection, batch.user_id)

    async def read_context(self, identity: IMReadIdentity, selection: IMSelection) -> IMReadContext:
        batch = cast(_Batch, identity.handle)
        mid = self._number(selection.message_id)
        # The preceding anchor read established the canonical conversation; never accept raw narrow.
        conversation = json.loads(selection.conversation_ref)
        if conversation["type"] == "stream":
            narrow = [
                {"operator": "channel", "operand": conversation["stream_id"]},
                {"operator": "topic", "operand": conversation["topic"]},
            ]
            fragment = (
                "narrow/channel/"
                + str(conversation["stream_id"])
                + "/topic/"
                + "".join({"%": ".", ".": ".2E"}.get(c, c)
                          for c in quote(conversation["topic"], safe=""))
                + "/near/"
                + str(mid)
            )
        else:
            narrow = [{"operator": "dm", "operand": conversation["to_user_ids"]}]
            fragment = (
                "narrow/dm/" + ",".join(map(str, conversation["to_user_ids"])) + "/near/" + str(mid)
            )
        result = await self._request(
            batch.client,
            "GET",
            "/api/v1/messages",
            params={
                "anchor": str(mid),
                "num_before": "3",
                "num_after": "0",
                "include_anchor": "true",
                "narrow": json.dumps(narrow),
                "apply_markdown": "false",
            },
        )
        raw = result.get("messages")
        if not isinstance(raw, list) or any(
            not isinstance(m, dict) or type(m.get("id")) is not int or m["id"] > mid for m in raw
        ):
            raise IMContextError("im_source_changed")
        messages = tuple(self._message(m, selection, batch.user_id) for m in raw)
        return IMReadContext(
            messages, result.get("found_anchor") is True, self._settings.origin + "/#" + fragment
        )


class ZulipInboundAdapter:
    def __init__(
        self, reader: ZulipCommunicationAdapter, *,
        webhook_tokens: Mapping[str, SecretStr],
        bot_credentials: Mapping[str, tuple[str, SecretStr]],
    ) -> None:
        self._reader = reader
        self._tokens = webhook_tokens
        self._bots = bot_credentials

    async def check_identity(self, binding: IMAccountBinding) -> None:
        settings = self._reader._settings
        if (binding.status != "active" or (
            binding.provider, binding.im_instance_id, binding.realm_id,
        ) != ("zulip", settings.im_instance_id, settings.realm_id)):
            raise IMContextError("im_identity_changed")
        async with self._reader.current_identity(binding) as identity:
            if (identity.im_user_id != binding.im_user_id
                    or not cast(_Batch, identity.handle).human_active):
                raise IMContextError("im_identity_changed")

    def authenticate_callback(
        self, *, target_bot_id: str, payload: dict[str, object],
    ) -> AuthenticatedInboundMessage:
        token = payload.get("token")
        expected = self._tokens.get(target_bot_id)
        if (expected is None or not isinstance(token, str)
                or not hmac.compare_digest(token.encode(), expected.get_secret_value().encode())):
            raise IMContextError("im_inbound_authentication_failed", 401)
        settings = self._reader._settings
        event = _inbound_event(
            payload.get("message"), instance=settings.im_instance_id,
            realm=settings.realm_id, target_bot_id=target_bot_id, trigger=payload.get("trigger"),
        )
        return AuthenticatedInboundMessage(
            event, cast(dict[str, Any], payload["message"])["content"],
        )

    @asynccontextmanager
    async def _reader_message(
        self, binding: IMAccountBinding, event: InboundEvent,
    ) -> AsyncIterator[tuple[_Batch, dict[str, Any]]]:
        settings = self._reader._settings
        if (binding.status != "active" or (
            binding.provider, binding.im_instance_id, binding.realm_id, binding.im_user_id,
        ) != (event.provider, event.im_instance_id, event.realm_id, event.sender_id)
                or (event.provider, event.im_instance_id, event.realm_id)
                != ("zulip", settings.im_instance_id, settings.realm_id)):
            raise IMContextError("im_identity_changed")
        mid = self._reader._number(event.source_message_id)
        params = {"apply_markdown": "false", "allow_empty_topic_name": "true"}
        async with self._reader.current_identity(binding) as identity:
            if identity.im_user_id != binding.im_user_id:
                raise IMContextError("im_identity_changed")
            batch = cast(_Batch, identity.handle)
            if not batch.human_active:
                raise IMContextError("im_inbound_sender_invalid")
            result = await self._reader._request(
                batch.client, "GET", f"/api/v1/messages/{mid}", params=params,
            )
            raw = result.get("message")
            current = _inbound_event(
                raw, instance=settings.im_instance_id, realm=settings.realm_id,
                target_bot_id=event.target_bot_id, trigger=event.trigger_kind,
            )
            if current != event:
                raise IMContextError("im_source_changed")
            yield batch, cast(dict[str, Any], raw)

    async def delegation_mentions(
        self, binding: IMAccountBinding, event: InboundEvent,
    ) -> frozenset[str] | None:
        return (await self._delegation_markup(binding, event)).delegation_mentions()

    async def _delegation_markup(
        self, binding: IMAccountBinding, event: InboundEvent,
    ) -> _DelegationMarkup:
        async with self._reader_message(binding, event) as (batch, _):
            result = await self._reader._request(
                batch.client, "GET", f"/api/v1/messages/{event.source_message_id}",
                params={"apply_markdown": "true", "allow_empty_topic_name": "true"},
            )
            raw = result.get("message")
            raw_content = result.get("raw_content")
            if (not isinstance(raw, dict) or not isinstance(raw_content, str)
                    or not isinstance(raw.get("content"), str)
                    or raw.get("content_type") != "text/html"):
                raise IMContextError("im_source_changed")
            original = {**raw, "content": raw_content, "content_type": "text/x-markdown"}
            if _inbound_event(
                original, instance=event.im_instance_id, realm=event.realm_id,
                target_bot_id=event.target_bot_id, trigger=event.trigger_kind,
            ) != event:
                raise IMContextError("im_source_changed")
            parser = _DelegationMarkup()
            parser.feed(raw["content"])
            return parser

    async def _verify_bot(
        self, batch: _Batch, event: InboundEvent, bot_id: str,
    ) -> None:
        credentials = self._bots.get(bot_id)
        if credentials is None:
            raise IMContextError("im_bot_identity_unavailable", 503)
        email, api_key = credentials
        auth = httpx2.BasicAuth(email, api_key.get_secret_value())
        bot = await self._reader._request(batch.client, "GET", "/api/v1/users/me", auth=auth)
        if (str(bot.get("user_id")) != bot_id
                or bot.get("is_bot") is not True or bot.get("is_active") is not True):
            raise IMContextError("im_bot_identity_changed")
        bot_result = await self._reader._request(
            batch.client, "GET", f"/api/v1/messages/{event.source_message_id}", auth=auth,
            params={"apply_markdown": "false", "allow_empty_topic_name": "true"},
        )
        bot_raw = bot_result.get("message")
        target_event = _inbound_event(
            bot_raw, instance=event.im_instance_id, realm=event.realm_id,
            target_bot_id=event.target_bot_id, trigger=event.trigger_kind,
        )
        if target_event != event:
            raise IMContextError("im_source_changed")
        if event.conversation_kind == "group":
            flags = cast(dict[str, Any], bot_raw).get("flags")
            if not isinstance(flags, list) or "mentioned" not in flags:
                raise IMContextError("im_inbound_target_invalid")

    async def reread_trigger(
        self, binding: IMAccountBinding, event: InboundEvent,
        *, delegate_bot_id: str | None = None,
    ) -> VerifiedInboundMessage:
        async with self._reader_message(binding, event) as (batch, raw):
            # The reader established visibility. The Bot read only verifies its own
            # real recipient/mention flags; it cannot substitute for that reader.
            await self._verify_bot(batch, event, event.target_bot_id)
            if delegate_bot_id is not None:
                await self._verify_bot(batch, event, delegate_bot_id)
            question = raw["content"].replace("\r\n", "\n").replace("\r", "\n")
            if delegate_bot_id is not None:
                parser = await self._delegation_markup(binding, event)
                question = parser.question(question, (event.target_bot_id, delegate_bot_id))
            return VerifiedInboundMessage(
                event,
                question,
            )


class ZulipDeliveryAdapter:
    def __init__(
        self,
        *,
        client: httpx2.AsyncClient,
        base_url: str,
        provider: str,
        im_instance_id: str,
        realm_id: str,
        sender_bot_id: str,
        bot_email: str,
        bot_api_key: SecretStr,
        host_origin: str,
        bots: Mapping[str, tuple[str, SecretStr]] | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._instance = im_instance_id
        self._realm = realm_id
        self._bot_id = sender_bot_id
        self._bots = dict(bots or {})
        self._bots[sender_bot_id] = (bot_email, bot_api_key)
        self._host_origin = host_origin.rstrip("/")

    @staticmethod
    def _number(value: str) -> int:
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise ValueError("im_delivery_identity_invalid")
        return int(value)

    def _conversation(self, recipient: str, sender_bot_id: str) -> str:
        return json.dumps(
            {"type": "private", "user_ids": sorted(
                {self._number(sender_bot_id), self._number(recipient)}
            )},
            sort_keys=True,
            separators=(",", ":"),
        )

    def prepare_private_card(
        self,
        *,
        delivery_id: UUID,
        initiator_label: str,
        recipient_im_user_id: str,
        note: str,
    ) -> DeliveryMessage:
        target = self._conversation(recipient_im_user_id, self._bot_id)
        content = (
            f"EternalAI 助手，受{initiator_label}委托\n\n{note}\n\n"
            f"投递编号：{delivery_id}\n"
            f"[回到 EternalAI 核实]({self._host_origin}/messages?delivery={delivery_id})"
        )
        return DeliveryMessage(
            provider=self._provider,
            im_instance_id=self._instance,
            realm_id=self._realm,
            sender_bot_id=self._bot_id,
            target_conversation_ref=target,
            card_content=content,
        )

    def prepare_agent_reply(
        self, *, delivery_id: UUID, sender_bot_id: str, recipient_im_user_id: str, text: str,
    ) -> DeliveryMessage:
        return DeliveryMessage(
            provider=self._provider, im_instance_id=self._instance, realm_id=self._realm,
            sender_bot_id=sender_bot_id,
            target_conversation_ref=self._conversation(recipient_im_user_id, sender_bot_id),
            card_content=f"{text}\n\n投递编号：{delivery_id}",
        )

    def prepare_group_status(
        self, *, delivery_id: UUID, sender_bot_id: str, conversation_ref: str,
        delegate_display_name: str | None = None,
    ) -> DeliveryMessage:
        text = ("请求已接收，请查看与助手的私聊。" if delegate_display_name is None else
                f"已转交{delegate_display_name}，结果将私回提问人，处理情况可在‘我的提问’查看")
        return DeliveryMessage(
            provider=self._provider, im_instance_id=self._instance, realm_id=self._realm,
            sender_bot_id=sender_bot_id, target_conversation_ref=conversation_ref,
            card_content=f"{text}\n投递编号：{delivery_id}",
            kind="group_status",
        )

    def _target(
        self, message: DeliveryMessage,
    ) -> tuple[httpx2.BasicAuth, dict[str, str], list[dict[str, object]]]:
        if ((message.provider, message.im_instance_id, message.realm_id)
                != (self._provider, self._instance, self._realm)
                or message.sender_bot_id not in self._bots):
            raise ValueError("im_delivery_target_changed")
        email, api_key = self._bots[message.sender_bot_id]
        auth = httpx2.BasicAuth(email, api_key.get_secret_value())
        conversation = json.loads(message.target_conversation_ref)
        if conversation["type"] == "private":
            users = conversation["user_ids"]
            bot_id = self._number(message.sender_bot_id)
            if (not isinstance(users, list) or any(type(user) is not int for user in users)
                    or bot_id not in users or len(set(users)) != len(users)
                    or len(users) < 2 or (message.kind == "private" and len(users) != 2)):
                raise ValueError("im_delivery_target_changed")
            recipients = [user for user in users if user != bot_id]
            data = {"type": "direct", "to": json.dumps(recipients)}
            narrow: list[dict[str, object]] = [{"operator": "dm", "operand": recipients}]
        elif conversation["type"] == "stream" and message.kind == "group_status":
            if (type(conversation.get("stream_id")) is not int
                    or not isinstance(conversation.get("topic"), str)):
                raise ValueError("im_delivery_target_changed")
            data = {
                "type": "stream", "to": str(conversation["stream_id"]),
                "topic": conversation["topic"],
            }
            narrow = [
                {"operator": "channel", "operand": conversation["stream_id"]},
                {"operator": "topic", "operand": conversation["topic"]},
            ]
        else:
            raise ValueError("im_delivery_target_changed")
        return auth, data, narrow

    async def _bot_identity(self, bot_id: str, auth: httpx2.BasicAuth) -> bool:
        response = await self._client.get(
            self._base_url + "/api/v1/users/me", auth=auth, follow_redirects=False
        )
        data = response.json()
        return (
            response.status_code == 200
            and isinstance(data, dict)
            and data.get("result") == "success"
            and type(data.get("user_id")) is int
            and str(data["user_id"]) == bot_id
            and data.get("is_bot") is True
            and data.get("is_active") is True
        )

    async def send_message_once(self, message: DeliveryMessage) -> DeliveryResult:
        try:
            auth, target, _ = self._target(message)
        except (ValueError, KeyError, TypeError):
            return DeliveryResult("known_rejected", None, "im_delivery_target_changed")
        try:
            if not await self._bot_identity(message.sender_bot_id, auth):
                return DeliveryResult("known_rejected", None, "im_bot_identity_changed")
        except (httpx2.RequestError, ValueError):
            # No send request was made, so this preflight failure is unambiguous.
            return DeliveryResult("known_rejected", None, "im_bot_identity_unavailable")
        try:
            response = await self._client.post(
                self._base_url + "/api/v1/messages",
                auth=auth,
                follow_redirects=False,
                data={**target, "content": message.card_content},
            )
            data = response.json()
        except (httpx2.RequestError, ValueError):
            return DeliveryResult("unknown", None, "im_delivery_response_unknown")
        if isinstance(data, dict):
            if (response.status_code == 200 and data.get("result") == "success"
                    and type(data.get("id")) is int and data["id"] > 0):
                return DeliveryResult("accepted", str(data["id"]), "im_delivery_accepted")
            if (response.status_code in (400, 401, 403, 404, 429)
                    and data.get("result") == "error"):
                return DeliveryResult("known_rejected", None, "im_delivery_rejected")
        return DeliveryResult("unknown", None, "im_delivery_response_unknown")

    async def reconcile_message(
        self, message: DeliveryMessage, delivery_id: UUID
    ) -> DeliveryResult:
        unknown = DeliveryResult("unknown", None, "im_delivery_unconfirmed")
        try:
            auth, _, narrow = self._target(message)
            if not await self._bot_identity(message.sender_bot_id, auth):
                return unknown
            response = await self._client.get(
                self._base_url + "/api/v1/messages",
                auth=auth,
                follow_redirects=False,
                params={
                    "anchor": "newest",
                    "num_before": 100,
                    "num_after": 0,
                    "apply_markdown": "false",
                    "allow_empty_topic_name": "true",
                    "narrow": json.dumps(narrow + [
                        {"operator": "search", "operand": str(delivery_id)},
                    ]),
                },
            )
            data = response.json()
        except (httpx2.RequestError, ValueError, KeyError, TypeError):
            return unknown
        if (response.status_code != 200 or not isinstance(data, dict)
                or data.get("result") != "success"
                or data.get("found_oldest") is not True
                or data.get("found_newest") is not True
                or data.get("history_limited") is not False):
            return unknown
        messages = data.get("messages")
        if not isinstance(messages, list) or len(messages) != 1:
            return unknown
        raw = messages[0]
        if not isinstance(raw, dict):
            return unknown
        remote = cast(dict[str, Any], raw)
        if (type(remote.get("sender_id")) is not int
                or str(remote["sender_id"]) != message.sender_bot_id):
            return unknown
        try:
            conversation, _ = _source_conversation(remote)
        except IMContextError:
            return unknown
        if conversation != message.target_conversation_ref:
            return unknown
        content = remote.get("content")
        if (remote.get("content_type") != "text/x-markdown"
                or not isinstance(content, str)
                or remote.get("last_edit_timestamp") is not None
                or remote.get("edit_history")
                or content != message.card_content
                or type(remote.get("id")) is not int or remote["id"] <= 0):
            return unknown
        return DeliveryResult("accepted", str(remote["id"]), "im_delivery_reconciled")
