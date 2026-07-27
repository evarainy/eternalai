"""Fail-fast production configuration loaded from process environment."""

from __future__ import annotations

import base64
import binascii
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.db.config import get_database_url

_DEFAULT_LLM_MODEL = "qwen3.5-27b"
_DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
_DEFAULT_LLM_MAX_TOKENS = 2048
_DEFAULT_LLM_TEMPERATURE = 0.6
_DEFAULT_LLM_TOP_P = 0.95
_DEFAULT_LLM_TOP_K = 20
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0
_MAX_HEALTH_TIMEOUT_SECONDS = 60.0
_DEFAULT_OA_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ProductionSettings:
    """Validated values needed to construct the production application."""

    environment_name: str
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    oa_base_url: str
    oa_timeout_seconds: float
    oa_credential_ttl_seconds: int
    session_cookie_ttl_seconds: int
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

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> ProductionSettings:
        source = os.environ if environment is None else environment
        return cls(
            environment_name=source.get("ENV", "production").strip().casefold()
            or "production",
            database_url=get_database_url(source),
            redis_url=_required(source, "REDIS_URL"),
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
            llm_base_url=_http_base_url(source, "LLM_BASE_URL"),
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
        )


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


def _http_base_url(source: Mapping[str, str], name: str) -> str:
    value = _required(source, name).rstrip("/")
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


__all__ = ("ProductionSettings",)
