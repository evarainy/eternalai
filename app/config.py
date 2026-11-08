"""Fail-fast production configuration loaded from process environment."""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import math
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias, cast
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, ValidationError

from app.db.config import get_database_url

if TYPE_CHECKING:
    # Runtime import stays in _mcp_services so WorkerSettings never loads MCP modules.
    from app.mcp.models import ServiceConfig

_DEFAULT_LLM_BASE_URL = "http://34.74.11.38:8011/v1"
_DEFAULT_LLM_MODEL = "glm-4.7"
_DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
_DEFAULT_LLM_MAX_TOKENS = 2048
_DEFAULT_LLM_TEMPERATURE = 0.6
_DEFAULT_LLM_TOP_P = 0.95
_DEFAULT_LLM_TOP_K = 20
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0
_MAX_HEALTH_TIMEOUT_SECONDS = 60.0
_MAX_SELF_CHECK_HOLD_SECONDS = 600
_FILE_ARTIFACT_ROOT_KEY = "FILE_ARTIFACT_ROOT"
_FILE_ARTIFACT_MAC_KEY = "ETERNALAI_FILE_ARTIFACT_MAC_KEY_B64"
_FILE_ARTIFACT_RETENTION_KEY = "FILE_ARTIFACT_RETENTION_SECONDS"
_MIN_FILE_ARTIFACT_RETENTION_SECONDS = 300
_MAX_FILE_ARTIFACT_RETENTION_SECONDS = 1209600
_DEFAULT_OA_TIMEOUT_SECONDS = 30.0
_DEFAULT_SESSION_RENEW_MIN_INTERVAL_SECONDS = 300
_DEFAULT_OA_USER_PROFILE_PATH = "/api/hrm/resource/getResourceBaseTitle"
# Identity is read on every page load, so it gets a tighter ceiling than the
# capability reads: a slow OA must not stretch the first paint.
_DEFAULT_OA_PROFILE_TIMEOUT_SECONDS = 3.0
_DEFAULT_CREDENTIAL_POLL_INTERVAL_SECONDS = 600
_DEFAULT_CREDENTIAL_POLL_MAXIMUM_BACKOFF_SECONDS = 3600
_DEFAULT_CREDENTIAL_POLL_WORK_START_HOUR = 8
_DEFAULT_CREDENTIAL_POLL_WORK_END_HOUR = 18
_DEFAULT_CREDENTIAL_POLL_TIMEZONE = "Asia/Shanghai"
_DEFAULT_CREDENTIAL_POLL_GLOBAL_CONCURRENCY = 4
_DEFAULT_CREDENTIAL_POLL_SCHEDULER_TICK_SECONDS = 60
_DEFAULT_OA_HEARTBEAT_PATH = "/api/hrm/common/heartbeat"
_DEFAULT_OA_HEARTBEAT_INTERVAL_SECONDS = 60
_DEFAULT_OA_RECOVERY_COOLDOWN_SECONDS = 60
_DEFAULT_OA_RECOVERY_WAIT_SECONDS = 15
_DEFAULT_OA_RECOVERY_KICK_WINDOW_SECONDS = 300
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
OAReadAdapterMode: TypeAlias = Literal["mock", "replay", "live"]
RuntimeEngine: TypeAlias = Literal["legacy", "agentscope"]
MCP_TLS_CA_ENDPOINT = "https://34.64.8.101/mcp"
MCP_TLS_CA_OAUTH_ENDPOINTS = frozenset({
    "https://34.64.8.101/oauth/register",
    "https://34.64.8.101/oauth/token",
})
_MCP_TLS_CA_PEM_SHA256 = "1f6ece41434a006f9cb29043a0c10c314c9a053d2bc16dbeec17f6d49118c76a"


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
class BrowserSettings:
    """Isolated opt-in configuration; disabled mode reads only its switch.

    No credentials or provider instances are loaded here. Composition remains
    disconnected until the browser execution and source manifest gates are ready.
    """

    enabled: bool = False
    transport: Literal["playwright", "cdp"] | None = None
    decision_origin: str | None = None
    request_model: str | None = None
    deployment_model: str | None = None

    @classmethod
    def from_env(cls, source: Mapping[str, str]) -> BrowserSettings:
        if not _boolean(source, "BROWSER_ENABLED", default=False):
            return cls()
        transport = _required(source, "BROWSER_TRANSPORT")
        if transport not in {"playwright", "cdp"}:
            raise RuntimeError("BROWSER_TRANSPORT must be playwright or cdp")
        decision_keys = (
            "BROWSER_DECISION_ORIGIN", "BROWSER_DECISION_REQUEST_MODEL",
            "BROWSER_DECISION_DEPLOYMENT_MODEL",
        )
        configured = tuple(name in source for name in decision_keys)
        if not any(configured):
            # Absence is only configuration data. The signed Site budget and
            # installation authority decide whether a model-free path is legal.
            return cls(
                enabled=True, transport=cast(Literal["playwright", "cdp"], transport),
            )
        if not all(configured):
            raise RuntimeError("BROWSER_DECISION configuration must be complete")
        deployment = _required(source, "BROWSER_DECISION_DEPLOYMENT_MODEL")
        if "latest" in deployment.casefold():
            raise RuntimeError("BROWSER_DECISION_DEPLOYMENT_MODEL must be pinned")
        origin = _http_base_url(source, "BROWSER_DECISION_ORIGIN")
        _canonical_http_origin(origin, "BROWSER_DECISION_ORIGIN")
        return cls(
            enabled=True, transport=cast(Literal["playwright", "cdp"], transport),
            decision_origin=origin,
            request_model=_required(source, "BROWSER_DECISION_REQUEST_MODEL"),
            deployment_model=deployment,
        )


