"""Strict, local D installation references; no discovery or generated credentials."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.engine import make_url

from app.config import ProductionSettings
from app.db.config import get_database_url
from scripts.smoke.environment import parse_env_file
from scripts.smoke.errors import ENV_PARSE_REASONS, EnvParseReason, SmokeError

Reference = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]
Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# One real OA repair category, as the form shows it (single non-blank line, see below).
CategoryOption = Annotated[str, Field(min_length=1, max_length=100)]
ConfigurationCode = Literal[
    "d_form_config_missing",
    "d_form_config_invalid",
    "d_form_environment_invalid",
    "d_form_identity_missing",
    "d_form_identity_invalid",
    "d_form_key_reference_missing",
    "d_form_key_reference_invalid",
    "d_form_pool_missing",
    "d_form_pool_mismatch",
    "d_form_authorization_missing",
    "d_form_reference_missing",
    "d_form_owned_deployment_missing",
    "d_form_login_contract_missing",
    "d_form_resource_scope_denied",
]

DiagnosticStage = Literal[
    "environment_file", "environment_parse", "production_settings", "browser_enabled",
]
DiagnosticCategory = Literal[
    "missing", "unreadable", "invalid_format", "invalid_encoding",
    "invalid_value", "invalid_settings", "disabled", "unknown",
]
_DIAGNOSTIC_STAGES = frozenset({
    "environment_file", "environment_parse", "production_settings", "browser_enabled",
})
_DIAGNOSTIC_CATEGORIES = frozenset({
    "missing", "unreadable", "invalid_format", "invalid_encoding",
    "invalid_value", "invalid_settings", "disabled", "unknown",
})
# Source-defined field names only. No environment keys or ValidationError loc
# are copied into a diagnostic, including dynamic service IDs or key references.
_DIAGNOSTIC_FIELDS = frozenset({
    "DATABASE_URL", "REDIS_URL", "ENV", "CSRF_ALLOWED_ORIGINS", "PHASE0_MOCK_MODE",
    "SESSION_COOKIE_SECURE", "SESSION_COOKIE_TTL_S", "SESSION_ABSOLUTE_TTL_S",
    "SESSION_RENEW_MIN_INTERVAL_S", "OA_BASE_URL", "OA_TIMEOUT_S",
    "OA_SOURCE_PROFILE_ID", "OA_TENANT_ID", "OA_CREDENTIAL_TTL_S",
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64", "ETERNALAI_IDENTITY_HMAC_KEY_B64",
    "ETERNALAI_SESSION_BINDING_KEY_B64", "ETERNALAI_SESSION_SIGNING_KEY_B64",
    "LLM_BASE_URL", "LLM_ENABLE_THINKING", "LLM_MAX_TOKENS", "LLM_MODEL",
    "LLM_TEMPERATURE", "LLM_TIMEOUT_S", "LLM_TOP_K", "LLM_TOP_P", "HEALTH_TIMEOUT_S",
    "MCP_SERVICE_CONFIGS_JSON", "MCP_TLS_CA_CONFIG_JSON",
    "OA_MESSAGE_CENTER_PAGE_SIZE", "OA_MESSAGE_CENTER_PATH", "OA_PROFILE_TIMEOUT_S",
    "OA_READ_ADAPTER_MODE", "OA_READ_CONTRACT_PACK_DIR", "OA_USER_PROFILE_PATH",
    "OA_PENDING_WORKFLOWS_ACTIONTYPE", "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
    "OA_PENDING_WORKFLOWS_COUNTS_PATH", "OA_PENDING_WORKFLOWS_DATAS_PATH",
    "OA_PENDING_WORKFLOWS_HIDE_NO_DATA_TAB", "OA_PENDING_WORKFLOWS_METHOD",
    "OA_PENDING_WORKFLOWS_OFFICAL_TYPE", "OA_PENDING_WORKFLOWS_SORT_PARAMS",
    "OA_PENDING_WORKFLOWS_SPLIT_PAGE_KEY_PATH", "OA_PENDING_WORKFLOWS_VIEW_SCOPE",
    "OA_SYSTEM_MESSAGES_BIZSTATE", "OA_SYSTEM_MESSAGES_CATEGORY_ID",
    "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR", "OA_SYSTEM_MESSAGES_SELECT_STATE",
    "ORGANIZATION_DIRECTORY_MAX_AGE_S", "ORGANIZATION_DIRECTORY_SYNC_SOURCE_AI_USER_ID",
    "CREDENTIAL_POLL_GLOBAL_CONCURRENCY", "CREDENTIAL_POLL_INTERVAL_S",
    "CREDENTIAL_POLL_MAXIMUM_BACKOFF_S", "CREDENTIAL_POLL_SCHEDULER_TICK_S",
    "CREDENTIAL_POLL_TIMEZONE", "CREDENTIAL_POLL_WORK_END_HOUR",
    "CREDENTIAL_POLL_WORK_START_HOUR", "BROWSER_ENABLED", "BROWSER_TRANSPORT",
    "BROWSER_DECISION_DEPLOYMENT_MODEL", "BROWSER_DECISION_ORIGIN",
    "BROWSER_DECISION_REQUEST_MODEL",
})


class DFormConfigurationError(ValueError):
    def __init__(
        self, code: ConfigurationCode, *, stage: DiagnosticStage | None = None,
        field_name: str | None = None, category: DiagnosticCategory = "unknown",
        parse_reason: EnvParseReason | None = None, line_number: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self._diagnostic = (stage, field_name, category)
        self._env_parse_diagnostic = (parse_reason, line_number)

    def safe_diagnostic(self) -> dict[str, str | int | None]:
        stage, name, category = self._diagnostic
        reason, line = self._env_parse_diagnostic
        return {
            "stage": stage if type(stage) is str and stage in _DIAGNOSTIC_STAGES else None,
            "field": name if type(name) is str and name in _DIAGNOSTIC_FIELDS else None,
            "category": category if type(category) is str
            and category in _DIAGNOSTIC_CATEGORIES else "unknown",
            "parse_reason": reason if type(reason) is str and reason in ENV_PARSE_REASONS else None,
            "line_number": line if type(line) is int and line > 0 else None,
        }


def _settings_diagnostic(error: Exception) -> tuple[str | None, DiagnosticCategory]:
    """Recognize source-defined error contracts; never return their message or loc."""
    if (type(error) not in {RuntimeError, ValueError} or len(error.args) != 1
            or type(error.args[0]) is not str):
        return None, "invalid_settings"
    message = error.args[0]
    fixed_fields = {
        "mcp_configuration_invalid": "MCP_SERVICE_CONFIGS_JSON",
        "mcp_tls_ca_configuration_invalid": "MCP_TLS_CA_CONFIG_JSON",
        "mcp_tls_ca_certificate_invalid": "MCP_TLS_CA_CONFIG_JSON",
        "mcp_tls_ca_service_mismatch": "MCP_TLS_CA_CONFIG_JSON",
    }
    if message in fixed_fields:
        return fixed_fields[message], "invalid_settings"
    for name in _DIAGNOSTIC_FIELDS:
        if message == f"{name} is required" or message == f"{name} must not be empty":
            return name, "missing"
        if message == f"{name} must contain valid base64":
            return name, "invalid_encoding"
        if message.startswith(name + " "):
            return name, "invalid_value"
    return None, "invalid_settings"


class LocalConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return type(self).__name__ + "()"


class ActorReference(LocalConfiguration):
    token_ref: Reference = Field(repr=False)
    bound_session: str = Field(min_length=1, max_length=512, repr=False)
    required_role: Identity


class PublicationActorReference(ActorReference):
    operations: tuple[
        Literal["prepare", "activate", "deactivate", "read", "execute", "teach"], ...
    ] = Field(min_length=1)


class CleanupActorReference(ActorReference):
    operations: tuple[Literal["recover", "cleanup"], ...] = Field(min_length=1)


class BindingSelector(LocalConfiguration):
    user_id: Identity = Field(repr=False)
    binding_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")] = Field(repr=False)
    revision: int = Field(ge=1, le=9007199254740991, strict=True)


class KeyRingReference(LocalConfiguration):
    references: dict[Identity, Reference] = Field(min_length=1, repr=False)
    active_id: Identity


class KeyReferences(LocalConfiguration):
    payload: KeyRingReference
    resource: KeyRingReference
    request_digest: KeyRingReference
    input_digest: Reference = Field(repr=False)
    result_digest: Reference = Field(repr=False)
    proof_context: Reference = Field(repr=False)


class PoolReference(LocalConfiguration):
    provider_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")] = "d_form_fill_stop"
    aliases: tuple[Identity, ...] = ()
    manifest_digest: Digest


class DFormLoginControls(LocalConfiguration):
    """Audited deployment selectors, exclusively read by the trusted local host."""

    login_path: str = Field(min_length=1, max_length=2048, repr=False)
    login_id: str = Field(min_length=1, max_length=512, repr=False)
    password: str = Field(min_length=1, max_length=512, repr=False)
    submit: str = Field(min_length=1, max_length=512, repr=False)
    captcha: str | None = Field(default=None, min_length=1, max_length=512, repr=False)
    mfa: str | None = Field(default=None, min_length=1, max_length=512, repr=False)
    sso: str | None = Field(default=None, min_length=1, max_length=512, repr=False)
    warning: str | None = Field(default=None, min_length=1, max_length=512, repr=False)


class DFormTaskConfiguration(LocalConfiguration):
    version: Literal["d-form-task-v1"]
    authorization_mode: Literal["actors", "fixed_owner"] = "actors"
    environment_files: tuple[Path, ...] = Field(min_length=1, max_length=2, repr=False)
    origin: str = Field(min_length=1, max_length=2048, repr=False)
    python: Path = Field(repr=False)
    # Still present in the task configuration shared with older worktrees; the
    # owned browser no longer checks the Playwright driver executable.
    playwright_node: Path = Field(repr=False)
    receipts_root: Path = Field(repr=False)
    chromium: Path | None = Field(default=None, repr=False)
    chromium_sha256: Digest | None = Field(default=None, repr=False)
    owned_runs_root: Path | None = Field(default=None, repr=False)
    login_controls: DFormLoginControls | None = Field(default=None, repr=False)
    binding: BindingSelector | None = None
    resource_record: Path | None = Field(default=None, repr=False)
    business_actor: ActorReference | None = None
    publication_actor: PublicationActorReference | None = None
    cleanup_actor: CleanupActorReference | None = None
    handoff_actor: ActorReference | None = None
    keys: KeyReferences | None = None
    pool: PoolReference | None = None
    # Trusted category candidates the AgentScope path lets the model choose from
    # (user decision 2026-10-29); absent keeps the explicit-category collection.
    category_options: tuple[CategoryOption, ...] | None = Field(
        default=None, min_length=1, max_length=64
    )

    @field_validator("category_options")
    @classmethod
    def unique_categories(cls, options: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if options is not None and (
            len(set(options)) != len(options)
            or any(
                not item.strip() or "\x00" in item or len(item.splitlines()) != 1
                for item in options
            )
        ):
            raise ValueError("d_form_config_invalid")
        return options

    @field_validator("environment_files")
    @classmethod
    def environment_paths(cls, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        if len(set(paths)) != len(paths) or any(not p.is_absolute() for p in paths):
            raise ValueError("d_form_config_invalid")
        return paths

    @field_validator("python", "playwright_node", "receipts_root", "resource_record",
                     "chromium", "owned_runs_root")
    @classmethod
    def absolute_path(cls, path: Path | None) -> Path | None:
        if path is not None and not path.is_absolute():
            raise ValueError("d_form_config_invalid")
        return path


Model = TypeVar("Model", bound=BaseModel)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DFormConfigurationError("d_form_config_invalid")
        result[key] = value
    return result


def read_local_model(path: Path, model: type[Model]) -> tuple[Model, bytes]:
    """Read only the explicitly supplied file, with bounded size and closed keys."""
    try:
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 65536:
            raise DFormConfigurationError("d_form_reference_missing")
        data = path.read_bytes()
        if len(data) > 65536:
            raise DFormConfigurationError("d_form_config_invalid")
        json.loads(data, object_pairs_hook=_unique_object)
        return model.model_validate_json(data), data
    except DFormConfigurationError:
        raise
    except ValidationError as error:
        if model is DFormTaskConfiguration and any(
            item["loc"] and item["loc"][0] == "login_controls"
            for item in error.errors(include_input=False, include_context=False)
        ):
            raise DFormConfigurationError("d_form_login_contract_missing") from None
        raise DFormConfigurationError("d_form_config_invalid") from None
    except Exception:
        raise DFormConfigurationError("d_form_config_invalid") from None


@dataclass(frozen=True, slots=True, repr=False)
class LoadedDFormConfiguration:
    config: DFormTaskConfiguration
    settings: ProductionSettings
    environment: Mapping[str, str] = field(repr=False)


def decode_key(environment: Mapping[str, str], reference: str) -> bytes:
    value = environment.get(reference)
    if not value:
        raise DFormConfigurationError("d_form_key_reference_missing")
    try:
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != 32:
            raise ValueError
        return decoded
    except Exception:
        raise DFormConfigurationError("d_form_key_reference_invalid") from None


def decode_ring(environment: Mapping[str, str], ring: KeyRingReference) -> dict[str, bytes]:
    if ring.active_id not in ring.references:
        raise DFormConfigurationError("d_form_key_reference_invalid")
    return {key: decode_key(environment, ref) for key, ref in ring.references.items()}


def missing_conditions(config: DFormTaskConfiguration) -> tuple[ConfigurationCode, ...]:
    missing: list[ConfigurationCode] = []
    if config.binding is None or (
        config.authorization_mode == "actors" and config.business_actor is None
    ):
        missing.append("d_form_identity_missing")
    if config.keys is None:
        missing.append("d_form_key_reference_missing")
    if config.pool is None:
        missing.append("d_form_pool_missing")
    if config.authorization_mode == "actors" and (
        config.publication_actor is None
        or config.cleanup_actor is None
    ):
        missing.append("d_form_authorization_missing")
    if config.authorization_mode == "fixed_owner" and any(
        actor is not None for actor in (
            config.business_actor, config.publication_actor, config.cleanup_actor,
            config.handoff_actor,
        )
    ):
        missing.append("d_form_identity_invalid")
    if config.resource_record is not None:
        missing.append("d_form_resource_scope_denied")
    if config.chromium is None or config.chromium_sha256 is None or config.owned_runs_root is None:
        missing.append("d_form_owned_deployment_missing")
    if config.login_controls is None:
        missing.append("d_form_login_contract_missing")
    return tuple(missing)


def load_configuration(
    path: Path, *, resolved_environment: Mapping[str, str] | None = None,
    windows_browser_subprocesses: bool = False,
) -> LoadedDFormConfiguration:
    if not path.is_absolute() or not path.is_file():
        raise DFormConfigurationError("d_form_config_missing")
    config, _ = read_local_model(path, DFormTaskConfiguration)
    environment: dict[str, str] = {}
    stage: DiagnosticStage = "environment_file"
    try:
        # Parse base first, so an invalid base is never hidden by an overlay.
        for selected in config.environment_files:
            stage = "environment_file"
            if not selected.is_file():
                raise ValueError
            if resolved_environment is None:
                stage = "environment_parse"
                environment.update(parse_env_file(selected))
        stage = "production_settings"
        if resolved_environment is not None:
            # The operator already applied the explicit file and Docker layers.
            environment.update(resolved_environment)
        if sys.platform == "win32" and windows_browser_subprocesses:
            database_url = make_url(get_database_url(environment))
            if database_url.drivername not in {"postgresql+psycopg", "postgresql+asyncpg"}:
                raise ValueError("windows_browser_database_driver_invalid")
            environment["DATABASE_URL"] = database_url.set(
                drivername="postgresql+asyncpg",
            ).render_as_string(hide_password=False)
        settings = ProductionSettings.from_environment(environment)
    except Exception as error:
        name: str | None = None
        category: DiagnosticCategory = "unknown"
        parse_reason: EnvParseReason | None = None
        line_number: int | None = None
        if stage == "production_settings":
            name, category = _settings_diagnostic(error)
        elif stage == "environment_file":
            if type(error) is ValueError:
                category = "missing"
            elif isinstance(error, OSError):
                category = "unreadable"
        elif isinstance(error, SmokeError):
            if error.code == "env_file_unreadable":
                category = "unreadable"
            elif error.code == "env_file_invalid":
                category = "invalid_format"
                parse_reason, line_number = error.safe_env_diagnostic()
        raise DFormConfigurationError(
            "d_form_environment_invalid", stage=stage, field_name=name, category=category,
            parse_reason=parse_reason, line_number=line_number,
        ) from None
    if not settings.browser.enabled:
        raise DFormConfigurationError(
            "d_form_environment_invalid", stage="browser_enabled",
            field_name="BROWSER_ENABLED", category="disabled",
        )
    if (
        not config.python.is_file()
        or not config.receipts_root.is_dir()
        or config.receipts_root.resolve().is_relative_to(Path(__file__).resolve().parents[3])
    ):
        raise DFormConfigurationError("d_form_reference_missing")
    # The owned route never opens a legacy resource record, even for validation.
    # Keys are consumed only through existing references; never generated or logged.
    if config.keys is not None:
        for ring in (config.keys.payload, config.keys.resource, config.keys.request_digest):
            decode_ring(environment, ring)
        for ref in (config.keys.input_digest, config.keys.result_digest, config.keys.proof_context):
            decode_key(environment, ref)
    for actor in (
        config.business_actor,
        config.publication_actor,
        config.cleanup_actor,
    ):
        if actor is not None and not environment.get(actor.token_ref):
            raise DFormConfigurationError("d_form_identity_missing")
    return LoadedDFormConfiguration(config, settings, environment)
