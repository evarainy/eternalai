"""Idempotent smoke configuration and local prerequisite checks."""

from __future__ import annotations

import base64
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from scripts.smoke.errors import SmokeError
from scripts.smoke.har import MessageCenterContract

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_RUNTIME_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "OA_BASE_URL",
    "OA_CREDENTIAL_TTL_S",
    "SESSION_COOKIE_TTL_S",
    "CSRF_ALLOWED_ORIGINS",
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
    "ETERNALAI_IDENTITY_HMAC_KEY_B64",
    "ETERNALAI_SESSION_SIGNING_KEY_B64",
    "ETERNALAI_SESSION_BINDING_KEY_B64",
    "OA_READ_ADAPTER_MODE",
    "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
    "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
    "OA_MESSAGE_CENTER_PATH",
    "OA_PENDING_WORKFLOWS_CATEGORY_ID",
    "OA_PENDING_WORKFLOWS_BIZSTATE",
    "OA_PENDING_WORKFLOWS_SELECT_STATE",
    "OA_SYSTEM_MESSAGES_CATEGORY_ID",
    "OA_SYSTEM_MESSAGES_BIZSTATE",
    "OA_SYSTEM_MESSAGES_SELECT_STATE",
    "OA_MESSAGE_CENTER_PAGE_SIZE",
)
_ALLOW_EMPTY_RUNTIME_KEYS = frozenset(
    {
        "OA_PENDING_WORKFLOWS_BIZSTATE",
        "OA_PENDING_WORKFLOWS_SELECT_STATE",
        "OA_SYSTEM_MESSAGES_BIZSTATE",
        "OA_SYSTEM_MESSAGES_SELECT_STATE",
    }
)


@dataclass(frozen=True, slots=True)
class InfraStatus:
    docker_available: bool
    postgres_reachable: bool
    redis_reachable: bool


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    added_keys: tuple[str, ...]
    missing_keys: tuple[str, ...]
    merged: dict[str, str]
    infra: InfraStatus


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse the repository's simple dotenv format without variable expansion."""

    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise SmokeError("env_file_unreadable") from None
    result: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SmokeError("env_file_invalid")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if _ENV_KEY.fullmatch(key) is None or key in result:
            raise SmokeError("env_file_invalid")
        result[key] = _parse_env_value(raw_value.strip())
    return result


def prepare_environment(
    *,
    repo_root: Path,
    base_env_path: Path,
    smoke_env_path: Path,
    contract: MessageCenterContract,
    process_environment: Mapping[str, str] | None = None,
    check_infra: bool = True,
) -> PreparedEnvironment:
    """Append missing smoke values, then validate the fully layered environment."""

    base = parse_env_file(base_env_path)
    existing = parse_env_file(smoke_env_path)
    desired = _desired_smoke_values(contract)
    added = tuple(key for key in desired if key not in existing)
    if added:
        _append_env_values(smoke_env_path, {key: desired[key] for key in added})
        existing = parse_env_file(smoke_env_path)

    merged = dict(os.environ if process_environment is None else process_environment)
    merged.update(base)
    merged.update(existing)
    missing = _missing_runtime_keys(merged)
    _validate_contract_pack_dirs(repo_root, merged, missing)
    infra = (
        check_infrastructure(merged)
        if check_infra and not missing
        else InfraStatus(False, False, False)
    )
    return PreparedEnvironment(
        added_keys=added,
        missing_keys=missing,
        merged=merged,
        infra=infra,
    )


