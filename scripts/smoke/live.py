"""Value-free protocol observation around the production OA Live provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import HTTPMessage, RemoteDisconnected
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scripts.smoke.errors import SmokeError

_EXPECTED_FORM_FIELDS = frozenset(
    {"id", "pagesize", "msgid", "mintime", "bizstate", "selectState"}
)
_CURSOR_FIELDS = ("msgid", "mintime")
_INITIAL_CURSOR = {"msgid": "0", "mintime": "0"}
_TODO_VIEW_CONDITION = "5"
_TODO_SESSION_KEY_LENGTH = 69


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
    http_status_code: int | None = None
    todo_three_step_matches: bool | None = None
    authoritative_count_matches: bool | None = None
    fixed_viewcondition_matches: bool | None = None
    query_credential_chain_matches: bool | None = None


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
    http_status_code: int | None = None
    _last_cursor_digest: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "ProtocolEvidence(structural_only=True)"

    def observe_request(self, request: Request) -> None:
        """Observe one outgoing request without retaining any wire value."""
        form = _parse_request_form(request.data)
        self.request_count += 1
        if set(form) != _EXPECTED_FORM_FIELDS:
            self.configured_form_matches = False
        for key in ("id", "pagesize", "bizstate", "selectState"):
            if form.get(key) != self.expected_form.get(key):
                self.configured_form_matches = False

        request_cursor_digest = _cursor_digest(form)
        if self.request_count == 1:
            if any(
                form.get(key) != _INITIAL_CURSOR[key]
                for key in _CURSOR_FIELDS
            ):
                self.cursor_chain_matches = False
        elif request_cursor_digest != self._last_cursor_digest:
            self.cursor_chain_matches = False

    def observe_http_status(self, status_code: int) -> None:
        """Retain only a bounded HTTP status code, never response details."""

        self.http_status_code = status_code if 100 <= status_code <= 599 else None

    def observe_response(self, raw_response: bytes) -> None:
        """Observe one response body and discard all raw response values."""

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

    def observe(self, request: Request, raw_response: bytes) -> None:
        """Observe one exchange and discard all raw request/response values."""

        self.observe_request(request)
        self.observe_response(raw_response)

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
            http_status_code=self.http_status_code,
        )

    def clear_transient_state(self) -> None:
        self._last_cursor_digest = None


@dataclass(repr=False, slots=True)
class TodoProtocolEvidence:
    """Observe the to-do three-step protocol without retaining query credentials."""

    split_page_key_path: str = field(repr=False)
    counts_path: str = field(repr=False)
    datas_path: str = field(repr=False)
    expected_split_form: Mapping[str, str] = field(repr=False)
    expected_sort_params: str = field(repr=False)
    request_count: int = 0
    response_count: int = 0
    record_count: int = 0
    configured_form_matches: bool = True
    successful_envelopes: bool = True
    request_order_matches: bool = True
    fixed_viewcondition_matches: bool = True
    query_credential_chain_matches: bool = True
    envelope_fields: set[str] = field(default_factory=set)
    record_field_types: dict[str, set[str]] = field(default_factory=dict)
    transport_failure_kind: str | None = None
    http_status_code: int | None = None
    _last_request_kind: str | None = field(default=None, repr=False)
    _query_credential_digest: str | None = field(default=None, repr=False)
    _authoritative_count: int | None = field(default=None, repr=False)
    _datas_request_count: int = field(default=0, repr=False)

    def __repr__(self) -> str:
        return "TodoProtocolEvidence(structural_only=True)"

    def observe_request(self, request: Request) -> None:
        """Retain request kind and structural booleans, never request values."""

        self.request_count += 1
        try:
            path = urlsplit(request.full_url).path
        except ValueError:
            path = ""
        form = _parse_request_form(request.data)
        kind = self._request_kind(path)
        self._last_request_kind = kind
        expected_kind = (
            "split"
            if self.request_count == 1
            else "counts"
            if self.request_count == 2
            else "datas"
        )
        if kind != expected_kind:
            self.request_order_matches = False

        if kind == "split":
            for key, expected_value in self.expected_split_form.items():
                if form.get(key) != expected_value:
                    self.configured_form_matches = False
            if form.get("viewcondition") != _TODO_VIEW_CONDITION:
                self.fixed_viewcondition_matches = False
        elif kind == "counts":
            if set(form) != {"dataKey"}:
                self.configured_form_matches = False
            self._observe_query_credential(form.get("dataKey"))
        elif kind == "datas":
            self._datas_request_count += 1
            if set(form) != {"current", "dataKey", "sortParams"}:
                self.configured_form_matches = False
            if form.get("sortParams") != self.expected_sort_params:
                self.configured_form_matches = False
            current = form.get("current", "")
            if not current.isdecimal() or int(current) != self._datas_request_count:
                self.request_order_matches = False
            self._observe_query_credential(form.get("dataKey"))
        else:
            self.request_order_matches = False
            self.configured_form_matches = False

    def observe_http_status(self, status_code: int) -> None:
        self.http_status_code = status_code if 100 <= status_code <= 599 else None

    def observe_response(self, raw_response: bytes) -> None:
        """Consume and discard raw values after extracting structure and counts."""

        payload: Any = None
        try:
            payload = json.loads(raw_response.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.successful_envelopes = False
            return
        finally:
            raw_response = b""

        self.response_count += 1
        if not isinstance(payload, Mapping):
            self.successful_envelopes = False
            return
        self.envelope_fields.update(
            key
            for key in payload
            if isinstance(key, str) and key not in {"sessionkey", "dataKey"}
        )
        if self._last_request_kind == "split":
            query_credential = payload.get("sessionkey")
            if (
                not isinstance(query_credential, str)
                or len(query_credential) != _TODO_SESSION_KEY_LENGTH
            ):
                self.successful_envelopes = False
                self._query_credential_digest = None
            else:
                self._query_credential_digest = _value_digest(query_credential)
        elif self._last_request_kind == "counts":
            count = payload.get("count")
            if (
                payload.get("status") is not True
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                self.successful_envelopes = False
            else:
                self._authoritative_count = count
        elif self._last_request_kind == "datas":
            records = payload.get("datas")
            if payload.get("status") is not True or not isinstance(records, list):
                self.successful_envelopes = False
            else:
                self.record_count += len(records)
                self._observe_records(records)
                records = []
        else:
            self.successful_envelopes = False
        payload = None

    def observe(self, request: Request, raw_response: bytes) -> None:
        """Observe one exchange and immediately discard its raw values."""

        self.observe_request(request)
        self.observe_response(raw_response)

    def summary(self) -> ProtocolSummary:
        three_step_matches = (
            self.request_order_matches
            and self.request_count >= 3
            and self.response_count == self.request_count
            and self._datas_request_count >= 1
        )
        authoritative_count_matches = (
            self._authoritative_count is not None
            and self.record_count == self._authoritative_count
        )
        return ProtocolSummary(
            request_count=self.request_count,
            response_count=self.response_count,
            record_count=self.record_count,
            terminal_empty_page=False,
            cursor_chain_matches=False,
            configured_form_matches=self.configured_form_matches,
            successful_envelopes=self.successful_envelopes,
            envelope_fields=tuple(sorted(self.envelope_fields)),
            record_field_types={
                key: tuple(sorted(types))
                for key, types in sorted(self.record_field_types.items())
            },
            transport_failure_kind=self.transport_failure_kind,
            http_status_code=self.http_status_code,
            todo_three_step_matches=three_step_matches,
            authoritative_count_matches=authoritative_count_matches,
            fixed_viewcondition_matches=self.fixed_viewcondition_matches,
            query_credential_chain_matches=self.query_credential_chain_matches,
        )

    def clear_transient_state(self) -> None:
        self._last_request_kind = None
        self._query_credential_digest = None

    def _request_kind(self, path: str) -> str | None:
        if path == self.split_page_key_path:
            return "split"
        if path == self.counts_path:
            return "counts"
        if path == self.datas_path:
            return "datas"
        return None

    def _observe_query_credential(self, value: str | None) -> None:
        if (
            value is None
            or self._query_credential_digest is None
            or _value_digest(value) != self._query_credential_digest
        ):
            self.query_credential_chain_matches = False

    def _observe_records(self, records: list[Any]) -> None:
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


class RecordingOpener:
    """Proxy-free, no-redirect opener that records only safe protocol metadata."""

    def __init__(self, evidence: ProtocolEvidence | TodoProtocolEvidence) -> None:
        self._evidence = evidence
        self._opener = build_opener(ProxyHandler({}), _NoRedirectHandler())

    def open(self, request: Request, timeout: float) -> _RecordingResponse:
        self._evidence.observe_request(request)
        try:
            response = self._opener.open(request, timeout=timeout)
        except HTTPError as exc:
            self._evidence.observe_http_status(int(exc.code))
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
        self._evidence.observe_http_status(int(response.getcode()))
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
        evidence: ProtocolEvidence | TodoProtocolEvidence,
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
            self._evidence.observe_response(raw)
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


def _value_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    "TodoProtocolEvidence",
    "compare_record_structures",
)
