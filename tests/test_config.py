from __future__ import annotations

import base64
import re
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
        "CSRF_ALLOWED_ORIGINS": (
            "https://app.example.gov.cn,http://127.0.0.1:5173"
        ),
        "PHASE0_MOCK_MODE": "true",
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
    assert settings.csrf_allowed_origins == frozenset(
        {"https://app.example.gov.cn", "http://127.0.0.1:5173"}
    )
    assert settings.credential_encryption_key == _TEST_KEY
    assert settings.oa_read_adapter_mode == "mock"
    assert settings.oa_read_contract_pack_dir is None
    assert settings.oa_pending_workflows_contract_pack_dir is None
    assert settings.oa_system_messages_contract_pack_dir is None
    assert settings.oa_pending_workflows_path is None
    assert settings.oa_system_messages_path is None
    assert settings.phase0_mock_mode is True


@pytest.mark.parametrize("configured_mode", [None, "mock"])
def test_non_testing_mock_mode_requires_explicit_mock_flag(
    configured_mode: str | None,
) -> None:
    environment = _environment()
    environment.pop("PHASE0_MOCK_MODE")
    if configured_mode is None:
        environment.pop("OA_READ_ADAPTER_MODE", None)
    else:
        environment["OA_READ_ADAPTER_MODE"] = configured_mode

    with pytest.raises(
        RuntimeError,
        match="requires ENV=testing or PHASE0_MOCK_MODE=true",
    ):
        ProductionSettings.from_environment(environment)


def test_testing_environment_keeps_default_mock_without_phase_flag() -> None:
    environment = _environment()
    environment["ENV"] = "testing"
    environment.pop("PHASE0_MOCK_MODE")
    environment.pop("OA_READ_ADAPTER_MODE", None)

    settings = ProductionSettings.from_environment(environment)

    assert settings.oa_read_adapter_mode == "mock"
    assert settings.phase0_mock_mode is False


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
        ("PHASE0_MOCK_MODE", "sometimes"),
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


def test_production_settings_require_csrf_allowed_origins() -> None:
    environment = _environment()
    environment.pop("CSRF_ALLOWED_ORIGINS")

    with pytest.raises(RuntimeError, match="CSRF_ALLOWED_ORIGINS is required"):
        ProductionSettings.from_environment(environment)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "https://app.example.gov.cn,",
        "https://app.example.gov.cn, https://admin.example.gov.cn",
        "https://app.example.gov.cn,https://app.example.gov.cn",
        "https://*.example.gov.cn",
        "ftp://app.example.gov.cn",
        "https://user:password@app.example.gov.cn",
        "https://app.example.gov.cn/admin",
        "https://app.example.gov.cn?mode=admin",
        "https://app.example.gov.cn#admin",
        "https://app.example.gov.cn/",
        "HTTPS://app.example.gov.cn",
        "https://app.example.gov.cn:443",
        "https://999.999.999.999",
    ],
)
def test_production_settings_reject_noncanonical_csrf_origins(value: str) -> None:
    environment = _environment()
    environment["CSRF_ALLOWED_ORIGINS"] = value

    with pytest.raises(RuntimeError, match="CSRF_ALLOWED_ORIGINS"):
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


def test_oa_read_replay_mode_requires_explicit_contract_pack() -> None:
    environment = _environment()
    environment["OA_READ_ADAPTER_MODE"] = "replay"

    with pytest.raises(RuntimeError, match="OA_READ_CONTRACT_PACK_DIR"):
        ProductionSettings.from_environment(environment)


@pytest.mark.parametrize(
    "missing_name",
    [
        "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
        "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
        "OA_PENDING_WORKFLOWS_PATH",
        "OA_SYSTEM_MESSAGES_PATH",
    ],
)
def test_oa_read_live_mode_requires_each_capability_configuration(
    missing_name: str,
    tmp_path: Path,
) -> None:
    environment = _environment()
    environment.update(
        {
            "OA_READ_ADAPTER_MODE": "live",
            "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR": str(tmp_path / "pending"),
            "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": str(tmp_path / "messages"),
            "OA_PENDING_WORKFLOWS_PATH": "/api/workflow/pending",
            "OA_SYSTEM_MESSAGES_PATH": "/api/system/messages",
        }
    )
    environment.pop(missing_name)

    with pytest.raises(RuntimeError, match=re.escape(missing_name)):
        ProductionSettings.from_environment(environment)


