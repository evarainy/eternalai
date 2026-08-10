"""Replay and bounded standard-library Live providers for OA reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, NoReturn, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from pydantic import SecretStr, ValidationError

from app.infra.adapters.oa.contracts import (
    OAContractPackProfile,
    OALegacyPendingWorkflowCollection,
    OAPendingWorkflowCollection,
    OAStructuralDriftReport,
    OASystemMessageCollection,
    build_contract_drift_baseline_fingerprint,
    build_live_pending_workflows_fingerprint,
    build_live_system_messages_fingerprint,
    build_structural_fingerprint,
    compare_structural_fingerprints,
    normalize_pending_workflow_records,
    normalize_system_message_records,
)
from app.ports.auth import OASessionCredential

_DEFAULT_MESSAGE_CENTER_PAGE_SIZE = 20
_DEFAULT_MAX_PAGES = 50
_DEFAULT_MAX_RECORDS = 5_000
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_INITIAL_MESSAGE_CENTER_CURSOR = ("0", "0")
_MAX_CONFIGURED_PAGES = 1_000
_MAX_CONFIGURED_PAGE_SIZE = 1_000
_MAX_CONFIGURED_RECORDS = 1_000_000
_MAX_CONFIGURED_RESPONSE_BYTES = 32 * 1024 * 1024
_PENDING_WORKFLOW_SESSION_KEY_LENGTH = 69
_LEGACY_PENDING_WORKFLOW_PROFILES = frozenset(
    {"ecology9-pending-workflows-v1", "ecology9-pending-workflows-v2"}
)
_MESSAGE_CENTER_PAYLOAD_REASON_CODES = {
    "OA message-center page exceeds the record limit": "page_limit",
    "OA message-center aggregate exceeds the record limit": "aggregate_limit",
    "OA message-center pagination cursor did not advance": "cursor_not_advanced",
    "OA message-center record repeated across pages": "record_repeated",
    "OA message-center response shape changed": "response_shape",
    "OA message-center response status is invalid": "response_status",
    "OA message-center response data is invalid": "response_data",
    "OA message-center response cursor is invalid": "response_cursor",
}
_COOKIE_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SESSION_EXPIRED_CODES = frozenset(
    {
        "401",
        "invalid_session",
        "login_required",
        "not_logged_in",
        "not_login",
        "session_expired",
        "session_invalid",
    }
)
_PERMISSION_DENIED_CODES = frozenset(
    {
        "403",
        "forbidden",
        "no_permission",
        "permission_denied",
    }
)


class OAReadProvider(Protocol):
    """Narrow provider seam behind the real OA adapter."""

    requires_credential: bool

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection: ...

    async def list_system_messages(
        self,
        credential: OASessionCredential | None = None,
    ) -> OASystemMessageCollection: ...


class OAStructuralDriftReporter(Protocol):
    """Optional value-free sink for a Live structural comparison."""

    def __call__(self, report: OAStructuralDriftReport) -> None: ...


class OAContractPackError(RuntimeError):
    """The selected Replay Contract Pack cannot be loaded safely."""


class OAContractPackPayloadInvalid(OAContractPackError):
    """The Contract Pack exists but violates its normalized payload contract."""


class LiveOAReadProviderNotImplemented(RuntimeError):
    """Compatibility error retained for callers from the Replay-only phase."""


class OALiveProviderError(RuntimeError):
    """Base class for safely classified Live OA failures."""


class OALiveIdentityUnbound(OALiveProviderError):
    """The Live request has no user-delegated credential."""


class OALiveIdentityExpired(OALiveProviderError):
    """The local or upstream OA Session requires reauthentication."""


class OALivePermissionDenied(OALiveProviderError):
    """The authenticated OA user lacks permission for this read."""


class OALiveTimeout(OALiveProviderError):
    """The bounded OA request timed out."""


class OALiveHTTPServerError(OALiveProviderError):
    """OA returned a server-side HTTP failure."""


class OALivePayloadInvalid(OALiveProviderError):
    """OA returned malformed, contradictory, or unnormalizable data."""


class OALiveRequestError(OALiveProviderError):
    """The Live request failed outside a more specific safe classification."""


class ReplayOAReadProvider:
    """Load one immutable, fingerprint-bound OA Contract Pack from disk."""

    requires_credential = False

    def __init__(self, contract_pack_dir: Path) -> None:
        self._contract_pack_dir = contract_pack_dir

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        del credential
        collection, _fingerprint = _load_contract_pack(self._contract_pack_dir)
        if not isinstance(collection, OAPendingWorkflowCollection):
            raise OAContractPackPayloadInvalid(
                "Contract Pack does not provide pending workflows"
            )
        return collection

    async def list_system_messages(
        self,
        credential: OASessionCredential | None = None,
    ) -> OASystemMessageCollection:
        del credential
        collection, _fingerprint = _load_contract_pack(self._contract_pack_dir)
        if not isinstance(collection, OASystemMessageCollection):
            raise OAContractPackPayloadInvalid(
                "Contract Pack does not provide system messages"
            )
        return collection


class LiveOAReadProvider:
    """Fetch and normalize one user-delegated OA read with bounded pagination."""

    requires_credential = True

    def __init__(
        self,
        *,
        base_url: str,
        message_center_endpoint_path: str,
        pending_workflows_split_page_key_path: str,
        pending_workflows_counts_path: str,
        pending_workflows_datas_path: str,
        pending_workflows_actiontype: str,
        pending_workflows_hide_no_data_tab: str,
        pending_workflows_method: str,
        pending_workflows_offical_type: str,
        pending_workflows_view_scope: str,
        pending_workflows_sort_params: str,
        system_messages_category_id: str,
        system_messages_bizstate: str,
        system_messages_select_state: str,
        timeout_seconds: float,
        pending_workflows_contract_pack_dir: Path,
        system_messages_contract_pack_dir: Path,
        drift_reporter: OAStructuralDriftReporter | None = None,
        page_size: int = _DEFAULT_MESSAGE_CENTER_PAGE_SIZE,
        max_pages: int = _DEFAULT_MAX_PAGES,
        max_records: int = _DEFAULT_MAX_RECORDS,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        opener_factory: Callable[[], OpenerDirector] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._base_url, self._allowed_origin = _validate_base_url(base_url)
        self._message_center_endpoint_path = _validate_endpoint_path(
            message_center_endpoint_path
        )
        self._pending_workflows_split_page_key_path = _validate_endpoint_path(
            pending_workflows_split_page_key_path
        )
        self._pending_workflows_counts_path = _validate_endpoint_path(
            pending_workflows_counts_path
        )
        self._pending_workflows_datas_path = _validate_endpoint_path(
            pending_workflows_datas_path
        )
        self._pending_workflows_split_form = _build_pending_workflows_split_form(
            actiontype=pending_workflows_actiontype,
            hide_no_data_tab=pending_workflows_hide_no_data_tab,
            method=pending_workflows_method,
            offical_type=pending_workflows_offical_type,
            view_scope=pending_workflows_view_scope,
        )
        self._pending_workflows_sort_params = _validate_pending_workflow_form_value(
            pending_workflows_sort_params,
            field_name="sortParams",
        )
        self._system_messages_form = _validate_message_center_form(
            category_id=system_messages_category_id,
            bizstate=system_messages_bizstate,
            select_state=system_messages_select_state,
            allow_empty_category=True,
        )
        if timeout_seconds <= 0:
            raise ValueError("OA HTTP timeout must be positive")
        if not 1 <= page_size <= _MAX_CONFIGURED_PAGE_SIZE:
            raise ValueError("OA message-center page size is outside the allowed range")
        if not 1 <= max_pages <= _MAX_CONFIGURED_PAGES:
            raise ValueError("OA maximum page count is outside the allowed range")
        if not 1 <= max_records <= _MAX_CONFIGURED_RECORDS:
            raise ValueError("OA aggregate record limit is outside the allowed range")
        if not 1 <= max_response_bytes <= _MAX_CONFIGURED_RESPONSE_BYTES:
            raise ValueError("OA response size limit is outside the allowed range")
        pending_collection, pending_expected_fingerprint = _load_contract_pack(
            pending_workflows_contract_pack_dir
        )
        if not isinstance(pending_collection, OAPendingWorkflowCollection):
            raise OAContractPackPayloadInvalid(
                "Live pending-workflow provider requires its matching Contract Pack"
            )
        system_message_collection, system_message_expected_fingerprint = (
            _load_contract_pack(system_messages_contract_pack_dir)
        )
        if not isinstance(system_message_collection, OASystemMessageCollection):
            raise OAContractPackPayloadInvalid(
                "Live system-message provider requires its matching Contract Pack"
            )

        self._timeout_seconds = timeout_seconds
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_records = max_records
        self._max_response_bytes = max_response_bytes
        self._pending_expected_fingerprint = pending_expected_fingerprint
        self._system_message_expected_fingerprint = (
            system_message_expected_fingerprint
        )
        self._drift_reporter = drift_reporter
        self._opener_factory = opener_factory or self._build_isolated_opener
        self._clock = clock

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        raw_records: list[Any] = []
        collection: OAPendingWorkflowCollection | None = None
        authoritative_count = 0

        try:
            raw_records, authoritative_count = (
                await self._list_pending_workflow_records(credential)
            )
            try:
                actual_fingerprint = (
                    build_live_pending_workflows_fingerprint(raw_records)
                )
                drift_report = compare_structural_fingerprints(
                    self._pending_expected_fingerprint,
                    actual_fingerprint,
                )
            except (TypeError, ValueError):
                raise OALivePayloadInvalid(
                    "OA payload structure cannot be fingerprinted"
                ) from None
            except Exception:
                _log_provider_failure("structural_fingerprint")
                raise OALiveRequestError(
                    "OA response processing failed"
                ) from None
            if self._drift_reporter is not None:
                self._drift_reporter(drift_report)

            try:
                collection = normalize_pending_workflow_records(
                    raw_records,
                    record_limit=self._max_records,
                    authoritative_count=authoritative_count,
                )
            except (TypeError, ValueError):
                raise OALivePayloadInvalid(
                    "OA payload violates the normalized contract"
                ) from None
            except Exception:
                _log_provider_failure("normalization")
                raise OALiveRequestError(
                    "OA response processing failed"
                ) from None
            if collection is None:
                _log_provider_failure("normalization")
                raise OALiveRequestError("OA response processing failed")
            return collection
        finally:
            credential = None
            raw_records.clear()
            collection = None
            authoritative_count = 0

    async def list_system_messages(
        self,
        credential: OASessionCredential | None = None,
    ) -> OASystemMessageCollection:
        raw_records: list[Any] = []
        collection: OASystemMessageCollection | None = None

        try:
            raw_records = await self._list_message_center_records(
                credential,
                self._system_messages_form,
            )
            try:
                actual_fingerprint = build_live_system_messages_fingerprint(
                    raw_records
                )
                drift_report = compare_structural_fingerprints(
                    self._system_message_expected_fingerprint,
                    actual_fingerprint,
                )
            except (TypeError, ValueError):
                raise OALivePayloadInvalid(
                    "OA payload structure cannot be fingerprinted"
                ) from None
            except Exception:
                _log_provider_failure("structural_fingerprint")
                raise OALiveRequestError(
                    "OA response processing failed"
                ) from None
            if self._drift_reporter is not None:
                self._drift_reporter(drift_report)

            try:
                collection = normalize_system_message_records(
                    raw_records,
                    record_limit=self._max_records,
                    is_complete=True,
                    link_normalizer=self._normalize_system_message_link,
                )
            except (TypeError, ValueError):
                raise OALivePayloadInvalid(
                    "OA payload violates the normalized contract"
                ) from None
            except Exception:
                _log_provider_failure("normalization")
                raise OALiveRequestError(
                    "OA response processing failed"
                ) from None
            if collection is None:
                _log_provider_failure("normalization")
                raise OALiveRequestError("OA response processing failed")
            return collection
        finally:
            credential = None
            raw_records.clear()
            collection = None

    async def _list_pending_workflow_records(
        self,
        credential: OASessionCredential | None,
    ) -> tuple[list[Any], int]:
        """Run splitPageKey -> counts -> datas and return only a proven aggregate."""

        if credential is None:
            raise OALiveIdentityUnbound("OA credential is required")
        cookie_header = ""
        sessionkey = ""
        opener: OpenerDirector | None = None
        split_payload: dict[str, Any] | None = None
        counts_payload: dict[str, Any] | None = None
        datas_payload: dict[str, Any] | None = None
        raw_records: list[Any] = []
        completed_records: list[Any] | None = None
        page_records: list[Any] = []
        seen_todo_ids: set[str] = set()
        authoritative_count = 0
        parsed_value: Any = None
        payload_error_kind: str | None = None
        record: Any = None
        todo_id = ""

        try:
            ttl_status = _credential_ttl_status(
                credential.expires_at,
                self._clock(),
            )
            if ttl_status == "invalid":
                raise OALiveRequestError("OA credential expiry is invalid")
            if ttl_status == "expired":
                raise OALiveIdentityExpired("OA Session has expired")
            cookie_header = _build_cookie_header(credential.cookies)
            opener = self._opener_factory()

            split_payload = await self._request_form_payload(
                opener=opener,
                cookie_header=cookie_header,
                endpoint_path=self._pending_workflows_split_page_key_path,
                parameters=self._pending_workflows_split_form,
            )
            parsed_value, payload_error_kind = _consume_pending_workflow_value(
                split_payload,
                _read_pending_workflow_sessionkey,
                check_business_envelope=True,
            )
            split_payload = None
            if payload_error_kind is not None:
                _raise_pending_workflow_payload_error(payload_error_kind)
            sessionkey = parsed_value
            parsed_value = None
            payload_error_kind = None

            # ``getUnoperators`` changes response shape on empty pages, while
            # ``checks`` and ``getWfListParams`` add no consumed bare business
            # field. Skip all three; HTML span twins are intentionally excluded.
            counts_payload = await self._request_form_payload(
                opener=opener,
                cookie_header=cookie_header,
                endpoint_path=self._pending_workflows_counts_path,
                parameters={"dataKey": sessionkey},
            )
            parsed_value, payload_error_kind = _consume_pending_workflow_value(
                counts_payload,
                _read_pending_workflow_count,
                check_business_envelope=True,
            )
            counts_payload = None
            if payload_error_kind is not None:
                _raise_pending_workflow_payload_error(payload_error_kind)
            authoritative_count = parsed_value
            parsed_value = None
            payload_error_kind = None
            if authoritative_count > self._max_records:
                raise OALivePayloadInvalid(
                    "OA pending-workflow authoritative count exceeds the record limit"
                )

            for page_number in range(1, self._max_pages + 1):
                datas_payload = await self._request_form_payload(
                    opener=opener,
                    cookie_header=cookie_header,
                    endpoint_path=self._pending_workflows_datas_path,
                    parameters={
                        "current": str(page_number),
                        "dataKey": sessionkey,
                        "sortParams": self._pending_workflows_sort_params,
                    },
                )
                parsed_value, payload_error_kind = _consume_pending_workflow_value(
                    datas_payload,
                    _read_pending_workflow_page,
                    check_business_envelope=True,
                )
                datas_payload = None
                if payload_error_kind is not None:
                    _raise_pending_workflow_payload_error(payload_error_kind)
                page_records, response_page_size = parsed_value
                parsed_value = None
                payload_error_kind = None
                if len(page_records) > response_page_size:
                    raise OALivePayloadInvalid(
                        "OA pending-workflow page exceeds its declared size"
                    )
                if not page_records:
                    if len(raw_records) != authoritative_count:
                        raise OALivePayloadInvalid(
                            "OA pending-workflow returned count does not match "
                            "the authoritative count"
                        )
                    completed_records = raw_records
                    raw_records = []
                    return completed_records, authoritative_count

                for record in page_records:
                    parsed_value, payload_error_kind = (
                        _consume_pending_workflow_value(
                            record,
                            _pending_workflow_record_identifier,
                            check_business_envelope=False,
                        )
                    )
                    record = None
                    if payload_error_kind is not None:
                        _raise_pending_workflow_payload_error(payload_error_kind)
                    todo_id = parsed_value
                    parsed_value = None
                    payload_error_kind = None
                    if todo_id in seen_todo_ids:
                        raise OALivePayloadInvalid(
                            "OA pending-workflow record repeated across pages"
                        )
                    seen_todo_ids.add(todo_id)
                raw_records.extend(page_records)
                page_records.clear()
                if len(raw_records) > authoritative_count:
                    raise OALivePayloadInvalid(
                        "OA pending-workflow returned count exceeds "
                        "the authoritative count"
                    )
                if len(raw_records) == authoritative_count:
                    completed_records = raw_records
                    raw_records = []
                    return completed_records, authoritative_count

            raise OALivePayloadInvalid(
                "OA pending-workflow returned count does not match "
                "the authoritative count within the page limit"
            )
        finally:
            credential = None
            cookie_header = ""
            sessionkey = ""
            opener = None
            split_payload = None
            counts_payload = None
            datas_payload = None
            raw_records.clear()
            page_records.clear()
            completed_records = None
            seen_todo_ids.clear()
            authoritative_count = 0
            parsed_value = None
            payload_error_kind = None
            record = None
            todo_id = ""

    async def _list_message_center_records(
        self,
        credential: OASessionCredential | None,
        form: Mapping[str, str],
    ) -> list[Any]:
        if credential is None:
            raise OALiveIdentityUnbound("OA credential is required")
        cookie_header = ""
        opener: OpenerDirector | None = None
        payload: dict[str, Any] | None = None
        page_records: list[Any] = []
        raw_records: list[Any] = []
        completed_records: list[Any] | None = None
        seen_record_fingerprints: set[bytes] = set()
        cursor = _INITIAL_MESSAGE_CENTER_CURSOR
        next_cursor = _INITIAL_MESSAGE_CENTER_CURSOR
        payload_error_message = ""
        payload_error_reason = "payload_invalid"

        try:
            ttl_status = _credential_ttl_status(
                credential.expires_at,
                self._clock(),
            )
            if ttl_status == "invalid":
                raise OALiveRequestError("OA credential expiry is invalid")
            if ttl_status == "expired":
                raise OALiveIdentityExpired("OA Session has expired")
            cookie_header = _build_cookie_header(credential.cookies)
            opener = self._opener_factory()

            # Inferred pending P2-OA-INTRANET-SMOKE-001: feed each response's
            # msgid/mintime back into the same-named request fields verbatim;
            # require every later page to keep the first page's exact envelope;
            # and treat only an explicit empty data page as successful termination.
            # Any conflicting signal fails closed through cursor, shape, or limit checks.
            for page_number in range(1, self._max_pages + 1):
                assert opener is not None
                payload = await self._request_message_center_page(
                    opener=opener,
                    cookie_header=cookie_header,
                    form=form,
                    cursor=cursor,
                )
                page_error_kind: str | None = None
                is_complete = False
                try:
                    _raise_for_business_error(payload)
                    page_records, next_cursor = _read_message_center_page(payload)
                    if len(page_records) > self._page_size:
                        raise OALivePayloadInvalid(
                            "OA message-center page exceeds the record limit"
                        )
                    aggregate_record_count = len(raw_records) + len(page_records)
                    if aggregate_record_count > self._max_records:
                        raise OALivePayloadInvalid(
                            "OA message-center aggregate exceeds the record limit"
                        )
                    is_complete = not page_records
                    if page_records and next_cursor == cursor:
                        raise OALivePayloadInvalid(
                            "OA message-center pagination cursor did not advance"
                        )
                    for record in page_records:
                        record_fingerprint = _message_center_record_fingerprint(record)
                        if record_fingerprint in seen_record_fingerprints:
                            raise OALivePayloadInvalid(
                                "OA message-center record repeated across pages"
                            )
                        seen_record_fingerprints.add(record_fingerprint)
                except OALiveIdentityExpired:
                    page_error_kind = "identity_expired"
                except OALivePermissionDenied:
                    page_error_kind = "permission_denied"
                except OALivePayloadInvalid as exc:
                    payload_error_message, payload_error_reason = (
                        _safe_message_center_payload_error(exc)
                    )
                    page_error_kind = "payload_invalid"
                except OALiveProviderError:
                    page_error_kind = "payload_invalid"
                except Exception:
                    _log_provider_failure("payload_processing")
                    page_error_kind = "request_error"
                if page_error_kind is not None:
                    if page_error_kind == "payload_invalid":
                        _log_message_center_payload_rejection(
                            reason=payload_error_reason,
                            page_number=page_number,
                            payload=payload,
                            aggregate_record_count=len(raw_records),
                            cursor=cursor,
                            next_cursor=next_cursor,
                        )
                    payload = None
                    page_records.clear()
                    if page_error_kind == "identity_expired":
                        raise OALiveIdentityExpired(
                            "OA Session is no longer valid"
                        )
                    if page_error_kind == "permission_denied":
                        raise OALivePermissionDenied(
                            "OA permission was denied"
                        )
                    if page_error_kind == "request_error":
                        raise OALiveRequestError(
                            "OA response processing failed"
                        )
                    raise OALivePayloadInvalid(
                        payload_error_message or "OA response payload is invalid"
                    )

                raw_records.extend(page_records)
                page_records.clear()
                if is_complete:
                    completed_records = raw_records
                    raw_records = []
                    return completed_records
                if page_number == self._max_pages:
                    _log_message_center_payload_rejection(
                        reason="pagination_page_limit",
                        page_number=page_number,
                        payload=payload,
                        aggregate_record_count=len(raw_records),
                        cursor=cursor,
                        next_cursor=next_cursor,
                    )
                    raise OALivePayloadInvalid(
                        "OA message-center pagination may be truncated at the page limit"
                    )
                cursor = next_cursor
            raise OALivePayloadInvalid(
                "OA message-center pagination did not terminate"
            )  # pragma: no cover
        finally:
            cookie_header = ""
            credential = None
            opener = None
            payload = None
            page_records.clear()
            raw_records.clear()
            completed_records = None
            seen_record_fingerprints.clear()
            cursor = _INITIAL_MESSAGE_CENTER_CURSOR
            next_cursor = _INITIAL_MESSAGE_CENTER_CURSOR
            payload_error_message = ""
            payload_error_reason = ""

    def _build_isolated_opener(self) -> OpenerDirector:
        return build_opener(
            ProxyHandler({}),
            _SameOriginRedirectHandler(self._allowed_origin),
        )

    def _normalize_system_message_link(self, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or _origin_tuple(parsed) != self._allowed_origin
            ):
                raise ValueError("OA system-message link is not same-origin")
            relative = parsed.path or "/"
            if parsed.query:
                relative = f"{relative}?{parsed.query}"
            if parsed.fragment:
                relative = f"{relative}#{parsed.fragment}"
            return relative
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("OA system-message link is not host-relative")
        return value

    async def _request_message_center_page(
        self,
        *,
        opener: OpenerDirector,
        cookie_header: str,
        form: Mapping[str, str],
        cursor: tuple[str, str],
    ) -> dict[str, Any]:
        parameters = {
            "id": form["id"],
            "pagesize": str(self._page_size),
            "msgid": cursor[0],
            "mintime": cursor[1],
            "bizstate": form["bizstate"],
            "selectState": form["selectState"],
        }
        try:
            return await self._request_form_payload(
                opener=opener,
                cookie_header=cookie_header,
                endpoint_path=self._message_center_endpoint_path,
                parameters=parameters,
            )
        finally:
            parameters.clear()
            del cursor
            del form
            del cookie_header
            del opener

    async def _request_form_payload(
        self,
        *,
        opener: OpenerDirector,
        cookie_header: str,
        endpoint_path: str,
        parameters: Mapping[str, str],
    ) -> dict[str, Any]:
        owned_parameters = dict(parameters)
        encoded_parameters = b""
        payload: dict[str, Any] | None = None
        error_kind: str | None = None
        request: Request | None = None
        try:
            encoded_parameters = urlencode(owned_parameters).encode("ascii")
            request = Request(
                f"{self._base_url}{endpoint_path}",
                data=encoded_parameters,
                headers={
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Content-Type": (
                        "application/x-www-form-urlencoded; charset=utf-8"
                    ),
                    "Cookie": cookie_header,
                    "Origin": self._base_url,
                    "Referer": f"{self._base_url}/wui/index.html",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 6.1; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/86.0.4240.111 Safari/537.36"
                    ),
                    "X-Requested-With": "XMLHttpRequest",
                },
                method="POST",
            )
            payload = await asyncio.to_thread(self._open_json, opener, request)
        except HTTPError as exc:
            error_kind = _http_error_kind(int(exc.code))
        except OALiveIdentityUnbound:
            error_kind = "identity_unbound"
        except OALiveIdentityExpired:
            error_kind = "identity_expired"
        except OALivePermissionDenied:
            error_kind = "permission_denied"
        except OALiveTimeout:
            error_kind = "timeout"
        except OALiveHTTPServerError:
            error_kind = "http_server"
        except OALivePayloadInvalid:
            error_kind = "payload_invalid"
        except OALiveProviderError:
            error_kind = "request_error"
        except (TimeoutError, socket.timeout):
            error_kind = "timeout"
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                error_kind = "timeout"
            else:
                error_kind = "request_error"
        except (UnicodeError, json.JSONDecodeError, ValueError):
            error_kind = "payload_invalid"
        except OSError:
            error_kind = "request_error"
        except Exception:
            _log_provider_failure("http_request")
            error_kind = "request_error"
        finally:
            request = None
            encoded_parameters = b""
            owned_parameters.clear()
            del parameters
            del endpoint_path
            del cookie_header
            del opener

        if error_kind == "identity_unbound":
            raise OALiveIdentityUnbound("OA credential is required")
        if error_kind == "identity_expired":
            raise OALiveIdentityExpired("OA Session is no longer valid")
        if error_kind == "permission_denied":
            raise OALivePermissionDenied("OA permission was denied")
        if error_kind == "timeout":
            raise OALiveTimeout("OA request timed out")
        if error_kind == "http_server":
            raise OALiveHTTPServerError("OA returned a server error")
        if error_kind == "payload_invalid":
            raise OALivePayloadInvalid("OA response is not valid JSON")
        if error_kind is not None:
            raise OALiveRequestError("OA network request failed")
        if payload is None:
            raise OALiveRequestError("OA response is unavailable")
        return payload

    def _open_json(
        self,
        opener: OpenerDirector,
        request: Request,
    ) -> dict[str, Any]:
        with opener.open(request, timeout=self._timeout_seconds) as response:
            status_code = int(response.getcode())
            _raise_for_http_status(status_code)
            raw = response.read(self._max_response_bytes + 1)
        if len(raw) > self._max_response_bytes:
            raise ValueError("OA response exceeds the size limit")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("OA response must be a JSON object")
        return {str(key): value for key, value in payload.items()}


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    """Allow same-origin redirects while stopping Cookie-bearing cross-host hops."""

    def __init__(self, allowed_origin: tuple[str, str, int]) -> None:
        super().__init__()
        self._allowed_origin = allowed_origin

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        if _origin_tuple(urlsplit(newurl)) != self._allowed_origin:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _load_contract_pack(
    contract_pack_dir: Path,
) -> tuple[
    OALegacyPendingWorkflowCollection
    | OAPendingWorkflowCollection
    | OASystemMessageCollection,
    dict[str, Any],
]:
    profile_payload = _load_json(contract_pack_dir / "profile.json")
    try:
        profile = OAContractPackProfile.model_validate(
            profile_payload,
            strict=True,
        )
    except ValidationError:
        raise OAContractPackError("Contract Pack profile is invalid") from None

    if profile.profile_version != contract_pack_dir.name:
        raise OAContractPackError("Contract Pack directory and profile disagree")

    sample_payload = _load_json(contract_pack_dir / profile.sample_file)
    fingerprint_payload = _load_json(contract_pack_dir / profile.fingerprint_file)
    built_fingerprint = build_structural_fingerprint(sample_payload)
    if fingerprint_payload != built_fingerprint:
        raise OAContractPackError("Contract Pack structural fingerprint mismatch")

    collection_model: (
        type[OALegacyPendingWorkflowCollection]
        | type[OAPendingWorkflowCollection]
        | type[OASystemMessageCollection]
    )
    if profile.capability_id == "oa.list_pending_workflows":
        if profile.profile_version in _LEGACY_PENDING_WORKFLOW_PROFILES:
            collection_model = OALegacyPendingWorkflowCollection
        else:
            collection_model = OAPendingWorkflowCollection
    else:
        collection_model = OASystemMessageCollection
    try:
        collection = collection_model.model_validate(sample_payload, strict=True)
    except ValidationError:
        raise OAContractPackPayloadInvalid(
            "Contract Pack sample violates the normalized OA model"
        ) from None
    if not isinstance(fingerprint_payload, dict):
        raise OAContractPackError("Contract Pack fingerprint is invalid")
    drift_baseline = build_contract_drift_baseline_fingerprint(sample_payload)
    return collection, drift_baseline


def _load_json(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise OAContractPackError("Contract Pack file cannot be loaded") from None


def _validate_base_url(base_url: str) -> tuple[str, tuple[str, str, int]]:
    parsed = urlsplit(base_url.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("OA base URL must be an HTTP(S) origin without credentials")
    origin = _origin_tuple(parsed)
    normalized = f"{parsed.scheme.lower()}://{parsed.netloc.rstrip('/')}"
    return normalized, origin


def _validate_endpoint_path(endpoint_path: str) -> str:
    parsed = urlsplit(endpoint_path)
    if (
        not endpoint_path.startswith("/")
        or endpoint_path.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in endpoint_path
    ):
        raise ValueError("OA endpoint path must be a relative absolute-path reference")
    return parsed.path


def _origin_tuple(parsed: Any) -> tuple[str, str, int]:
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    default_port = 443 if scheme == "https" else 80
    return scheme, hostname, parsed.port or default_port


def _credential_ttl_status(
    expires_at: datetime,
    now: datetime,
) -> str:
    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        return "invalid"
    if expires_at <= now:
        return "expired"
    return "active"


def _build_cookie_header(cookies: Mapping[str, SecretStr]) -> str:
    if not cookies:
        raise OALiveRequestError("OA credential has no Session cookies")
    pairs: list[str] = []
    value = ""
    secret: SecretStr | None = None
    invalid = False
    unexpected = False
    try:
        for name, secret in sorted(cookies.items()):
            value = secret.get_secret_value()
            if (
                _COOKIE_NAME_PATTERN.fullmatch(name) is None
                or not value
                or ";" in value
                or any(
                    ord(character) < 0x20 or ord(character) == 0x7F
                    for character in value
                )
            ):
                invalid = True
                break
            pairs.append(f"{name}={value}")
    except Exception:
        unexpected = True
    value = ""
    secret = None
    del cookies
    if unexpected:
        pairs.clear()
        _log_provider_failure("cookie_header")
        raise OALiveRequestError("OA Session cookie cannot be prepared")
    if invalid:
        pairs.clear()
        raise OALiveRequestError("OA Session cookie is invalid")
    header = "; ".join(pairs)
    pairs.clear()
    return header


def _http_error_kind(status_code: int) -> str:
    if status_code == 401:
        return "identity_expired"
    if status_code == 403:
        return "permission_denied"
    if 500 <= status_code < 600:
        return "http_server"
    return "request_error"


def _log_provider_failure(stage: str) -> None:
    logging.getLogger(__name__).error(
        "oa_live_provider_failure stage=%s classification=adapter_error",
        stage,
    )


def _safe_message_center_payload_error(
    error: OALivePayloadInvalid,
) -> tuple[str, str]:
    message = str(error)
    reason = _MESSAGE_CENTER_PAYLOAD_REASON_CODES.get(message)
    if reason is None:
        return "OA response payload is invalid", "payload_invalid"
    return message, reason


def _log_message_center_payload_rejection(
    *,
    reason: str,
    page_number: int,
    payload: Mapping[str, Any] | None,
    aggregate_record_count: int,
    cursor: tuple[str, str],
    next_cursor: tuple[str, str],
) -> None:
    data = payload.get("data") if payload is not None else None
    page_record_count = len(data) if isinstance(data, list) else -1
    cursor_fields = (
        "".join(
            "1" if payload is not None and key in payload else "0"
            for key in ("maxtime", "mintime", "msgid")
        )
        if payload is not None
        else "000"
    )
    logging.getLogger(__name__).warning(
        "oa_live_message_center_payload_rejected reason=%s page=%d "
        "page_records=%d aggregate_records=%d cursor_fields=%s "
        "cursor_advanced=%s",
        reason,
        page_number,
        page_record_count,
        aggregate_record_count,
        cursor_fields,
        next_cursor != cursor,
    )
    data = None


def report_oa_structural_drift(report: OAStructuralDriftReport) -> None:
    """Emit only value-free structural metadata for production Live drift."""

    if report.matches:
        return
    logging.getLogger(__name__).warning(
        "oa_live_structural_drift algorithm=%s expected_sha256=%s "
        "actual_sha256=%s added=%s removed=%s changed=%s",
        report.algorithm,
        report.expected_sha256,
        report.actual_sha256,
        ",".join(node.path for node in report.added) or "-",
        ",".join(node.path for node in report.removed) or "-",
        ",".join(node.path for node in report.changed) or "-",
    )


def _raise_for_http_status(status_code: int) -> None:
    if 200 <= status_code < 300:
        return
    if status_code == 401:
        raise OALiveIdentityExpired("OA Session is no longer valid")
    if status_code == 403:
        raise OALivePermissionDenied("OA permission was denied")
    if 500 <= status_code < 600:
        raise OALiveHTTPServerError("OA returned a server error")
    raise OALiveRequestError("OA returned an unsupported HTTP status")


def _raise_for_business_error(payload: Mapping[str, Any]) -> None:
    if payload.get("loginstatus") is False:
        raise OALiveIdentityExpired("OA Session is no longer valid")
    for key in ("errorCode", "error_code", "code"):
        value = payload.get(key)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        normalized = str(value).strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in _SESSION_EXPIRED_CODES:
            raise OALiveIdentityExpired("OA Session is no longer valid")
        if normalized in _PERMISSION_DENIED_CODES:
            raise OALivePermissionDenied("OA permission was denied")


def _build_pending_workflows_split_form(
    *,
    actiontype: str,
    hide_no_data_tab: str,
    method: str,
    offical_type: str,
    view_scope: str,
) -> dict[str, str]:
    """Build the exact 34-field viewcondition=5 form observed onsite."""

    configured = {
        "method": _validate_pending_workflow_form_value(
            method, field_name="method"
        ),
        "officalType": _validate_pending_workflow_form_value(
            offical_type, field_name="officalType"
        ),
        "hideNoDataTab": _validate_pending_workflow_form_value(
            hide_no_data_tab, field_name="hideNoDataTab"
        ),
        "viewScope": _validate_pending_workflow_form_value(
            view_scope, field_name="viewScope"
        ),
        "actiontype": _validate_pending_workflow_form_value(
            actiontype, field_name="actiontype"
        ),
    }
    return {
        "method": configured["method"],
        "offical": "",
        "officalType": configured["officalType"],
        "hideNoDataTab": configured["hideNoDataTab"],
        "viewScope": configured["viewScope"],
        "complete": "0",
        "viewcondition": "5",
        "defaultTabVal": "0",
        "requestname": "",
        "wfcode": "",
        "workflowid": "",
        "createdateselect": "0",
        "createdatefrom": "",
        "createdateto": "",
        "creatertype": "0",
        "workcode": "",
        "doingStatus": "0",
        "ownerdepartmentid": "",
        "creatersubcompanyid": "",
        "workflowtype": "",
        "requestlevel": "",
        "recievedateselect": "0",
        "recievedatefrom": "",
        "recievedateto": "",
        "wfstatu": "1",
        "nodetype": "",
        "unophrmid": "",
        "docids": "",
        "hrmcreaterid": "",
        "crmids": "",
        "proids": "",
        "menuIds": "1,13",
        "menuPathIds": "1,13",
        "actiontype": configured["actiontype"],
    }


def _validate_pending_workflow_form_value(
    value: str,
    *,
    field_name: str,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 4_096
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ValueError(f"OA pending-workflow {field_name} is invalid")
    return value


def _consume_pending_workflow_value(
    value: Any,
    reader: Callable[[Any], Any],
    *,
    check_business_envelope: bool,
) -> tuple[Any, str | None]:
    """Return a parsed value or a safe error kind without retaining traceback data."""

    parsed_value: Any = None
    error_kind: str | None = None
    try:
        if check_business_envelope:
            _raise_for_business_error(value)
        parsed_value = reader(value)
    except OALiveIdentityExpired:
        error_kind = "identity_expired"
    except OALivePermissionDenied:
        error_kind = "permission_denied"
    except OALiveProviderError:
        error_kind = "payload_invalid"
    except Exception:
        _log_provider_failure("payload_processing")
        error_kind = "request_error"
    finally:
        value = None
        del reader
    return parsed_value, error_kind


def _raise_pending_workflow_payload_error(error_kind: str) -> NoReturn:
    if error_kind == "identity_expired":
        raise OALiveIdentityExpired("OA Session is no longer valid")
    if error_kind == "permission_denied":
        raise OALivePermissionDenied("OA permission was denied")
    if error_kind == "payload_invalid":
        raise OALivePayloadInvalid("OA pending-workflow response payload is invalid")
    raise OALiveRequestError("OA response processing failed")


def _read_pending_workflow_sessionkey(payload: Mapping[str, Any]) -> str:
    sessionkey = payload.get("sessionkey")
    if (
        not isinstance(sessionkey, str)
        or len(sessionkey) != _PENDING_WORKFLOW_SESSION_KEY_LENGTH
        or sessionkey != sessionkey.strip()
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in sessionkey
        )
    ):
        raise OALivePayloadInvalid(
            "OA pending-workflow split response session key is invalid"
        )
    return sessionkey


def _read_pending_workflow_count(payload: Mapping[str, Any]) -> int:
    if payload.get("status") is not True:
        raise OALivePayloadInvalid(
            "OA pending-workflow count response status is invalid"
        )
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise OALivePayloadInvalid(
            "OA pending-workflow authoritative count is invalid"
        )
    return count


def _read_pending_workflow_page(
    payload: Mapping[str, Any],
) -> tuple[list[Any], int]:
    if payload.get("status") is not True:
        raise OALivePayloadInvalid(
            "OA pending-workflow data response status is invalid"
        )
    records = payload.get("datas")
    page_size_raw = payload.get("pageSize")
    if not isinstance(records, list):
        raise OALivePayloadInvalid(
            "OA pending-workflow data response records are invalid"
        )
    if (
        not isinstance(page_size_raw, str)
        or not page_size_raw.isascii()
        or not page_size_raw.isdecimal()
        or len(page_size_raw) > len(str(_MAX_CONFIGURED_RECORDS))
    ):
        raise OALivePayloadInvalid(
            "OA pending-workflow data response page size is invalid"
        )
    page_size = int(page_size_raw)
    if not 1 <= page_size <= _MAX_CONFIGURED_RECORDS:
        raise OALivePayloadInvalid(
            "OA pending-workflow data response page size is invalid"
        )
    return list(records), page_size


def _pending_workflow_record_identifier(record: Any) -> str:
    if not isinstance(record, Mapping):
        raise OALivePayloadInvalid(
            "OA pending-workflow record must be an object"
        )
    identifier = record.get("requestid")
    if not isinstance(identifier, str) or not identifier.strip():
        raise OALivePayloadInvalid(
            "OA pending-workflow record identifier is invalid"
        )
    return identifier.strip()


def _validate_message_center_form(
    *,
    category_id: str,
    bizstate: str,
    select_state: str,
    allow_empty_category: bool,
) -> Mapping[str, str]:
    return {
        "id": _validate_message_center_form_value(
            category_id,
            name="category id",
            allow_empty=allow_empty_category,
        ),
        "bizstate": _validate_message_center_form_value(
            bizstate,
            name="business state",
            allow_empty=True,
        ),
        "selectState": _validate_message_center_form_value(
            select_state,
            name="selection state",
            allow_empty=True,
        ),
    }


def _validate_message_center_form_value(
    value: str,
    *,
    name: str,
    allow_empty: bool,
) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or (not value and not allow_empty)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"OA message-center {name} is invalid")
    return value


def _read_message_center_page(
    payload: Mapping[str, Any],
) -> tuple[list[Any], tuple[str, str]]:
    expected_keys = {"data", "maxtime", "mintime", "msgid", "status"}
    if set(payload) != expected_keys:
        raise OALivePayloadInvalid("OA message-center response shape changed")
    if payload.get("status") != "1":
        raise OALivePayloadInvalid("OA message-center response status is invalid")
    records = payload.get("data")
    if not isinstance(records, list):
        raise OALivePayloadInvalid("OA message-center response data is invalid")
    cursor_values: list[str] = []
    for key in ("maxtime", "mintime", "msgid"):
        value = payload.get(key)
        if not isinstance(value, str) or value != value.strip():
            cursor_values.clear()
            raise OALivePayloadInvalid(
                "OA message-center response cursor is invalid"
            )
        cursor_values.append(value)
    cursor = (cursor_values[2], cursor_values[1])
    cursor_values.clear()
    return records, cursor


def _message_center_record_fingerprint(record: Any) -> bytes:
    identity = ""
    serialized = ""
    try:
        if isinstance(record, Mapping):
            # Both message-center capabilities key on the same wire id.
            for key in ("messageid",):
                value = record.get(key)
                if isinstance(value, str) and value:
                    identity = f"{key}\u0000{value}"
                    break
        if not identity:
            serialized = json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity = f"record\u0000{serialized}"
        return hashlib.sha256(identity.encode("utf-8")).digest()
    finally:
        identity = ""
        serialized = ""


__all__ = (
    "LiveOAReadProvider",
    "LiveOAReadProviderNotImplemented",
    "OAContractPackError",
    "OAContractPackPayloadInvalid",
    "OALiveHTTPServerError",
    "OALiveIdentityExpired",
    "OALiveIdentityUnbound",
    "OALivePayloadInvalid",
    "OALivePermissionDenied",
    "OALiveProviderError",
    "OALiveRequestError",
    "OALiveTimeout",
    "OAReadProvider",
    "OAStructuralDriftReporter",
    "ReplayOAReadProvider",
    "report_oa_structural_drift",
)
