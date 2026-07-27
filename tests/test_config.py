from __future__ import annotations

import base64

import pytest

from app.config import ProductionSettings

_TEST_KEY = b"config-test-key-material-32byte!"
_TEST_KEY_B64 = base64.b64encode(_TEST_KEY).decode("ascii")


def _environment() -> dict[str, str]:
    return {
        "ENV": "production",
        "DATABASE_URL": "postgresql://user:database-secret@db.invalid/eternalai",
        "REDIS_URL": "redis://:redis-secret@redis.invalid:6379/0",
        "OA_BASE_URL": "https://oa.invalid",
        "OA_CREDENTIAL_TTL_S": "3600",
        "SESSION_COOKIE_TTL_S": "1800",
        "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64": _TEST_KEY_B64,
        "ETERNALAI_IDENTITY_HMAC_KEY_B64": _TEST_KEY_B64,
        "ETERNALAI_SESSION_SIGNING_KEY_B64": _TEST_KEY_B64,
        "ETERNALAI_SESSION_BINDING_KEY_B64": _TEST_KEY_B64,
        "LLM_BASE_URL": "http://vllm.invalid:8000/v1",
    }


def test_production_settings_apply_approved_llm_defaults() -> None:
    settings = ProductionSettings.from_environment(_environment())

    assert settings.environment_name == "production"
    assert settings.llm_base_url == "http://vllm.invalid:8000/v1"
    assert settings.llm_model == "qwen3.5-27b"
    assert settings.llm_timeout_seconds == 120
    assert settings.llm_max_tokens == 2048
    assert settings.llm_temperature == 0.6
    assert settings.llm_top_p == 0.95
    assert settings.llm_top_k == 20
    assert settings.llm_enable_thinking is False
    assert settings.health_timeout_seconds == 5
    assert settings.credential_encryption_key == _TEST_KEY


def test_production_settings_repr_excludes_urls_and_key_material() -> None:
    settings = ProductionSettings.from_environment(_environment())

    rendered = repr(settings)

    assert "database-secret" not in rendered
    assert "redis-secret" not in rendered
    assert _TEST_KEY_B64 not in rendered
    assert repr(_TEST_KEY) not in rendered


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_ENABLE_THINKING", "sometimes"),
        ("LLM_TOP_P", "0"),
        ("LLM_MAX_TOKENS", "-1"),
        ("SESSION_COOKIE_TTL_S", "0"),
        ("LLM_BASE_URL", "http://user:password@vllm.invalid/v1"),
        ("HEALTH_TIMEOUT_S", "inf"),
        ("HEALTH_TIMEOUT_S", "nan"),
        ("HEALTH_TIMEOUT_S", "61"),
    ],
)
def test_production_settings_fail_closed_on_invalid_values(
    name: str,
    value: str,
) -> None:
    environment = _environment()
    environment[name] = value

    with pytest.raises(RuntimeError):
        ProductionSettings.from_environment(environment)


def test_production_settings_require_every_secret_without_echoing_values() -> None:
    environment = _environment()
    environment.pop("ETERNALAI_SESSION_SIGNING_KEY_B64")

    with pytest.raises(
        RuntimeError,
        match="ETERNALAI_SESSION_SIGNING_KEY_B64 is required",
    ):
        ProductionSettings.from_environment(environment)