@pytest.mark.parametrize(
    "path_name",
    ["OA_PENDING_WORKFLOWS_PATH", "OA_SYSTEM_MESSAGES_PATH"],
)
def test_oa_read_live_mode_requires_safe_host_relative_paths(
    path_name: str,
    tmp_path: Path,
) -> None:
    environment = _environment()
    environment.update(
        {
            "OA_READ_ADAPTER_MODE": "live",
            "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR": str(tmp_path / "pending"),
            "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": str(tmp_path / "messages"),
            "OA_PENDING_WORKFLOWS_PATH": "/api/workflow/pending",
            "OA_SYSTEM_MESSAGES_PATH": "/api/system/messages",
        }
    )
    for unsafe_path in (
        "https://other.invalid/pending",
        "//other.invalid/pending",
        "/api/../admin",
        "/api/pending?cookie=unsafe",
    ):
        environment[path_name] = unsafe_path
        with pytest.raises(RuntimeError, match="relative path"):
            ProductionSettings.from_environment(environment)


def test_pending_workflows_config_keeps_existing_backslash_semantics(
    tmp_path: Path,
) -> None:
    environment = _environment()
    environment.update(
        {
            "OA_READ_ADAPTER_MODE": "live",
            "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR": str(tmp_path / "pending"),
            "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": str(tmp_path / "messages"),
            "OA_PENDING_WORKFLOWS_PATH": r"/api\workflow\pending",
            "OA_SYSTEM_MESSAGES_PATH": "/api/system/messages",
        }
    )

    settings = ProductionSettings.from_environment(environment)

    assert settings.oa_pending_workflows_path == r"/api\workflow\pending"


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
        environment.update(
            {
                "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": str(
                    tmp_path / "ecology9-system-messages-v1"
                ),
                "OA_PENDING_WORKFLOWS_PATH": "/api/workflow/pending",
                "OA_SYSTEM_MESSAGES_PATH": "/api/system/messages",
            }
        )

    settings = ProductionSettings.from_environment(environment)

    assert settings.oa_read_adapter_mode == mode
    assert settings.oa_read_contract_pack_dir == contract_pack
    assert settings.oa_pending_workflows_contract_pack_dir == (
        contract_pack if mode == "live" else None
    )
    assert settings.oa_system_messages_contract_pack_dir == (
        tmp_path / "ecology9-system-messages-v1" if mode == "live" else None
    )
    assert settings.oa_pending_workflows_path == (
        "/api/workflow/pending" if mode == "live" else None
    )
    assert settings.oa_system_messages_path == (
        "/api/system/messages" if mode == "live" else None
    )


def test_live_explicit_pending_pack_takes_priority_over_legacy_alias(
    tmp_path: Path,
) -> None:
    legacy_pack = tmp_path / "legacy-pending"
    pending_pack = tmp_path / "pending"
    system_pack = tmp_path / "messages"
    environment = _environment()
    environment.update(
        {
            "OA_READ_ADAPTER_MODE": "live",
            "OA_READ_CONTRACT_PACK_DIR": str(legacy_pack),
            "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR": str(pending_pack),
            "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": str(system_pack),
            "OA_PENDING_WORKFLOWS_PATH": "/api/workflow/pending",
            "OA_SYSTEM_MESSAGES_PATH": "/api/system/messages",
        }
    )

    settings = ProductionSettings.from_environment(environment)

    assert settings.oa_read_contract_pack_dir == legacy_pack
    assert settings.oa_pending_workflows_contract_pack_dir == pending_pack
    assert settings.oa_system_messages_contract_pack_dir == system_pack