@dataclass(frozen=True, slots=True)
class McpTlsCaConfig:
    """One approved public certificate and one exact existing service version."""

    ca_file: Path = field(repr=False)
    service_config_id: str
    service_config_version: int
    endpoint: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ca_file, Path) or not self.ca_file.is_absolute()
            or type(self.service_config_id) is not str
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.service_config_id) is None
            or type(self.service_config_version) is not int or self.service_config_version < 1
            or self.endpoint != MCP_TLS_CA_ENDPOINT
        ):
            raise ValueError("mcp_tls_ca_configuration_invalid")

    @property
    def service_key(self) -> tuple[str, int, str]:
        return self.service_config_id, self.service_config_version, self.endpoint


def load_mcp_tls_context(ca_file: Path) -> ssl.SSLContext:
    """Load only the approved public PEM; no settings, environment or client IO."""
    try:
        if not ca_file.is_absolute() or ca_file.is_symlink():
            raise ValueError
        with ca_file.open("rb") as certificate:
            pem = certificate.read(65537)
        if len(pem) > 65536 or hashlib.sha256(pem).hexdigest() != _MCP_TLS_CA_PEM_SHA256:
            raise ValueError
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cadata=pem.decode("ascii"))
        return context
    except (OSError, ValueError):
        raise RuntimeError("mcp_tls_ca_certificate_invalid") from None


@dataclass(frozen=True, slots=True)
class FixedDocumentSettings:
    """Optional single-document settings; never open the file at application startup."""

    path: str = field(repr=False)
    tenant_id: str = field(repr=False)
    ai_user_id: str = field(repr=False)
    topic: str = field(default="指定文档", repr=False)
    max_bytes: int = 32768

    @property
    def valid(self) -> bool:
        return bool(self.tenant_id and self.ai_user_id and self.topic.strip()) and (
            type(self.max_bytes) is int and 1 <= self.max_bytes <= 131072
            and len(self.topic) <= 256
        )

    @classmethod
    def from_env(cls, source: Mapping[str, str]) -> FixedDocumentSettings | None:
        path = source.get("FIXED_DOCUMENT_PATH", "").strip()
        if not path:
            return None
        try:
            max_bytes = int(source.get("FIXED_DOCUMENT_MAX_BYTES", "32768"))
        except ValueError:
            max_bytes = 0  # A document-local error must not prevent OA/D/MCP startup.
        return cls(
            path=path,
            tenant_id=source.get("FIXED_DOCUMENT_TENANT_ID", "").strip(),
            ai_user_id=source.get("FIXED_DOCUMENT_AI_USER_ID", "").strip(),
            topic=source.get("FIXED_DOCUMENT_TOPIC", "指定文档").strip(),
            max_bytes=max_bytes,
        )


