from __future__ import annotations

import base64
from dataclasses import asdict
from pathlib import Path

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
    }


def test_production_settings_apply_approved_llm_defaults() -> None:
    settings = ProductionSettings.from_environment(_environment())

    assert settings.environment_name == "production"
    assert settings.llm_base_url == "http://34.74.11.38:8011/v1"
    assert settings.llm_model == "glm-4.7"
    assert settings.llm_timeout_seconds == 120
    assert settings.llm_max_tokens == 2048
    assert settings.llm_temperature == 0.6
    assert settings.llm_top_p == 0.95
    assert settings.llm_top_k == 20
    assert settings.llm_enable_thinking is False
    assert settings.health_timeout_seconds == 5
    assert settings.credential_encryption_key == _TEST_KEY
    assert settings.oa_read_adapter_mode == "mock"
    assert settings.oa_read_contract_pack_dir is None
    assert settings.oa_pending_workflows_path is None


def test_production_settings_repr_excludes_urls_and_key_material() -> None:
    settings = ProductionSettings.from_environment(_environment())

    rendered = repr(settings)

    assert "database-secret" not in rendered
    assert "redis-secret" not in rendered
    assert "redis.invalid" in rendered
    assert "***" in rendered
    assert _TEST_KEY_B64 not in rendered
    assert repr(_TEST_KEY) not in rendered
    serialized_redis_url = repr(asdict(settings.redis_url))
    assert "redis-secret" not in serialized_redis_url
    assert "redis.invalid" in serialized_redis_url


def test_production_settings_allow_all_vllm_endpoint_overrides() -> None:
    environment = _environment()
    environment.update(
        {
            "LLM_BASE_URL": "http://vllm.invalid:8000/v1",
            "LLM_MODEL": "qwen-restored",
            "LLM_TEMPERATURE": "0.2",
            "LLM_TOP_P": "0.8",
            "LLM_TOP_K": "10",
        }
    )

    settings = ProductionSettings.from_environment(environment)

    assert settings.llm_base_url == "http://vllm.invalid:8000/v1"
    assert settings.llm_model == "qwen-restored"
    assert settings.llm_temperature == 0.2
    assert settings.llm_top_p == 0.8
    assert settings.llm_top_k == 10


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_ENABLE_THINKING", "sometimes"),
        ("LLM_TOP_P", "0"),
        ("LLM_MAX_TOKENS", "-1"),
        ("SESSION_COOKIE_TTL_S", "0"),
        ("LLM_BASE_URL", "http://user:password@vllm.invalid/v1"),
        ("REDIS_URL", "redis://pilot:password-marker@redis.invalid:not-a-port/0"),
        ("HEALTH_TIMEOUT_S", "inf"),
        ("HEALTH_TIMEOUT_S", "nan"),
        ("HEALTH_TIMEOUT_S", "61"),
        ("OA_READ_ADAPTER_MODE", "automatic"),
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


def test_invalid_redis_url_error_masks_password_and_keeps_host() -> None:
    password_marker = "password-marker-must-not-escape"
    environment = _environment()
    environment["REDIS_URL"] = (
        f"redis://pilot:{password_marker}@redis.invalid:not-a-port/0"
    )

    with pytest.raises(RuntimeError) as captured:
        ProductionSettings.from_environment(environment)

    assert password_marker not in str(captured.value)
    assert "redis.invalid" in str(captured.value)


@pytest.mark.parametrize("mode", ["replay", "live"])
def test_oa_read_non_mock_modes_require_explicit_contract_pack(mode: str) -> None:
    environment = _environment()
    environment["OA_READ_ADAPTER_MODE"] = mode
    if mode == "live":
        environment["OA_PENDING_WORKFLOWS_PATH"] = "/api/workflow/pending"

    with pytest.raises(RuntimeError, match="OA_READ_CONTRACT_PACK_DIR"):
        ProductionSettings.from_environment(environment)


def test_oa_read_live_mode_requires_safe_host_relative_path(tmp_path: Path) -> None:
    environment = _environment()
    environment.update(
        {
            "OA_READ_ADAPTER_MODE": "live",
            "OA_READ_CONTRACT_PACK_DIR": str(tmp_path),
        }
    )

    with pytest.raises(RuntimeError, match="OA_PENDING_WORKFLOWS_PATH"):
        ProductionSettings.from_environment(environment)

    for unsafe_path in (
        "https://other.invalid/pending",
        "//other.invalid/pending",
        "/api/../admin",
        "/api/pending?cookie=unsafe",
    ):
        environment["OA_PENDING_WORKFLOWS_PATH"] = unsafe_path
        with pytest.raises(RuntimeError, match="relative path"):
            ProductionSettings.from_environment(environment)


@pytest.mark.parametrize("mode", ["replay", "live"])
def test_oa_read_modes_preserve_explicit_safe_configuration(
    mode: str,
    tmp_path: Path,
) -> None:
    contract_pack = tmp_path / "ecology9-safe-profile"
    environment = _environment()
    environment.update(
        {
            "OA_READ_ADAPTER_MODE": mode,
            "OA_READ_CONTRACT_PACK_DIR": str(contract_pack),
        }
    )
    if mode == "live":
        environment["OA_PENDING_WORKFLOWS_PATH"] = "/api/workflow/pending"

    settings = ProductionSettings.from_environment(environment)

    assert settings.oa_read_adapter_mode == mode
    assert settings.oa_read_contract_pack_dir == contract_pack
    assert settings.oa_pending_workflows_path == (
        "/api/workflow/pending" if mode == "live" else None
    )
