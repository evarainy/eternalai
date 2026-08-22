"""Fail-fast production configuration loaded from process environment."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias, cast
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.config import get_database_url

_DEFAULT_LLM_BASE_URL = "http://34.74.11.38:8011/v1"
_DEFAULT_LLM_MODEL = "glm-4.7"
_DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
_DEFAULT_LLM_MAX_TOKENS = 2048
_DEFAULT_LLM_TEMPERATURE = 0.6
_DEFAULT_LLM_TOP_P = 0.95
_DEFAULT_LLM_TOP_K = 20
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0
_MAX_HEALTH_TIMEOUT_SECONDS = 60.0
_DEFAULT_OA_TIMEOUT_SECONDS = 30.0
_DEFAULT_CREDENTIAL_POLL_INTERVAL_SECONDS = 600
_DEFAULT_CREDENTIAL_POLL_MAXIMUM_BACKOFF_SECONDS = 3600
_DEFAULT_CREDENTIAL_POLL_WORK_START_HOUR = 8
_DEFAULT_CREDENTIAL_POLL_WORK_END_HOUR = 18
_DEFAULT_CREDENTIAL_POLL_TIMEZONE = "Asia/Shanghai"
_DEFAULT_CREDENTIAL_POLL_GLOBAL_CONCURRENCY = 4
_DEFAULT_CREDENTIAL_POLL_SCHEDULER_TICK_SECONDS = 60
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
OAReadAdapterMode: TypeAlias = Literal["mock", "replay", "live"]


class _SecretValue:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __deepcopy__(self, _memo: object) -> _SecretValue:
        return self

    def __repr__(self) -> str:
        return "'***'"

    def __str__(self) -> str:
        return "***"


@dataclass(frozen=True, slots=True)
class RedisConnectionURL:
    """Parsed Redis connection data with a uniformly redacted string form."""

    scheme: str
    host: str
    port: int
    username: str | None
    _password: _SecretValue | None = field(repr=False)
    database: int

    @classmethod
    def parse(cls, value: str) -> RedisConnectionURL:
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise ValueError("REDIS_URL is invalid") from None
        location = _redis_location(parsed.scheme, parsed.hostname)
        if (
            parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"REDIS_URL must be a redis:// or rediss:// URL for {location}"
            )
        try:
            port = parsed.port or 6379
        except ValueError:
            raise ValueError(f"REDIS_URL port is invalid for {location}") from None
        path = parsed.path.lstrip("/")
        if path and (not path.isascii() or not path.isdigit()):
            raise ValueError(
                f"REDIS_URL database must be a non-negative integer for {location}"
            )
        username = (
            unquote(parsed.username) if parsed.username is not None else None
        )
        password = (
            _SecretValue(unquote(parsed.password))
            if parsed.password is not None
            else None
        )
        if username is not None and password is None:
            raise ValueError(f"REDIS_URL username requires a password for {location}")
        return cls(
            scheme=parsed.scheme,
            host=parsed.hostname,
            port=port,
            username=username,
            _password=password,
            database=int(path) if path else 0,
        )

    def password_for_connection(self) -> str | None:
        return self._password.reveal() if self._password is not None else None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"

    def __str__(self) -> str:
        credentials = ""
        if self._password is not None:
            encoded_username = (
                quote(self.username, safe="") if self.username is not None else ""
            )
            credentials = f"{encoded_username}:***@"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return (
            f"{self.scheme}://{credentials}{host}:{self.port}/{self.database}"
        )


@dataclass(frozen=True, slots=True)
class ProductionSettings:
    """Validated values needed to construct the production application."""

    environment_name: str
    database_url: str = field(repr=False)
    redis_url: RedisConnectionURL
    oa_base_url: str
    oa_timeout_seconds: float
    oa_credential_ttl_seconds: int
    session_cookie_ttl_seconds: int
    session_cookie_secure: bool
    credential_encryption_key: bytes = field(repr=False)
    identity_hmac_key: bytes = field(repr=False)
    session_signing_key: bytes = field(repr=False)
    session_binding_key: bytes = field(repr=False)
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: float
    llm_max_tokens: int
    llm_temperature: float
    llm_top_p: float
    llm_top_k: int
    llm_enable_thinking: bool
    health_timeout_seconds: float
    csrf_allowed_origins: frozenset[str] = field(default_factory=frozenset)
    oa_read_adapter_mode: OAReadAdapterMode = "mock"
    oa_read_contract_pack_dir: Path | None = None
    oa_pending_workflows_contract_pack_dir: Path | None = None
    oa_system_messages_contract_pack_dir: Path | None = None
    oa_message_center_path: str | None = None
    oa_pending_workflows_split_page_key_path: str | None = None
    oa_pending_workflows_counts_path: str | None = None
    oa_pending_workflows_datas_path: str | None = None
    oa_pending_workflows_actiontype: str | None = None
    oa_pending_workflows_hide_no_data_tab: str | None = None
    oa_pending_workflows_method: str | None = None
    oa_pending_workflows_offical_type: str | None = None
    oa_pending_workflows_view_scope: str | None = None
    oa_pending_workflows_sort_params: str | None = None
    oa_system_messages_category_id: str | None = None
    oa_system_messages_bizstate: str | None = None
    oa_system_messages_select_state: str | None = None
    oa_message_center_page_size: int = 20
    credential_poll_interval_seconds: int = _DEFAULT_CREDENTIAL_POLL_INTERVAL_SECONDS
    credential_poll_maximum_backoff_seconds: int = (
        _DEFAULT_CREDENTIAL_POLL_MAXIMUM_BACKOFF_SECONDS
    )
    credential_poll_work_start_hour: int = _DEFAULT_CREDENTIAL_POLL_WORK_START_HOUR
    credential_poll_work_end_hour: int = _DEFAULT_CREDENTIAL_POLL_WORK_END_HOUR
    credential_poll_timezone: str = _DEFAULT_CREDENTIAL_POLL_TIMEZONE
    credential_poll_global_concurrency: int = (
        _DEFAULT_CREDENTIAL_POLL_GLOBAL_CONCURRENCY
    )
    credential_poll_scheduler_tick_seconds: int = (
        _DEFAULT_CREDENTIAL_POLL_SCHEDULER_TICK_SECONDS
    )
    phase0_mock_mode: bool = False

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> ProductionSettings:
        source = os.environ if environment is None else environment
        environment_name = (
            source.get("ENV", "production").strip().casefold()
            or "production"
        )
        oa_read_adapter_mode = _oa_read_adapter_mode(source)
        oa_read_contract_pack_dir = _oa_read_contract_pack_dir(
            source,
            oa_read_adapter_mode,
        )
        phase0_mock_mode = _boolean(
            source,
            "PHASE0_MOCK_MODE",
            default=False,
        )
        session_cookie_secure = _boolean(
            source,
            "SESSION_COOKIE_SECURE",
            default=True,
        )
        csrf_allowed_origins = _csrf_allowed_origins(source)
        if not session_cookie_secure and any(
            origin.startswith("https://") for origin in csrf_allowed_origins
        ):
            raise RuntimeError(
                "session_cookie_transport_invalid: "
                "SESSION_COOKIE_SECURE=false requires every "
                "CSRF_ALLOWED_ORIGINS entry to use http://"
            )
        if (
            oa_read_adapter_mode == "mock"
            and environment_name != "testing"
            and not phase0_mock_mode
        ):
            raise RuntimeError(
                "OA_READ_ADAPTER_MODE=mock requires ENV=testing "
                "or PHASE0_MOCK_MODE=true"
            )
        settings = cls(
            environment_name=environment_name,
            database_url=get_database_url(source),
            redis_url=_redis_url(source),
            oa_base_url=_http_base_url(source, "OA_BASE_URL"),
            oa_timeout_seconds=_positive_float(
                source,
                "OA_TIMEOUT_S",
                _DEFAULT_OA_TIMEOUT_SECONDS,
            ),
            oa_credential_ttl_seconds=_required_positive_int(
                source,
                "OA_CREDENTIAL_TTL_S",
            ),
            session_cookie_ttl_seconds=_required_positive_int(
                source,
                "SESSION_COOKIE_TTL_S",
            ),
            session_cookie_secure=session_cookie_secure,
            credential_encryption_key=_base64_key(
                source,
                "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
                exact_bytes=32,
            ),
            identity_hmac_key=_base64_key(
                source,
                "ETERNALAI_IDENTITY_HMAC_KEY_B64",
                minimum_bytes=32,
            ),
            session_signing_key=_base64_key(
                source,
                "ETERNALAI_SESSION_SIGNING_KEY_B64",
                minimum_bytes=32,
            ),
            session_binding_key=_base64_key(
                source,
                "ETERNALAI_SESSION_BINDING_KEY_B64",
                minimum_bytes=32,
            ),
            llm_base_url=_http_base_url(
                source,
                "LLM_BASE_URL",
                default=_DEFAULT_LLM_BASE_URL,
            ),
            llm_model=_required(source, "LLM_MODEL", default=_DEFAULT_LLM_MODEL),
            llm_timeout_seconds=_positive_float(
                source,
                "LLM_TIMEOUT_S",
                _DEFAULT_LLM_TIMEOUT_SECONDS,
            ),
            llm_max_tokens=_positive_int(
                source,
                "LLM_MAX_TOKENS",
                _DEFAULT_LLM_MAX_TOKENS,
            ),
            llm_temperature=_bounded_float(
                source,
                "LLM_TEMPERATURE",
                _DEFAULT_LLM_TEMPERATURE,
                minimum=0.0,
                maximum=2.0,
            ),
            llm_top_p=_bounded_float(
                source,
                "LLM_TOP_P",
                _DEFAULT_LLM_TOP_P,
                minimum=0.0,
                maximum=1.0,
                minimum_inclusive=False,
            ),
            llm_top_k=_positive_int(
                source,
                "LLM_TOP_K",
                _DEFAULT_LLM_TOP_K,
            ),
            llm_enable_thinking=_boolean(
                source,
                "LLM_ENABLE_THINKING",
                default=False,
            ),
            health_timeout_seconds=_bounded_float(
                source,
                "HEALTH_TIMEOUT_S",
                _DEFAULT_HEALTH_TIMEOUT_SECONDS,
                minimum=0.0,
                maximum=_MAX_HEALTH_TIMEOUT_SECONDS,
                minimum_inclusive=False,
            ),
            csrf_allowed_origins=csrf_allowed_origins,
            oa_read_adapter_mode=oa_read_adapter_mode,
            oa_read_contract_pack_dir=oa_read_contract_pack_dir,
            oa_pending_workflows_contract_pack_dir=_oa_live_contract_pack_dir(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
            ),
            oa_system_messages_contract_pack_dir=_oa_live_contract_pack_dir(
                source,
                oa_read_adapter_mode,
                "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
            ),
            oa_message_center_path=_oa_capability_path(
                source,
                oa_read_adapter_mode,
                "OA_MESSAGE_CENTER_PATH",
            ),
            oa_pending_workflows_split_page_key_path=_oa_capability_path(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_SPLIT_PAGE_KEY_PATH",
            ),
            oa_pending_workflows_counts_path=_oa_capability_path(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_COUNTS_PATH",
            ),
            oa_pending_workflows_datas_path=_oa_capability_path(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_DATAS_PATH",
            ),
            oa_pending_workflows_actiontype=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_ACTIONTYPE",
                allow_empty=False,
            ),
            oa_pending_workflows_hide_no_data_tab=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_HIDE_NO_DATA_TAB",
                allow_empty=False,
            ),
            oa_pending_workflows_method=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_METHOD",
                allow_empty=False,
            ),
            oa_pending_workflows_offical_type=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_OFFICAL_TYPE",
                allow_empty=False,
            ),
            oa_pending_workflows_view_scope=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_VIEW_SCOPE",
                allow_empty=False,
            ),
            oa_pending_workflows_sort_params=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_PENDING_WORKFLOWS_SORT_PARAMS",
                allow_empty=False,
            ),
            oa_system_messages_category_id=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_SYSTEM_MESSAGES_CATEGORY_ID",
                allow_empty=True,
            ),
            oa_system_messages_bizstate=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_SYSTEM_MESSAGES_BIZSTATE",
                allow_empty=True,
            ),
            oa_system_messages_select_state=_oa_live_form_parameter(
                source,
                oa_read_adapter_mode,
                "OA_SYSTEM_MESSAGES_SELECT_STATE",
                allow_empty=True,
            ),
            oa_message_center_page_size=_bounded_positive_int(
                source,
                "OA_MESSAGE_CENTER_PAGE_SIZE",
                20,
                maximum=1_000,
            ),
            credential_poll_interval_seconds=_bounded_positive_int(
                source,
                "CREDENTIAL_POLL_INTERVAL_S",
                _DEFAULT_CREDENTIAL_POLL_INTERVAL_SECONDS,
                minimum=600,
                maximum=86_400,
            ),
            credential_poll_maximum_backoff_seconds=_bounded_positive_int(
                source,
                "CREDENTIAL_POLL_MAXIMUM_BACKOFF_S",
                _DEFAULT_CREDENTIAL_POLL_MAXIMUM_BACKOFF_SECONDS,
                minimum=600,
                maximum=604_800,
            ),
            credential_poll_work_start_hour=_integer_in_range(
                source,
                "CREDENTIAL_POLL_WORK_START_HOUR",
                _DEFAULT_CREDENTIAL_POLL_WORK_START_HOUR,
                minimum=0,
                maximum=23,
            ),
            credential_poll_work_end_hour=_integer_in_range(
                source,
                "CREDENTIAL_POLL_WORK_END_HOUR",
                _DEFAULT_CREDENTIAL_POLL_WORK_END_HOUR,
                minimum=1,
                maximum=24,
            ),
            credential_poll_timezone=_required(
                source,
                "CREDENTIAL_POLL_TIMEZONE",
                default=_DEFAULT_CREDENTIAL_POLL_TIMEZONE,
            ),
            credential_poll_global_concurrency=_bounded_positive_int(
                source,
                "CREDENTIAL_POLL_GLOBAL_CONCURRENCY",
                _DEFAULT_CREDENTIAL_POLL_GLOBAL_CONCURRENCY,
                maximum=32,
            ),
            credential_poll_scheduler_tick_seconds=_bounded_positive_int(
                source,
                "CREDENTIAL_POLL_SCHEDULER_TICK_S",
                _DEFAULT_CREDENTIAL_POLL_SCHEDULER_TICK_SECONDS,
                maximum=600,
            ),
            phase0_mock_mode=phase0_mock_mode,
        )
        if (
            settings.credential_poll_maximum_backoff_seconds
            < settings.credential_poll_interval_seconds
        ):
            raise RuntimeError(
                "CREDENTIAL_POLL_MAXIMUM_BACKOFF_S must be at least "
                "CREDENTIAL_POLL_INTERVAL_S"
            )
        if (
            settings.credential_poll_work_start_hour
            >= settings.credential_poll_work_end_hour
        ):
            raise RuntimeError(
                "CREDENTIAL_POLL_WORK_START_HOUR must be earlier than "
                "CREDENTIAL_POLL_WORK_END_HOUR"
            )
        try:
            ZoneInfo(settings.credential_poll_timezone)
        except ZoneInfoNotFoundError:
            raise RuntimeError("CREDENTIAL_POLL_TIMEZONE is invalid") from None
        return settings


def _required(
    source: Mapping[str, str],
    name: str,
    *,
    default: str | None = None,
) -> str:
    value = source.get(name, default)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


def _redis_url(source: Mapping[str, str]) -> RedisConnectionURL:
    try:
        return RedisConnectionURL.parse(_required(source, "REDIS_URL"))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None


def _redis_location(scheme: str, host: str | None) -> str:
    safe_scheme = scheme if scheme in {"redis", "rediss"} else "redis"
    safe_host = host or "<unknown-host>"
    if ":" in safe_host:
        safe_host = f"[{safe_host}]"
    return f"{safe_scheme}://{safe_host}"


def _http_base_url(
    source: Mapping[str, str],
    name: str,
    *,
    default: str | None = None,
) -> str:
    value = _required(source, name, default=default).rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{name} must be an HTTP(S) base URL without credentials")
    return value


def _csrf_allowed_origins(source: Mapping[str, str]) -> frozenset[str]:
    name = "CSRF_ALLOWED_ORIGINS"
    raw = source.get(name)
    if raw is None or not raw.strip():
        raise RuntimeError(f"{name} is required")
    if raw != raw.strip():
        raise RuntimeError(f"{name} must contain canonical HTTP(S) origins")

    origins = raw.split(",")
    if any(not origin or origin != origin.strip() for origin in origins):
        raise RuntimeError(f"{name} must not contain empty or padded entries")

    canonical_origins = [_canonical_http_origin(origin, name) for origin in origins]
    if len(canonical_origins) != len(set(canonical_origins)):
        raise RuntimeError(f"{name} must not contain duplicate origins")
    return frozenset(canonical_origins)


def _canonical_http_origin(value: str, name: str) -> str:
    if "*" in value:
        raise RuntimeError(f"{name} does not allow wildcard origins")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise RuntimeError(f"{name} must contain canonical HTTP(S) origins") from None
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{name} must contain origin-only HTTP(S) URLs")

    canonical_host = _canonical_origin_host(hostname, name)
    default_port = 80 if parsed.scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    host_for_url = (
        f"[{canonical_host}]" if ":" in canonical_host else canonical_host
    )
    canonical = f"{parsed.scheme}://{host_for_url}{port_suffix}"
    if value != canonical:
        raise RuntimeError(f"{name} must contain canonical HTTP(S) origins")
    return canonical


def _canonical_origin_host(hostname: str, name: str) -> str:
    if not hostname.isascii() or "%" in hostname:
        raise RuntimeError(f"{name} must contain canonical ASCII hostnames")
    if ":" in hostname:
        try:
            return ipaddress.IPv6Address(hostname).compressed
        except ipaddress.AddressValueError:
            raise RuntimeError(f"{name} contains an invalid hostname") from None
    if hostname.replace(".", "").isdigit():
        try:
            return str(ipaddress.IPv4Address(hostname))
        except ipaddress.AddressValueError:
            raise RuntimeError(f"{name} contains an invalid hostname") from None
    if len(hostname) > 253 or any(
        _HOST_LABEL.fullmatch(label) is None for label in hostname.split(".")
    ):
        raise RuntimeError(f"{name} contains an invalid hostname")
    return hostname


def _oa_read_adapter_mode(source: Mapping[str, str]) -> OAReadAdapterMode:
    value = source.get("OA_READ_ADAPTER_MODE", "mock").strip().casefold()
    if value not in {"mock", "replay", "live"}:
        raise RuntimeError("OA_READ_ADAPTER_MODE must be mock, replay, or live")
    return cast(OAReadAdapterMode, value)


def _oa_read_contract_pack_dir(
    source: Mapping[str, str],
    mode: OAReadAdapterMode,
) -> Path | None:
    raw = source.get("OA_READ_CONTRACT_PACK_DIR")
    if raw is None or not raw.strip():
        if mode == "replay":
            raise RuntimeError("OA_READ_CONTRACT_PACK_DIR is required for replay mode")
        return None
    return Path(raw.strip())


def _oa_live_contract_pack_dir(
    source: Mapping[str, str],
    mode: OAReadAdapterMode,
    name: str,
) -> Path | None:
    raw = source.get(name)
    if raw is None or not raw.strip():
        if mode == "live":
            raise RuntimeError(f"{name} is required for live mode")
        return None
    return Path(raw.strip())


def _oa_capability_path(
    source: Mapping[str, str],
    mode: OAReadAdapterMode,
    name: str,
) -> str | None:
    raw = source.get(name)
    if raw is None or not raw.strip():
        if mode == "live":
            raise RuntimeError(f"{name} is required for live mode")
        return None
    value = raw.strip()
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or any(part == ".." for part in parsed.path.split("/"))
    ):
        raise RuntimeError(f"{name} must be a relative path on the OA host")
    return value


def _oa_live_form_parameter(
    source: Mapping[str, str],
    mode: OAReadAdapterMode,
    name: str,
    *,
    allow_empty: bool,
) -> str | None:
    if name not in source:
        if mode == "live":
            raise RuntimeError(f"{name} is required for live mode")
        return None
    value = source[name].strip()
    if not value and not allow_empty:
        if mode == "live":
            raise RuntimeError(f"{name} is required for live mode")
        return None
    return value


def _required_positive_int(source: Mapping[str, str], name: str) -> int:
    raw = _required(source, name)
    return _parse_positive_int(raw, name)


def _positive_int(
    source: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = source.get(name)
    if raw is None:
        return default
    return _parse_positive_int(raw, name)


def _bounded_positive_int(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int,
) -> int:
    value = _positive_int(source, name, default)
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    if value > maximum:
        raise RuntimeError(f"{name} must be at most {maximum}")
    return value


def _integer_in_range(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = source.get(name, str(default)).strip()
    if not raw.isascii() or not raw.isdigit():
        raise RuntimeError(f"{name} must be an integer")
    value = int(raw)
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _parse_positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def _positive_float(
    source: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    return _bounded_float(
        source,
        name,
        default,
        minimum=0.0,
        maximum=float("inf"),
        minimum_inclusive=False,
    )


def _bounded_float(
    source: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
    minimum_inclusive: bool = True,
) -> float:
    raw = source.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} is invalid") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"{name} must be finite")
    minimum_ok = value >= minimum if minimum_inclusive else value > minimum
    if not minimum_ok or value > maximum:
        raise RuntimeError(f"{name} is outside the allowed range")
    return value


def _boolean(
    source: Mapping[str, str],
    name: str,
    *,
    default: bool,
) -> bool:
    raw = source.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _base64_key(
    source: Mapping[str, str],
    name: str,
    *,
    exact_bytes: int | None = None,
    minimum_bytes: int | None = None,
) -> bytes:
    encoded = _required(source, name)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(f"{name} must contain valid base64") from exc
    if exact_bytes is not None and len(decoded) != exact_bytes:
        raise RuntimeError(f"{name} must decode to exactly {exact_bytes} bytes")
    if minimum_bytes is not None and len(decoded) < minimum_bytes:
        raise RuntimeError(f"{name} must decode to at least {minimum_bytes} bytes")
    return decoded


__all__ = ("OAReadAdapterMode", "ProductionSettings", "RedisConnectionURL")