class _DeliveryRecipientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_user_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class IMDeliverySettings:
    """Fixed trial instance and Bot; credentials stay inside its adapter."""

    origin: str
    host_origin: str
    im_instance_id: str
    realm_id: str
    sender_bot_id: str
    bot_email: str = field(repr=False)
    bot_api_key: SecretStr = field(repr=False)
    recipients: tuple[tuple[str, str], ...]
    tls_ca_file: str | None = None

    @classmethod
    def from_env(cls, source: Mapping[str, str]) -> IMDeliverySettings | None:
        if not source.get("IM_DELIVERY_BOT_ID", "").strip():
            return None
        if _required(source, "IM_PROVIDER") != "zulip":
            raise RuntimeError("im_provider_unsupported")
        origin = _canonical_http_origin(_required(source, "IM_ZULIP_ORIGIN"), "IM_ZULIP_ORIGIN")
        host = _canonical_http_origin(_required(source, "IM_HOST_ORIGIN"), "IM_HOST_ORIGIN")
        if not origin.startswith("https://") or not host.startswith("https://"):
            raise RuntimeError("im_delivery_https_required")
        try:
            recipients = TypeAdapter(tuple[_DeliveryRecipientConfig, ...]).validate_json(
                _required(source, "IM_DELIVERY_RECIPIENTS_JSON")
            )
        except ValidationError:
            raise RuntimeError("im_delivery_recipients_invalid") from None
        return cls(
            origin=origin, host_origin=host,
            im_instance_id=_required(source, "IM_INSTANCE_ID"),
            realm_id=_required(source, "IM_ZULIP_REALM_ID"),
            sender_bot_id=_required(source, "IM_DELIVERY_BOT_ID"),
            bot_email=_required(source, "IM_DELIVERY_BOT_EMAIL"),
            bot_api_key=SecretStr(_required(source, "IM_DELIVERY_BOT_API_KEY")),
            recipients=tuple((person.ai_user_id, person.display_name) for person in recipients),
            tls_ca_file=source.get("IM_ZULIP_TLS_CA_FILE") or None,
        )


@dataclass(frozen=True, slots=True)
class ZulipSettings:
    origin: str
    host_origin: str
    im_instance_id: str
    realm_id: str
    helper_url: str
    jwt_secret: bytes = field(repr=False)
    tls_ca_file: str | None = None

    def __post_init__(self) -> None:
        for origin in (self.origin, self.host_origin):
            parsed = urlsplit(origin)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path or parsed.query or parsed.fragment
                    or origin != f"https://{parsed.netloc}"):
                raise ValueError("im_origin_invalid")
        helper = urlsplit(self.helper_url)
        if (self.origin == self.host_origin or self.helper_url != self.origin + helper.path
                or not helper.path.startswith("/") or helper.path == "/"
                or not self.im_instance_id or not self.realm_id or len(self.jwt_secret) < 32):
            raise ValueError("im_configuration_invalid")


def _zulip_settings(source: Mapping[str, str]) -> ZulipSettings | None:
    provider = source.get("IM_PROVIDER", "").strip()
    if not provider:
        return None
    if provider != "zulip":
        raise RuntimeError("im_provider_unsupported")
    return ZulipSettings(
        origin=_required(source, "IM_ZULIP_ORIGIN"),
        host_origin=_required(source, "IM_HOST_ORIGIN"),
        im_instance_id=_required(source, "IM_INSTANCE_ID"),
        realm_id=_required(source, "IM_ZULIP_REALM_ID"),
        helper_url=_required(source, "IM_SESSION_HELPER_URL"),
        jwt_secret=_base64_key(source, "IM_ZULIP_JWT_SECRET_B64", minimum_bytes=32),
        tls_ca_file=source.get("IM_ZULIP_TLS_CA_FILE") or None,
    )


class _PersonalAgentBotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str = Field(min_length=1)
    bot_email_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    bot_api_key_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    webhook_token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


def personal_agent_environment_keys(source: Mapping[str, str]) -> tuple[str, ...]:
    """Only explicit Bot credential references may enter the worker environment."""

    raw = source.get("IM_PERSONAL_AGENTS_JSON", "[]")
    try:
        bots = TypeAdapter(tuple[_PersonalAgentBotConfig, ...]).validate_json(raw)
    except ValidationError:
        raise RuntimeError("personal_agent_configuration_invalid") from None
    if not bots:
        return ()
    credential_keys = tuple(dict.fromkeys(
        key for bot in bots
        for key in (bot.bot_email_env, bot.bot_api_key_env, bot.webhook_token_env)
    ))
    return (*credential_keys, "IM_SESSION_HELPER_URL", "IM_ZULIP_JWT_SECRET_B64",
            "RUNTIME_ENGINE", "LLM_BASE_URL", "LLM_MODEL")


@dataclass(frozen=True, slots=True)
class PersonalAgentBotSettings:
    bot_id: str
    bot_email: str = field(repr=False)
    bot_api_key: SecretStr = field(repr=False)
    webhook_token: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True)
