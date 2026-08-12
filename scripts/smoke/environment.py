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
from scripts.smoke.har import MessageCenterContract, TodoListContract

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_RUNTIME_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "OA_BASE_URL",
    "OA_CREDENTIAL_TTL_S",
    "SESSION_COOKIE_TTL_S",
    "SESSION_COOKIE_SECURE",
    "CSRF_ALLOWED_ORIGINS",
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
    "ETERNALAI_IDENTITY_HMAC_KEY_B64",
    "ETERNALAI_SESSION_SIGNING_KEY_B64",
    "ETERNALAI_SESSION_BINDING_KEY_B64",
    "OA_READ_ADAPTER_MODE",
    "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
    "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
    "OA_MESSAGE_CENTER_PATH",
    "OA_PENDING_WORKFLOWS_SPLIT_PAGE_KEY_PATH",
    "OA_PENDING_WORKFLOWS_COUNTS_PATH",
    "OA_PENDING_WORKFLOWS_DATAS_PATH",
    "OA_PENDING_WORKFLOWS_ACTIONTYPE",
    "OA_PENDING_WORKFLOWS_HIDE_NO_DATA_TAB",
    "OA_PENDING_WORKFLOWS_METHOD",
    "OA_PENDING_WORKFLOWS_OFFICAL_TYPE",
    "OA_PENDING_WORKFLOWS_VIEW_SCOPE",
    "OA_PENDING_WORKFLOWS_SORT_PARAMS",
    "OA_SYSTEM_MESSAGES_CATEGORY_ID",
    "OA_SYSTEM_MESSAGES_BIZSTATE",
    "OA_SYSTEM_MESSAGES_SELECT_STATE",
    "OA_MESSAGE_CENTER_PAGE_SIZE",
)
_ALLOW_EMPTY_RUNTIME_KEYS = frozenset(
    {
        "OA_SYSTEM_MESSAGES_BIZSTATE",
        "OA_SYSTEM_MESSAGES_SELECT_STATE",
    }
)
_REQUIRED_PENDING_WORKFLOW_KEYS = (
    "OA_PENDING_WORKFLOWS_SPLIT_PAGE_KEY_PATH",
    "OA_PENDING_WORKFLOWS_COUNTS_PATH",
    "OA_PENDING_WORKFLOWS_DATAS_PATH",
    "OA_PENDING_WORKFLOWS_ACTIONTYPE",
    "OA_PENDING_WORKFLOWS_HIDE_NO_DATA_TAB",
    "OA_PENDING_WORKFLOWS_METHOD",
    "OA_PENDING_WORKFLOWS_OFFICAL_TYPE",
    "OA_PENDING_WORKFLOWS_VIEW_SCOPE",
    "OA_PENDING_WORKFLOWS_SORT_PARAMS",
)
_OBSOLETE_PENDING_WORKFLOW_KEYS = (
    "OA_PENDING_WORKFLOWS_BIZSTATE",
    "OA_PENDING_WORKFLOWS_CATEGORY_ID",
    "OA_PENDING_WORKFLOWS_SELECT_STATE",
)
_PENDING_PACK_KEY = "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR"
_PENDING_PACK_V2 = "tests/contract_packs/oa/ecology9-pending-workflows-v2"
_PENDING_PACK_V3 = "tests/contract_packs/oa/ecology9-pending-workflows-v3"
_ENVIRONMENT_DRIFT_ERROR_CODES = frozenset(
    {
        "smoke_environment_incomplete",
        "smoke_pending_workflows_keys_missing",
        "smoke_pending_workflows_obsolete_keys_present",
        "smoke_pending_workflows_contract_pack_stale",
    }
)
_PREPARE_ERROR_CODES = frozenset(
    {
        "contract_pack_directory_missing",
        "env_file_invalid",
        "env_file_unreadable",
        "env_value_invalid",
        "oa_har_base_url_mismatch",
        "smoke_env_write_failed",
        *_ENVIRONMENT_DRIFT_ERROR_CODES,
    }
)
_LOAD_ERROR_CODES = frozenset(
    {
        "env_file_invalid",
        "env_file_unreadable",
        *_ENVIRONMENT_DRIFT_ERROR_CODES,
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
    todo_contract: TodoListContract,
    process_environment: Mapping[str, str] | None = None,
    check_infra: bool = True,
    repair: bool = False,
) -> PreparedEnvironment:
    """Append missing defaults; rewrite stale or obsolete entries only on repair."""

    failure_code = "smoke_environment_rejected"
    try:
        return _prepare_environment(
            repo_root=repo_root,
            base_env_path=base_env_path,
            smoke_env_path=smoke_env_path,
            contract=contract,
            todo_contract=todo_contract,
            process_environment=process_environment,
            check_infra=check_infra,
            repair=repair,
        )
    except SmokeError as error:
        failure_code = _safe_prepare_error_code(error)
    except Exception:
        failure_code = "smoke_environment_rejected"
    finally:
        del (
            repo_root,
            base_env_path,
            smoke_env_path,
            contract,
            todo_contract,
            process_environment,
            check_infra,
            repair,
        )
    raise SmokeError(failure_code) from None


def _prepare_environment(
    *,
    repo_root: Path,
    base_env_path: Path,
    smoke_env_path: Path,
    contract: MessageCenterContract,
    todo_contract: TodoListContract,
    process_environment: Mapping[str, str] | None,
    check_infra: bool,
    repair: bool,
) -> PreparedEnvironment:
    base = parse_env_file(base_env_path)
    existing = parse_env_file(smoke_env_path)
    desired = _desired_smoke_values(contract, todo_contract)
    inherited = dict(
        os.environ if process_environment is None else process_environment
    )
    inherited.update(base)
    if not repair:
        _raise_smoke_environment_drift(existing, include_missing=False)

    added = tuple(key for key in desired if key not in existing)
    removed: tuple[str, ...] = ()
    updated: dict[str, str] = {}
    if repair:
        removed = tuple(
            key for key in _OBSOLETE_PENDING_WORKFLOW_KEYS if key in existing
        )
        if existing.get(_PENDING_PACK_KEY) == _PENDING_PACK_V2:
            updated[_PENDING_PACK_KEY] = _PENDING_PACK_V3

    candidate = dict(existing)
    for key in removed:
        candidate.pop(key)
    candidate.update(updated)
    candidate.update({key: desired[key] for key in added})
    merged = dict(inherited)
    merged.update(candidate)
    _raise_smoke_environment_drift(candidate)

    missing = _missing_runtime_keys(merged)
    if repair and missing:
        raise SmokeError("smoke_environment_incomplete")
    _validate_contract_pack_dirs(repo_root, merged, missing)
    infra = (
        check_infrastructure(merged)
        if check_infra and not missing
        else InfraStatus(False, False, False)
    )
    if added or removed or updated:
        _rewrite_env_values_atomically(
            smoke_env_path,
            additions={key: desired[key] for key in added},
            removals=removed,
            updates=updated,
        )
    return PreparedEnvironment(
        added_keys=added,
        missing_keys=missing,
        merged=merged,
        infra=infra,
    )


def _safe_prepare_error_code(error: SmokeError) -> str:
    if (
        len(error.args) == 1
        and isinstance(error.args[0], str)
        and error.args[0] in _PREPARE_ERROR_CODES
    ):
        return error.args[0]
    return "smoke_environment_rejected"


def load_runtime_environment(
    *,
    base_env_path: Path,
    smoke_env_path: Path,
    process_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    failure_code = "smoke_environment_rejected"
    try:
        return _load_runtime_environment(
            base_env_path=base_env_path,
            smoke_env_path=smoke_env_path,
            process_environment=process_environment,
        )
    except SmokeError as error:
        failure_code = _safe_load_error_code(error)
    except Exception:
        failure_code = "smoke_environment_rejected"
    finally:
        del base_env_path, smoke_env_path, process_environment
    raise SmokeError(failure_code) from None


def _load_runtime_environment(
    *,
    base_env_path: Path,
    smoke_env_path: Path,
    process_environment: Mapping[str, str] | None,
) -> dict[str, str]:
    smoke = parse_env_file(smoke_env_path)
    _raise_smoke_environment_drift(smoke)
    merged = dict(os.environ if process_environment is None else process_environment)
    merged.update(parse_env_file(base_env_path))
    merged.update(smoke)
    if _missing_runtime_keys(merged):
        raise SmokeError("smoke_environment_incomplete")
    return merged


def _safe_load_error_code(error: SmokeError) -> str:
    if (
        len(error.args) == 1
        and isinstance(error.args[0], str)
        and error.args[0] in _LOAD_ERROR_CODES
    ):
        return error.args[0]
    return "smoke_environment_rejected"


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


def _desired_smoke_values(
    contract: MessageCenterContract,
    todo_contract: TodoListContract,
) -> dict[str, str]:
    if contract.base_url != todo_contract.base_url:
        raise SmokeError("oa_har_base_url_mismatch")

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
        "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR": _PENDING_PACK_V3,
        "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR": (
            "tests/contract_packs/oa/ecology9-system-messages-v1"
        ),
        "OA_MESSAGE_CENTER_PATH": contract.endpoint_path,
        "OA_PENDING_WORKFLOWS_SPLIT_PAGE_KEY_PATH": (
            todo_contract.split_page_key_path
        ),
        "OA_PENDING_WORKFLOWS_COUNTS_PATH": todo_contract.counts_path,
        "OA_PENDING_WORKFLOWS_DATAS_PATH": todo_contract.datas_path,
        "OA_PENDING_WORKFLOWS_ACTIONTYPE": todo_contract.actiontype,
        "OA_PENDING_WORKFLOWS_HIDE_NO_DATA_TAB": (
            todo_contract.hide_no_data_tab
        ),
        "OA_PENDING_WORKFLOWS_METHOD": todo_contract.method,
        "OA_PENDING_WORKFLOWS_OFFICAL_TYPE": todo_contract.offical_type,
        "OA_PENDING_WORKFLOWS_VIEW_SCOPE": todo_contract.view_scope,
        "OA_PENDING_WORKFLOWS_SORT_PARAMS": todo_contract.sort_params,
        "OA_SYSTEM_MESSAGES_CATEGORY_ID": "2,31",
        "OA_SYSTEM_MESSAGES_BIZSTATE": contract.bizstate,
        "OA_SYSTEM_MESSAGES_SELECT_STATE": contract.select_state,
        "OA_MESSAGE_CENTER_PAGE_SIZE": "20",
        "HEALTH_TIMEOUT_S": "5",
        "ETERNALAI_BACKEND_URL": "http://127.0.0.1:8000",
    }


def _append_env_values(path: Path, values: Mapping[str, str]) -> None:
    _rewrite_env_values_atomically(
        path,
        additions=values,
        removals=(),
        updates={},
    )


def _rewrite_env_values_atomically(
    path: Path,
    *,
    additions: Mapping[str, str],
    removals: tuple[str, ...],
    updates: Mapping[str, str],
) -> None:
    mutation_values = {**additions, **updates}
    if any(
        _ENV_KEY.fullmatch(key) is None
        or "\n" in value
        or "\r" in value
        or "\x00" in value
        or not _env_value_round_trips(value)
        for key, value in mutation_values.items()
    ) or any(_ENV_KEY.fullmatch(key) is None for key in removals):
        raise SmokeError("env_value_invalid")
    target_sets = (set(additions), set(removals), set(updates))
    if any(
        left & right
        for index, left in enumerate(target_sets)
        for right in target_sets[index + 1 :]
    ):
        raise SmokeError("env_value_invalid")
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = parse_env_file(path)
        if set(additions) & set(existing) or not set(updates) <= set(existing):
            raise SmokeError("env_file_invalid")
        original = path.read_bytes() if path.exists() else b""
        candidate = _render_env_mutations(
            original,
            additions=additions,
            removals=frozenset(removals),
            updates=updates,
        )
        if candidate == original:
            return
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as writer:
            temporary_path = Path(writer.name)
            os.chmod(temporary_path, 0o600)
            writer.write(candidate)
            writer.flush()
            os.fsync(writer.fileno())
        reparsed = parse_env_file(temporary_path)
        expected = dict(existing)
        for key in removals:
            expected.pop(key, None)
        expected.update(updates)
        expected.update(additions)
        if reparsed != expected:
            raise SmokeError("env_value_invalid")
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


def _render_env_mutations(
    original: bytes,
    *,
    additions: Mapping[str, str],
    removals: frozenset[str],
    updates: Mapping[str, str],
) -> bytes:
    text = original.decode("utf-8")
    rendered: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        key = _env_assignment_key(raw_line)
        if key in removals:
            continue
        if key in updates:
            rendered.append(_replace_env_assignment_value(raw_line, updates[key]))
        else:
            rendered.append(raw_line)
    result = "".join(rendered)
    if additions:
        if result and not result.endswith(("\n", "\r")):
            result += "\n"
        result += "".join(f"{key}={value}\n" for key, value in additions.items())
    return result.encode("utf-8")


def _env_assignment_key(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    return line.split("=", 1)[0].strip()


def _replace_env_assignment_value(raw_line: str, value: str) -> str:
    if raw_line.endswith("\r\n"):
        content, ending = raw_line[:-2], "\r\n"
    elif raw_line.endswith(("\n", "\r")):
        content, ending = raw_line[:-1], raw_line[-1]
    else:
        content, ending = raw_line, ""
    prefix, raw_value = content.split("=", 1)
    comment_at = raw_value.find(" #")
    comment = raw_value[comment_at:] if comment_at >= 0 else ""
    return f"{prefix}={value}{comment}{ending}"


def _env_value_round_trips(value: str) -> bool:
    try:
        return _parse_env_value(value.strip()) == value
    except SmokeError:
        return False


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


def _raise_smoke_environment_drift(
    smoke_environment: Mapping[str, str],
    *,
    include_missing: bool = True,
) -> None:
    if any(
        key in smoke_environment for key in _OBSOLETE_PENDING_WORKFLOW_KEYS
    ):
        raise SmokeError("smoke_pending_workflows_obsolete_keys_present")
    pending_pack = smoke_environment.get(_PENDING_PACK_KEY)
    if pending_pack is not None and pending_pack != _PENDING_PACK_V3:
        raise SmokeError("smoke_pending_workflows_contract_pack_stale")
    if include_missing and any(
        key not in smoke_environment for key in _REQUIRED_PENDING_WORKFLOW_KEYS
    ):
        raise SmokeError("smoke_pending_workflows_keys_missing")


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