def load_runtime_environment(
    *,
    base_env_path: Path,
    smoke_env_path: Path,
    process_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    merged = dict(os.environ if process_environment is None else process_environment)
    merged.update(parse_env_file(base_env_path))
    merged.update(parse_env_file(smoke_env_path))
    missing = _missing_runtime_keys(merged)
    if missing:
        raise SmokeError("smoke_environment_incomplete")
    return merged


def check_infrastructure(environment: Mapping[str, str]) -> InfraStatus:
    """Check reachability without printing addresses or credentials."""

    docker_available = False
    docker = shutil.which("docker")
    if docker is not None:
        try:
            completed = subprocess.run(
                [docker, "info", "--format", "{{json .ServerVersion}}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            docker_available = completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            docker_available = False
    return InfraStatus(
        docker_available=docker_available,
        postgres_reachable=_url_socket_reachable(
            environment.get("DATABASE_URL", ""),
            default_port=5432,
        ),
        redis_reachable=_url_socket_reachable(
            environment.get("REDIS_URL", ""),
            default_port=6379,
        ),
    )


def _desired_smoke_values(contract: MessageCenterContract) -> dict[str, str]:
    def key() -> str:
        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    return {
        "ENV": "production",
        "API_HOST": "127.0.0.1",
        "API_PORT": "8000",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "OA_BASE_URL": contract.base_url,
        "OA_TIMEOUT_S": "30",
        "OA_CREDENTIAL_TTL_S": "14400",
        "SESSION_COOKIE_TTL_S": "14400",
        "CSRF_ALLOWED_ORIGINS": (
            "http://127.0.0.1:5173,http://localhost:5173"
        ),
        "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64": key(),
        "ETERNALAI_IDENTITY_HMAC_KEY_B64": key(),
        "ETERNALAI_SESSION_SIGNING_KEY_B64": key(),
        "ETERNALAI_SESSION_BINDING_KEY_B64": key(),
        "OA_READ_ADAPTER_MODE": "live",
        "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR": (
            "tests/contract_packs/oa/ecology9-pending-workflows-v2"
        ),
        "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": (
            "tests/contract_packs/oa/ecology9-system-messages-v1"
        ),
        "OA_MESSAGE_CENTER_PATH": contract.endpoint_path,
        "OA_PENDING_WORKFLOWS_CATEGORY_ID": "217",
        "OA_PENDING_WORKFLOWS_BIZSTATE": contract.bizstate,
        "OA_PENDING_WORKFLOWS_SELECT_STATE": contract.select_state,
        "OA_SYSTEM_MESSAGES_CATEGORY_ID": "2,31",
        "OA_SYSTEM_MESSAGES_BIZSTATE": contract.bizstate,
        "OA_SYSTEM_MESSAGES_SELECT_STATE": contract.select_state,
        "OA_MESSAGE_CENTER_PAGE_SIZE": "20",
        "HEALTH_TIMEOUT_S": "5",
        "ETERNALAI_BACKEND_URL": "http://127.0.0.1:8000",
    }


def _append_env_values(path: Path, values: Mapping[str, str]) -> None:
    if any(
        _ENV_KEY.fullmatch(key) is None
        or "\n" in value
        or "\r" in value
        or "\x00" in value
        for key, value in values.items()
    ):
        raise SmokeError("env_value_invalid")
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_bytes() if path.exists() else b""
        prefix = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
        appended = "".join(f"{key}={value}\n" for key, value in values.items()).encode(
            "utf-8"
        )
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as writer:
            temporary_path = Path(writer.name)
            os.chmod(temporary_path, 0o600)
            writer.write(original)
            writer.write(prefix)
            writer.write(appended)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError:
        raise SmokeError("smoke_env_write_failed") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _parse_env_value(raw: str) -> str:
    if not raw:
        return ""
    if raw[0] in {"'", '"'}:
        if len(raw) < 2 or raw[-1] != raw[0]:
            raise SmokeError("env_file_invalid")
        return raw[1:-1]
    marker = raw.find(" #")
    return raw[:marker].rstrip() if marker >= 0 else raw


def _url_socket_reachable(raw_url: str, *, default_port: int) -> bool:
    try:
        parsed = urlsplit(raw_url)
        if parsed.hostname is None:
            return False
        port = parsed.port or default_port
        with socket.create_connection((parsed.hostname, port), timeout=2):
            return True
    except (OSError, ValueError):
        return False


def _validate_contract_pack_dirs(
    repo_root: Path,
    environment: Mapping[str, str],
    missing: tuple[str, ...],
) -> None:
    if missing:
        return
    for name in (
        "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
        "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
    ):
        candidate = Path(environment[name])
        resolved = candidate if candidate.is_absolute() else repo_root / candidate
        if not resolved.is_dir():
            raise SmokeError("contract_pack_directory_missing")


def _missing_runtime_keys(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        key
        for key in _REQUIRED_RUNTIME_KEYS
        if key not in environment
        or (
            key not in _ALLOW_EMPTY_RUNTIME_KEYS
            and not environment[key].strip()
        )
    )


__all__ = (
    "InfraStatus",
    "PreparedEnvironment",
    "check_infrastructure",
    "load_runtime_environment",
    "parse_env_file",
    "prepare_environment",
)