class PersonalAgentSettings:
    zulip: ZulipSettings = field(repr=False)
    bots: tuple[PersonalAgentBotSettings, ...] = field(repr=False)
    llm_base_url: str
    llm_model: str

    @classmethod
    def from_env(cls, source: Mapping[str, str]) -> PersonalAgentSettings | None:
        try:
            bots = TypeAdapter(tuple[_PersonalAgentBotConfig, ...]).validate_json(
                source.get("IM_PERSONAL_AGENTS_JSON", "[]")
            )
        except ValidationError:
            raise RuntimeError("personal_agent_configuration_invalid") from None
        if not bots:
            return None
        if _runtime_engine(source) != "agentscope":
            raise RuntimeError("personal_agent_runtime_unsupported")
        if not source.get("IM_DELIVERY_BOT_ID", "").strip():
            raise RuntimeError("personal_agent_delivery_required")
        zulip = _zulip_settings(source)
        if zulip is None or len({bot.bot_id for bot in bots}) != len(bots):
            raise RuntimeError("personal_agent_configuration_invalid")
        return cls(
            zulip=zulip,
            bots=tuple(PersonalAgentBotSettings(
                bot_id=bot.bot_id,
                bot_email=_required(source, bot.bot_email_env),
                bot_api_key=SecretStr(_required(source, bot.bot_api_key_env)),
                webhook_token=SecretStr(_required(source, bot.webhook_token_env)),
            ) for bot in bots),
            llm_base_url=_http_base_url(
                source, "LLM_BASE_URL", default=_DEFAULT_LLM_BASE_URL,
            ),
            llm_model=_required(source, "LLM_MODEL", default=_DEFAULT_LLM_MODEL),
        )


