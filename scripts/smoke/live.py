"""Value-free protocol observation around the production OA Live provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import HTTPMessage, RemoteDisconnected
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scripts.smoke.errors import SmokeError

_EXPECTED_FORM_FIELDS = frozenset(
    {"id", "pagesize", "msgid", "mintime", "bizstate", "selectState"}
)
_CURSOR_FIELDS = ("msgid", "mintime")


@dataclass(frozen=True, slots=True)
class ProtocolSummary:
    request_count: int
    response_count: int
    record_count: int
    terminal_empty_page: bool
    cursor_chain_matches: bool
    configured_form_matches: bool
    successful_envelopes: bool
    envelope_fields: tuple[str, ...]
    record_field_types: dict[str, tuple[str, ...]]
    transport_failure_kind: str | None = None


@dataclass(repr=False, slots=True)
class ProtocolEvidence:
    """Retain field names, types, counts and booleans; never retain wire values."""

    expected_form: Mapping[str, str] = field(repr=False)
    request_count: int = 0
    response_count: int = 0
    record_count: int = 0
    terminal_empty_page: bool = False
    cursor_chain_matches: bool = True
    configured_form_matches: bool = True
    successful_envelopes: bool = True
    envelope_fields: set[str] = field(default_factory=set)
    record_field_types: dict[str, set[str]] = field(default_factory=dict)
    transport_failure_kind: str | None = None
    _last_cursor_digest: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "ProtocolEvidence(structural_only=True)"

    def observe(self, request: Request, raw_response: bytes) -> None:
        """Observe one exchange and discard all raw request/response values."""

        form = _parse_request_form(request.data)
        self.request_count += 1
        if set(form) != _EXPECTED_FORM_FIELDS:
            self.configured_form_matches = False
        for key in ("id", "pagesize", "bizstate", "selectState"):
            if form.get(key) != self.expected_form.get(key):
                self.configured_form_matches = False

        request_cursor_digest = _cursor_digest(form)
        if self.request_count == 1:
            if any(form.get(key) for key in _CURSOR_FIELDS):
                self.cursor_chain_matches = False
        elif request_cursor_digest != self._last_cursor_digest:
            self.cursor_chain_matches = False

        payload: Any = None
        try:
            payload = json.loads(raw_response.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.successful_envelopes = False
            self._last_cursor_digest = None
            return
        finally:
            raw_response = b""

        self.response_count += 1
        if not isinstance(payload, Mapping):
            self.successful_envelopes = False
            self._last_cursor_digest = None
            return
        self.envelope_fields.update(
            key for key in payload if isinstance(key, str)
        )
        if payload.get("status") != "1":
            self.successful_envelopes = False
        records = payload.get("data")
        if not isinstance(records, list):
            self.successful_envelopes = False
            self._last_cursor_digest = None
            return
        self.record_count += len(records)
        if not records:
            self.terminal_empty_page = True
        for record in records:
            if not isinstance(record, Mapping):
                self.record_field_types.setdefault("<non_object>", set()).add(
                    _json_type(record)
                )
                continue
            for key, value in record.items():
                if isinstance(key, str):
                    self.record_field_types.setdefault(key, set()).add(
                        _json_type(value)
                    )
        self._last_cursor_digest = _cursor_digest(payload)
        payload = None
        records = []

    def summary(self) -> ProtocolSummary:
        return ProtocolSummary(
            request_count=self.request_count,
            response_count=self.response_count,
            record_count=self.record_count,
            terminal_empty_page=self.terminal_empty_page,
            cursor_chain_matches=self.cursor_chain_matches,
            configured_form_matches=self.configured_form_matches,
            successful_envelopes=self.successful_envelopes,
            envelope_fields=tuple(sorted(self.envelope_fields)),
            record_field_types={
                key: tuple(sorted(types))
                for key, types in sorted(self.record_field_types.items())
            },
            transport_failure_kind=self.transport_failure_kind,
        )

    def clear_transient_state(self) -> None:
        self._last_cursor_digest = None


class RecordingOpener:
    """Proxy-free, no-redirect opener that records only safe protocol metadata."""

    def __init__(self, evidence: ProtocolEvidence) -> None:
        self._evidence = evidence
        self._opener = build_opener(ProxyHandler({}), _NoRedirectHandler())

    def open(self, request: Request, timeout: float) -> _RecordingResponse:
        try:
            response = self._opener.open(request, timeout=timeout)
        except HTTPError:
            raise
        except RemoteDisconnected:
            self._evidence.transport_failure_kind = "remote_disconnected"
            raise
        except ConnectionResetError:
            self._evidence.transport_failure_kind = "connection_reset"
            raise
        except ConnectionRefusedError:
            self._evidence.transport_failure_kind = "connection_refused"
            raise
        except TimeoutError:
            self._evidence.transport_failure_kind = "timeout"
            raise
        except URLError as exc:
            self._evidence.transport_failure_kind = _url_error_kind(exc)
            raise
        except OSError:
            self._evidence.transport_failure_kind = "transport_error"
            raise
        return _RecordingResponse(response, request, self._evidence)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


def _url_error_kind(error: URLError) -> str:
    reason = error.reason
    if isinstance(reason, RemoteDisconnected):
        return "remote_disconnected"
    if isinstance(reason, ConnectionResetError):
        return "connection_reset"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(reason, TimeoutError):
        return "timeout"
    return "url_error"


class _RecordingResponse:
    def __init__(
        self,
        response: Any,
        request: Request,
        evidence: ProtocolEvidence,
    ) -> None:
        self._response = response
        self._request = request
        self._evidence = evidence
        self._observed = False

    def __enter__(self) -> _RecordingResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool | None:
        return self._response.__exit__(exc_type, exc_value, traceback)

    def getcode(self) -> int:
        return int(self._response.getcode())

    @property
    def headers(self) -> HTTPMessage:
        return self._response.headers

    def read(self, amount: int = -1) -> bytes:
        raw = self._response.read(amount)
        if not self._observed:
            self._evidence.observe(self._request, raw)
            self._observed = True
        return raw


def compare_record_structures(
    left: ProtocolSummary,
    right: ProtocolSummary,
) -> tuple[bool, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Compare raw record fields and JSON types without accessing values."""

    left_fields = left.record_field_types
    right_fields = right.record_field_types
    added = tuple(sorted(right_fields.keys() - left_fields.keys()))
    removed = tuple(sorted(left_fields.keys() - right_fields.keys()))
    changed = tuple(
        sorted(
            key
            for key in left_fields.keys() & right_fields.keys()
            if left_fields[key] != right_fields[key]
        )
    )
    return not (added or removed or changed), added, removed, changed


def _parse_request_form(data: bytes | None) -> dict[str, str]:
    if data is None:
        return {}
    try:
        pairs = parse_qsl(
            data.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
        )
    except (UnicodeError, ValueError):
        return {}
    form: dict[str, str] = {}
    for key, value in pairs:
        if key in form:
            raise SmokeError("live_request_form_duplicate")
        form[key] = value
    return form


def _cursor_digest(values: Mapping[str, Any]) -> str | None:
    cursor: list[str] = []
    for key in _CURSOR_FIELDS:
        value = values.get(key)
        if not isinstance(value, str):
            return None
        cursor.append(value)
    encoded = json.dumps(cursor, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    cursor.clear()
    return hashlib.sha256(encoded).hexdigest()


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return "non_json"


__all__ = (
    "ProtocolEvidence",
    "ProtocolSummary",
    "RecordingOpener",
    "compare_record_structures",
)
