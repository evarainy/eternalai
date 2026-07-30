"""Replay and bounded standard-library Live providers for OA reads."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, Protocol
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
    OAPendingWorkflowCollection,
    OAStructuralDriftReport,
    build_live_pending_workflows_fingerprint,
    build_structural_fingerprint,
    compare_structural_fingerprints,
    normalize_pending_workflow_records,
)
from app.ports.auth import OASessionCredential

_DEFAULT_PAGE_SIZE = 100
_DEFAULT_MAX_PAGES = 50
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DEFAULT_PAGINATION_SIGNAL_KEYS = frozenset(
    {"hasMore", "total", "nextCursor"}
)
_KNOWN_PAGINATION_SIGNAL_KEYS = frozenset(
    {"hasMore", "has_more", "total", "nextCursor", "next_cursor"}
)
_MAX_CONFIGURED_PAGES = 1_000
_MAX_CONFIGURED_RESPONSE_BYTES = 32 * 1024 * 1024
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
_MISSING = object()


class OAReadProvider(Protocol):
    """Narrow provider seam behind the real OA adapter."""

    requires_credential: bool

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection: ...


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
        return collection


class LiveOAReadProvider:
    """Fetch and normalize one user-delegated OA read with bounded pagination."""

    requires_credential = True

    def __init__(
        self,
        *,
        base_url: str,
        endpoint_path: str,
        timeout_seconds: float,
        contract_pack_dir: Path,
        drift_reporter: OAStructuralDriftReporter | None = None,
        max_pages: int = _DEFAULT_MAX_PAGES,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        pagination_signal_keys: frozenset[str] = _DEFAULT_PAGINATION_SIGNAL_KEYS,
        opener_factory: Callable[[], OpenerDirector] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._base_url, self._allowed_origin = _validate_base_url(base_url)
        self._endpoint_path = _validate_endpoint_path(endpoint_path)
        if timeout_seconds <= 0:
            raise ValueError("OA HTTP timeout must be positive")
        if not 1 <= max_pages <= _MAX_CONFIGURED_PAGES:
            raise ValueError("OA maximum page count is outside the allowed range")
        if not 1 <= max_response_bytes <= _MAX_CONFIGURED_RESPONSE_BYTES:
            raise ValueError("OA response size limit is outside the allowed range")
        _collection, expected_fingerprint = _load_contract_pack(contract_pack_dir)

        self._timeout_seconds = timeout_seconds
        self._max_pages = max_pages
        self._max_response_bytes = max_response_bytes
        self._pagination_signal_keys = _validate_pagination_signal_keys(
            pagination_signal_keys
        )
        self._expected_fingerprint = expected_fingerprint
        self._drift_reporter = drift_reporter
        self._opener_factory = opener_factory or self._build_isolated_opener
        self._clock = clock

    async def list_pending_workflows(
        self,
        credential: OASessionCredential | None = None,
    ) -> OAPendingWorkflowCollection:
        if credential is None:
            raise OALiveIdentityUnbound("OA credential is required")
        cookie_header = ""
        opener: OpenerDirector | None = None
        raw_records: list[Any] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        payload: dict[str, Any] | None = None
        page_records: list[Any] = []
        pagination: Mapping[str, Any] = {}
        next_cursor: str | None = None
        expected_total: int | None = None
        collection: OAPendingWorkflowCollection | None = None

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
            for page_number in range(1, self._max_pages + 1):
                assert opener is not None
                payload = await self._request_page(
                    opener=opener,
                    cookie_header=cookie_header,
                    page_number=page_number,
                    cursor=cursor,
                )
                page_error_kind: str | None = None
                is_complete = False
                try:
                    _raise_for_business_error(payload)
                    page_records, pagination = _read_page(payload)
                    if len(page_records) > _DEFAULT_PAGE_SIZE:
                        raise OALivePayloadInvalid(
                            "OA page exceeds the record limit"
                        )
                    aggregate_record_count = len(raw_records) + len(page_records)
                    if (
                        aggregate_record_count
                        > self._max_pages * _DEFAULT_PAGE_SIZE
                    ):
                        raise OALivePayloadInvalid(
                            "OA aggregate exceeds the record limit"
                        )
                    is_complete, next_cursor, page_total = _resolve_pagination(
                        pagination,
                        expected_signal_keys=self._pagination_signal_keys,
                        page_record_count=len(page_records),
                        aggregate_record_count=aggregate_record_count,
                    )
                    if page_total is not None:
                        if expected_total is None:
                            expected_total = page_total
                        elif page_total != expected_total:
                            raise OALivePayloadInvalid(
                                "OA total signal changed between pages"
                            )
                except OALiveIdentityExpired:
                    page_error_kind = "identity_expired"
                except OALivePermissionDenied:
                    page_error_kind = "permission_denied"
                except OALiveProviderError:
                    page_error_kind = "payload_invalid"
                except Exception:
                    _log_provider_failure("payload_processing")
                    page_error_kind = "request_error"
                if page_error_kind is not None:
                    payload = None
                    page_records.clear()
                    pagination = {}
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
                    raise OALivePayloadInvalid("OA response payload is invalid")
                raw_records.extend(page_records)
                if is_complete:
                    break
                if page_number == self._max_pages:
                    raise OALivePayloadInvalid("OA pagination exceeds the page limit")
                if not page_records:
                    raise OALivePayloadInvalid(
                        "OA pagination cannot continue after an empty page"
                    )
                if next_cursor is not None:
                    if next_cursor in seen_cursors:
                        raise OALivePayloadInvalid("OA pagination cursor repeated")
                    seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:  # pragma: no cover - defensive; the loop always breaks or raises
                raise OALivePayloadInvalid("OA pagination did not terminate")

            try:
                actual_fingerprint = (
                    build_live_pending_workflows_fingerprint(raw_records)
                )
                drift_report = compare_structural_fingerprints(
                    self._expected_fingerprint,
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
                collection = normalize_pending_workflow_records(raw_records)
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
            cookie_header = ""
            credential = None
            opener = None
            payload = None
            page_records.clear()
            pagination = {}
            raw_records.clear()
            seen_cursors.clear()
            cursor = None
            next_cursor = None
            expected_total = None
            collection = None

    def _build_isolated_opener(self) -> OpenerDirector:
        return build_opener(
            ProxyHandler({}),
            _SameOriginRedirectHandler(self._allowed_origin),
        )

    async def _request_page(
        self,
        *,
        opener: OpenerDirector,
        cookie_header: str,
        page_number: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        parameters = {
            "page": str(page_number),
            "pageSize": str(_DEFAULT_PAGE_SIZE),
        }
        if cursor is not None:
            parameters["cursor"] = cursor
        payload: dict[str, Any] | None = None
        error_kind: str | None = None
        request: Request | None = None
        try:
            request = Request(
                f"{self._base_url}{self._endpoint_path}?{urlencode(parameters)}",
                headers={
                    "Accept": "application/json",
                    "Cookie": cookie_header,
                    "User-Agent": "EternalAI-OA-Read/1",
                },
                method="GET",
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
            parameters.clear()
            del cursor
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
) -> tuple[OAPendingWorkflowCollection, dict[str, Any]]:
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

    try:
        collection = OAPendingWorkflowCollection.model_validate(
            sample_payload,
            strict=True,
        )
    except ValidationError:
        raise OAContractPackPayloadInvalid(
            "Contract Pack sample violates the normalized OA model"
        ) from None
    if not isinstance(fingerprint_payload, dict):
        raise OAContractPackError("Contract Pack fingerprint is invalid")
    return collection, {str(key): value for key, value in fingerprint_payload.items()}


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


def _read_page(
    payload: Mapping[str, Any],
) -> tuple[list[Any], Mapping[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise OALivePayloadInvalid("OA response data is invalid")
    records = data.get("records")
    if not isinstance(records, list):
        raise OALivePayloadInvalid("OA response records are invalid")
    return records, data


def _resolve_pagination(
    pagination: Mapping[str, Any],
    *,
    expected_signal_keys: frozenset[str],
    page_record_count: int,
    aggregate_record_count: int,
) -> tuple[bool, str | None, int | None]:
    observed_signal_keys = frozenset(
        key for key in _KNOWN_PAGINATION_SIGNAL_KEYS if key in pagination
    )
    if observed_signal_keys != expected_signal_keys:
        raise OALivePayloadInvalid("OA pagination signal shape changed")
    completion_signals: list[bool] = []

    has_more = _read_alias(pagination, "hasMore", "has_more")
    if has_more is not _MISSING:
        if not isinstance(has_more, bool):
            raise OALivePayloadInvalid("OA has-more signal is invalid")
        completion_signals.append(not has_more)

    total = pagination.get("total", _MISSING)
    validated_total: int | None = None
    if total is not _MISSING:
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise OALivePayloadInvalid("OA total signal is invalid")
        if aggregate_record_count > total:
            raise OALivePayloadInvalid("OA total signal contradicts the records")
        validated_total = total
        completion_signals.append(aggregate_record_count == total)

    next_cursor_raw = _read_alias(pagination, "nextCursor", "next_cursor")
    next_cursor: str | None = None
    if next_cursor_raw is not _MISSING:
        if next_cursor_raw is None or next_cursor_raw == "":
            completion_signals.append(True)
        elif (
            isinstance(next_cursor_raw, str)
            and next_cursor_raw
            and next_cursor_raw == next_cursor_raw.strip()
        ):
            next_cursor = next_cursor_raw
            completion_signals.append(False)
        else:
            raise OALivePayloadInvalid("OA next-cursor signal is invalid")

    if not completion_signals:
        raise OALivePayloadInvalid("OA pagination has no termination signal")
    if any(signal != completion_signals[0] for signal in completion_signals[1:]):
        raise OALivePayloadInvalid("OA pagination signals contradict each other")
    if not completion_signals[0] and page_record_count == 0:
        raise OALivePayloadInvalid("OA pagination cannot continue after an empty page")
    return completion_signals[0], next_cursor, validated_total


def _validate_pagination_signal_keys(
    value: frozenset[str],
) -> frozenset[str]:
    if (
        not isinstance(value, frozenset)
        or not value
        or not value.issubset(_KNOWN_PAGINATION_SIGNAL_KEYS)
        or {"hasMore", "has_more"}.issubset(value)
        or {"nextCursor", "next_cursor"}.issubset(value)
    ):
        raise ValueError("OA pagination signal profile is invalid")
    return value


def _read_alias(
    payload: Mapping[str, Any],
    camel_case: str,
    snake_case: str,
) -> Any:
    camel_value = payload.get(camel_case, _MISSING)
    snake_value = payload.get(snake_case, _MISSING)
    if camel_value is not _MISSING and snake_value is not _MISSING:
        if camel_value != snake_value:
            raise OALivePayloadInvalid("OA pagination aliases contradict each other")
        return camel_value
    if camel_value is not _MISSING:
        return camel_value
    return snake_value


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