@dataclass(frozen=True, slots=True)
class ProductionSettings:
    """Validated values needed to construct the production application."""

    source_profile_id: str
    source_tenant_id: str
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
    # SESSION_COOKIE_TTL_S is the idle window; the absolute cap is off when None.
    session_absolute_ttl_seconds: int | None = None
    session_renew_min_interval_seconds: int = _DEFAULT_SESSION_RENEW_MIN_INTERVAL_SECONDS
    # Runtime behind /api/v1/runtime; AgentScope is the default, legacy stays selectable.
    runtime_engine: RuntimeEngine = "agentscope"
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
    # Both identity settings carry defaults on purpose: create_production_app
    # is fail-fast, and an existing deployment must keep starting without
    # having to learn two new environment variables first.
    oa_user_profile_path: str = _DEFAULT_OA_USER_PROFILE_PATH
    oa_profile_timeout_seconds: float = _DEFAULT_OA_PROFILE_TIMEOUT_SECONDS
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
    oa_heartbeat_path: str = _DEFAULT_OA_HEARTBEAT_PATH
    oa_heartbeat_interval_seconds: int = _DEFAULT_OA_HEARTBEAT_INTERVAL_SECONDS
    oa_recovery_cooldown_seconds: int = _DEFAULT_OA_RECOVERY_COOLDOWN_SECONDS
    oa_recovery_wait_seconds: int = _DEFAULT_OA_RECOVERY_WAIT_SECONDS
    oa_recovery_kick_window_seconds: int = _DEFAULT_OA_RECOVERY_KICK_WINDOW_SECONDS
    organization_directory_max_age_s: int = 172800
    organization_directory_sync_source_ai_user_id: str | None = field(default=None, repr=False)
    # Upper bound for the self-check ``hold_seconds`` parameter; 0 forbids holding.
    background_self_check_max_hold_seconds: int = 0
    im_delivery: IMDeliverySettings | None = field(default=None, repr=False)
    personal_agents: PersonalAgentSettings | None = field(default=None, repr=False)
    phase0_mock_mode: bool = False
    mcp_services: tuple[ServiceConfig, ...] = ()
    mcp_tls_ca: McpTlsCaConfig | None = field(default=None, repr=False)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    fixed_document: FixedDocumentSettings | None = field(default=None, repr=False)
    file_artifacts: FileArtifactSettings | None = field(default=None, repr=False)
    zulip: ZulipSettings | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.zulip is not None and (
            self.source_tenant_id != "default" or not self.session_cookie_secure
            or self.zulip.host_origin not in self.csrf_allowed_origins
        ):
            raise ValueError("im_host_configuration_invalid")
        ids = [profile.service_config_id for profile in self.mcp_services]
        if len(ids) != len(set(ids)) or any(
            p.tenant_id != self.source_tenant_id for p in self.mcp_services
        ):
            raise ValueError("mcp_configuration_invalid")
        if self.mcp_tls_ca is not None and not any(
            (profile.service_config_id, profile.service_config_version, profile.endpoint)
            == self.mcp_tls_ca.service_key
            for profile in self.mcp_services
        ):
            raise ValueError("mcp_tls_ca_service_mismatch")
        for value in (self.source_profile_id, self.source_tenant_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("source_profile_configuration_invalid")

    def oa_read_settings(self) -> OAReadSettings:
        """The narrow OA read values (same names, same values) for the shared adapter builder."""

        return OAReadSettings(
            **{item.name: getattr(self, item.name) for item in fields(OAReadSettings)}
        )

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
        session_cookie_ttl_seconds = _required_positive_int(source, "SESSION_COOKIE_TTL_S")
        # Unset or blank (compose passthrough) keeps the absolute cap off.
        absolute_ttl_raw = source.get("SESSION_ABSOLUTE_TTL_S", "").strip()
        session_absolute_ttl_seconds = (
            _parse_positive_int(absolute_ttl_raw, "SESSION_ABSOLUTE_TTL_S")
            if absolute_ttl_raw
            else None
        )
        if (
            session_absolute_ttl_seconds is not None
            and session_absolute_ttl_seconds <= session_cookie_ttl_seconds
        ):
            raise RuntimeError("SESSION_ABSOLUTE_TTL_S must exceed SESSION_COOKIE_TTL_S")
        session_renew_min_interval_seconds = _positive_int(
            source,
            "SESSION_RENEW_MIN_INTERVAL_S",
            _DEFAULT_SESSION_RENEW_MIN_INTERVAL_SECONDS,
        )
        if session_renew_min_interval_seconds >= session_cookie_ttl_seconds:
            raise RuntimeError(
                "SESSION_RENEW_MIN_INTERVAL_S must be less than SESSION_COOKIE_TTL_S"
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
            source_profile_id=source.get("OA_SOURCE_PROFILE_ID", ""),
            source_tenant_id=source.get("OA_TENANT_ID", ""),
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
            session_cookie_ttl_seconds=session_cookie_ttl_seconds,
            session_absolute_ttl_seconds=session_absolute_ttl_seconds,
            session_renew_min_interval_seconds=session_renew_min_interval_seconds,
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
            organization_directory_max_age_s=_directory_max_age(source),
            organization_directory_sync_source_ai_user_id=_directory_source_user(source),
            background_self_check_max_hold_seconds=_self_check_max_hold(source),
            file_artifacts=_file_artifact_settings(source),
            im_delivery=IMDeliverySettings.from_env(source),
            personal_agents=PersonalAgentSettings.from_env(source),
            csrf_allowed_origins=csrf_allowed_origins,
            runtime_engine=_runtime_engine(source),
            zulip=_zulip_settings(source),
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
            oa_user_profile_path=_oa_path_with_default(
                source,
                "OA_USER_PROFILE_PATH",
                _DEFAULT_OA_USER_PROFILE_PATH,
            ),
            oa_profile_timeout_seconds=_positive_float(
                source,
                "OA_PROFILE_TIMEOUT_S",
                min(
                    _positive_float(
                        source,
                        "OA_TIMEOUT_S",
                        _DEFAULT_OA_TIMEOUT_SECONDS,
                    ),
                    _DEFAULT_OA_PROFILE_TIMEOUT_SECONDS,
                ),
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
            oa_heartbeat_path=_oa_path_with_default(
                source,
                "OA_HEARTBEAT_PATH",
                _DEFAULT_OA_HEARTBEAT_PATH,
            ),
            oa_heartbeat_interval_seconds=_bounded_positive_int(
                source,
                "OA_HEARTBEAT_INTERVAL_S",
                _DEFAULT_OA_HEARTBEAT_INTERVAL_SECONDS,
                minimum=30,
                maximum=600,
            ),
            oa_recovery_cooldown_seconds=_bounded_positive_int(
                source,
                "OA_RECOVERY_COOLDOWN_S",
                _DEFAULT_OA_RECOVERY_COOLDOWN_SECONDS,
                minimum=30,
                maximum=3_600,
            ),
            oa_recovery_wait_seconds=_bounded_positive_int(
                source,
                "OA_RECOVERY_WAIT_S",
                _DEFAULT_OA_RECOVERY_WAIT_SECONDS,
                maximum=60,
            ),
            oa_recovery_kick_window_seconds=_bounded_positive_int(
                source,
                "OA_RECOVERY_KICK_WINDOW_S",
                _DEFAULT_OA_RECOVERY_KICK_WINDOW_SECONDS,
                minimum=60,
                maximum=3_600,
            ),
            phase0_mock_mode=phase0_mock_mode,
            mcp_services=_mcp_services(source),
            mcp_tls_ca=_mcp_tls_ca(source),
            browser=BrowserSettings.from_env(source),
            fixed_document=FixedDocumentSettings.from_env(source),
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
        except (ValueError, ZoneInfoNotFoundError):
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


def _runtime_engine(source: Mapping[str, str]) -> RuntimeEngine:
    value = source.get("RUNTIME_ENGINE", "agentscope")
    if value not in {"legacy", "agentscope"}:
        raise RuntimeError("RUNTIME_ENGINE must be legacy or agentscope")
    return cast(RuntimeEngine, value)


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


def _oa_path_with_default(
    source: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    """Validate an override the same way as a live path, but never require it."""

    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
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


__all__ = (
    "FileArtifactSettings",
    "OAReadAdapterMode",
    "OAReadSettings",
    "OAReportWorkerSettings",
    "ProductionSettings",
    "PersonalAgentSettings",
    "RedisConnectionURL",
    "RuntimeEngine",
    "WORKER_ENVIRONMENT_KEYS",
    "WorkerSettings",
)


def _directory_max_age(source: Mapping[str, str]) -> int:
    import re

    raw = source.get("ORGANIZATION_DIRECTORY_MAX_AGE_S", "172800")
    if re.fullmatch(r"[1-9][0-9]{0,5}", raw) is None or not 86400 <= int(raw) <= 604800:
        raise ValueError("ORGANIZATION_DIRECTORY_MAX_AGE_S must be an integer in 86400..604800")
    return int(raw)


def _self_check_max_hold(source: Mapping[str, str]) -> int:
    return _integer_in_range(
        source,
        "BACKGROUND_SELF_CHECK_MAX_HOLD_SECONDS",
        0,
        minimum=0,
        maximum=_MAX_SELF_CHECK_HOLD_SECONDS,
    )


@dataclass(frozen=True)
class FileArtifactSettings:
    root: Path
    mac_key: bytes = field(repr=False)
    # Only a root marked ``acceptance_sample`` accepts a value other than 14 days.
    retention_override_seconds: int | None = None


def _file_artifact_settings(source: Mapping[str, str]) -> FileArtifactSettings | None:
    """All three keys unset disables the feature; a partial configuration fails startup."""

    root = source.get(_FILE_ARTIFACT_ROOT_KEY, "").strip()
    key = source.get(_FILE_ARTIFACT_MAC_KEY, "").strip()
    retention = source.get(_FILE_ARTIFACT_RETENTION_KEY, "").strip()
    if not root and not key and not retention:
        return None
    if not root or not key:
        raise RuntimeError("file_artifact_configuration_incomplete")
    location = Path(root)
    if not location.is_absolute():
        raise RuntimeError(f"{_FILE_ARTIFACT_ROOT_KEY} must be an absolute path")
    return FileArtifactSettings(
        root=location,
        mac_key=_base64_key(source, _FILE_ARTIFACT_MAC_KEY, exact_bytes=32),
        retention_override_seconds=(
            _integer_in_range(
                source,
                _FILE_ARTIFACT_RETENTION_KEY,
                _MAX_FILE_ARTIFACT_RETENTION_SECONDS,
                minimum=_MIN_FILE_ARTIFACT_RETENTION_SECONDS,
                maximum=_MAX_FILE_ARTIFACT_RETENTION_SECONDS,
            )
            if retention
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class OAReadSettings:
    """OA read-adapter values shared by the API and the worker.

    Field names equal the ``ProductionSettings`` fields they come from; both parse them
    with the same helpers, so the API path keeps its parameters and errors.
    """

    source_tenant_id: str
    oa_read_adapter_mode: OAReadAdapterMode
    oa_base_url: str
    oa_timeout_seconds: float
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

    def __post_init__(self) -> None:
        if not isinstance(self.source_tenant_id, str) or not self.source_tenant_id.strip():
            raise ValueError("source_profile_configuration_invalid")


@dataclass(frozen=True)
class OAReportWorkerSettings:
    """What the worker needs to read the requester's own OA to-dos: no login or renewal."""

    oa_read: OAReadSettings
    credential_encryption_key: bytes = field(repr=False)


_OA_PENDING_PATH_KEYS: tuple[tuple[str, str], ...] = (
    ("oa_pending_workflows_split_page_key_path", "OA_PENDING_WORKFLOWS_SPLIT_PAGE_KEY_PATH"),
    ("oa_pending_workflows_counts_path", "OA_PENDING_WORKFLOWS_COUNTS_PATH"),
    ("oa_pending_workflows_datas_path", "OA_PENDING_WORKFLOWS_DATAS_PATH"),
)
# (field, key, allow_empty) exactly as ProductionSettings.from_environment parses them.
_OA_FORM_PARAMETER_KEYS: tuple[tuple[str, str, bool], ...] = (
    ("oa_pending_workflows_actiontype", "OA_PENDING_WORKFLOWS_ACTIONTYPE", False),
    ("oa_pending_workflows_hide_no_data_tab", "OA_PENDING_WORKFLOWS_HIDE_NO_DATA_TAB", False),
    ("oa_pending_workflows_method", "OA_PENDING_WORKFLOWS_METHOD", False),
    ("oa_pending_workflows_offical_type", "OA_PENDING_WORKFLOWS_OFFICAL_TYPE", False),
    ("oa_pending_workflows_view_scope", "OA_PENDING_WORKFLOWS_VIEW_SCOPE", False),
    ("oa_pending_workflows_sort_params", "OA_PENDING_WORKFLOWS_SORT_PARAMS", False),
    ("oa_system_messages_category_id", "OA_SYSTEM_MESSAGES_CATEGORY_ID", True),
    ("oa_system_messages_bizstate", "OA_SYSTEM_MESSAGES_BIZSTATE", True),
    ("oa_system_messages_select_state", "OA_SYSTEM_MESSAGES_SELECT_STATE", True),
)


def _oa_report_worker_settings(source: Mapping[str, str]) -> OAReportWorkerSettings | None:
    """Unset or ``mock`` OA mode leaves the report kind out of the worker."""

    mode = _oa_read_adapter_mode(source)
    if mode == "mock":
        return None
    paths = {name: _oa_capability_path(source, mode, key) for name, key in _OA_PENDING_PATH_KEYS}
    forms = {
        name: _oa_live_form_parameter(source, mode, key, allow_empty=allow_empty)
        for name, key, allow_empty in _OA_FORM_PARAMETER_KEYS
    }
    return OAReportWorkerSettings(
        oa_read=OAReadSettings(
            source_tenant_id=source.get("OA_TENANT_ID", ""),
            oa_read_adapter_mode=mode,
            oa_base_url=_http_base_url(source, "OA_BASE_URL"),
            oa_timeout_seconds=_positive_float(
                source, "OA_TIMEOUT_S", _DEFAULT_OA_TIMEOUT_SECONDS
            ),
            oa_read_contract_pack_dir=_oa_read_contract_pack_dir(source, mode),
            oa_pending_workflows_contract_pack_dir=_oa_live_contract_pack_dir(
                source, mode, "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR"
            ),
            oa_system_messages_contract_pack_dir=_oa_live_contract_pack_dir(
                source, mode, "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR"
            ),
            oa_message_center_path=_oa_capability_path(source, mode, "OA_MESSAGE_CENTER_PATH"),
            oa_message_center_page_size=_bounded_positive_int(
                source, "OA_MESSAGE_CENTER_PAGE_SIZE", 20, maximum=1_000
            ),
            **paths,
            **forms,
        ),
        credential_encryption_key=_base64_key(
            source, "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64", exact_bytes=32
        ),
    )


@dataclass(frozen=True)
class WorkerSettings:
    """Queue values plus optional IM Bot, reader and tool-free model settings."""

    database_url: str = field(repr=False)
    redis_url: RedisConnectionURL
    health_timeout_seconds: float
    organization_directory_max_age_s: int
    concurrency: int
    self_check_max_hold_seconds: int
    file_artifacts: FileArtifactSettings | None = field(default=None, repr=False)
    im_delivery: IMDeliverySettings | None = field(default=None, repr=False)
    personal_agents: PersonalAgentSettings | None = field(default=None, repr=False)
    # OA pending-workflow report kind; None leaves it unregistered in this worker.
    oa_report: OAReportWorkerSettings | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> WorkerSettings:
        source = os.environ if environment is None else environment
        return cls(
            database_url=get_database_url(source),
            redis_url=_redis_url(source),
            health_timeout_seconds=_bounded_float(
                source,
                "HEALTH_TIMEOUT_S",
                _DEFAULT_HEALTH_TIMEOUT_SECONDS,
                minimum=0.0,
                maximum=_MAX_HEALTH_TIMEOUT_SECONDS,
                minimum_inclusive=False,
            ),
            organization_directory_max_age_s=_directory_max_age(source),
            concurrency=_integer_in_range(
                source, "BACKGROUND_WORKER_CONCURRENCY", 2, minimum=1, maximum=8
            ),
            self_check_max_hold_seconds=_self_check_max_hold(source),
            file_artifacts=_file_artifact_settings(source),
            im_delivery=IMDeliverySettings.from_env(source),
            personal_agents=PersonalAgentSettings.from_env(source),
            oa_report=_oa_report_worker_settings(source),
        )


WORKER_ENVIRONMENT_KEYS: tuple[str, ...] = (
    "DATABASE_URL",
    "REDIS_URL",
    "HEALTH_TIMEOUT_S",
    "ORGANIZATION_DIRECTORY_MAX_AGE_S",
    "BACKGROUND_WORKER_CONCURRENCY",
    "BACKGROUND_SELF_CHECK_MAX_HOLD_SECONDS",
    _FILE_ARTIFACT_ROOT_KEY,
    _FILE_ARTIFACT_MAC_KEY,
    _FILE_ARTIFACT_RETENTION_KEY,
    "IM_PROVIDER",
    "IM_ZULIP_ORIGIN",
    "IM_HOST_ORIGIN",
    "IM_INSTANCE_ID",
    "IM_ZULIP_REALM_ID",
    "IM_ZULIP_TLS_CA_FILE",
    "IM_DELIVERY_BOT_ID",
    "IM_DELIVERY_BOT_EMAIL",
    "IM_DELIVERY_BOT_API_KEY",
    "IM_DELIVERY_RECIPIENTS_JSON",
    "IM_PERSONAL_AGENTS_JSON",
    # OA pending-workflow report: read-only OA values plus the credential decryption key.
    # No identity HMAC, session, password-login, renewal or heartbeat settings.
    "OA_READ_ADAPTER_MODE",
    "OA_TENANT_ID",
    "OA_BASE_URL",
    "OA_TIMEOUT_S",
    "OA_READ_CONTRACT_PACK_DIR",
    "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
    "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
    "OA_MESSAGE_CENTER_PATH",
    "OA_MESSAGE_CENTER_PAGE_SIZE",
    *(key for _, key in _OA_PENDING_PATH_KEYS),
    *(key for _, key, _ in _OA_FORM_PARAMETER_KEYS),
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
)


def _directory_source_user(source: Mapping[str, str]) -> str | None:
    raw = source.get("ORGANIZATION_DIRECTORY_SYNC_SOURCE_AI_USER_ID")
    if raw is not None and (not raw.strip() or raw != raw.strip()):
        raise ValueError(
            "ORGANIZATION_DIRECTORY_SYNC_SOURCE_AI_USER_ID must be a nonblank identifier"
        )
    return raw


def _mcp_services(source: Mapping[str, str]) -> tuple[ServiceConfig, ...]:
    from app.mcp.models import ServiceConfig

    raw = source.get("MCP_SERVICE_CONFIGS_JSON", "[]")
    try:
        if len(raw.encode()) > 65536:
            raise ValueError
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) > 16:
            raise ValueError
        return tuple(ServiceConfig.model_validate(value) for value in values)
    except (ValueError, TypeError):
        raise RuntimeError("mcp_configuration_invalid") from None


def _mcp_tls_ca(source: Mapping[str, str]) -> McpTlsCaConfig | None:
    raw = source.get("MCP_TLS_CA_CONFIG_JSON")
    if raw is None or not raw.strip():
        return None

    def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        fields: dict[str, object] = {}
        for name, value in pairs:
            if name in fields:
                raise ValueError
            fields[name] = value
        return fields

    try:
        if len(raw.encode()) > 4096:
            raise ValueError
        values = json.loads(raw, object_pairs_hook=unique_fields)
        if type(values) is not dict or set(values) != {
            "ca_file", "service_config_id", "service_config_version", "endpoint",
        } or type(values["ca_file"]) is not str:
            raise ValueError
        return McpTlsCaConfig(
            ca_file=Path(values["ca_file"]), service_config_id=values["service_config_id"],
            service_config_version=values["service_config_version"], endpoint=values["endpoint"],
        )
    except (ValueError, TypeError):
        raise RuntimeError("mcp_tls_ca_configuration_invalid") from None
