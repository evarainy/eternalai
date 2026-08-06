"""CLI for the two-command OA intranet smoke workflow."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import getpass
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from app.composition import (
    build_authentication_port,
    build_credential_store,
    build_principal_role_reader,
)
from app.config import ProductionSettings
from app.db.session import make_async_engine, make_async_session_factory
from app.event_loop import make_event_loop
from app.infra.adapters.oa.contracts import (
    OAStructuralDriftReport,
    OAStructuralNode,
    compare_structural_fingerprints,
)
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    OALiveHTTPServerError,
    OALiveIdentityExpired,
    OALiveIdentityUnbound,
    OALivePayloadInvalid,
    OALivePermissionDenied,
    OALiveProviderError,
    OALiveRequestError,
    OALiveTimeout,
)
from app.infra.auth.oa import OAAuthenticationError
from app.infra.persistence.capability_registry.repository import (
    PostgreSQLCapabilityRegistry,
)
from app.knowledge import BasicKnowledge
from app.ports.auth import AuthenticationError, LoginCredential, OASessionCredential
from app.ports.capability_registry import CapabilitySpec
from scripts import sanitize_oa_contract_pack as sanitizer
from scripts.smoke.capabilities import (
    OA_CAPABILITY_CONTEXT_PROBES,
    REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
    expected_oa_capabilities,
)
from scripts.smoke.environment import (
    PreparedEnvironment,
    load_runtime_environment,
    prepare_environment,
)
from scripts.smoke.errors import SmokeError
from scripts.smoke.har import extract_message_center_contract, har_entry_count
from scripts.smoke.live import (
    ProtocolEvidence,
    ProtocolSummary,
    RecordingOpener,
    compare_record_structures,
)

_SYSTEM_PROFILE = "ecology9-system-messages-v1"
_PENDING_V2_PROFILE = "ecology9-pending-workflows-v2"
_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}$")
_BACKEND_URL = "http://127.0.0.1:8000"
_BACKEND_HEALTH_CHECK_TIMEOUT_SECONDS = 5.0
_BACKEND_HEALTH_TIMEOUT_MARGIN_SECONDS = 2.0
_BACKEND_HEALTH_HTTP_TIMEOUT_SECONDS = (
    _BACKEND_HEALTH_CHECK_TIMEOUT_SECONDS + _BACKEND_HEALTH_TIMEOUT_MARGIN_SECONDS
)
_BACKEND_HEALTH_COMPONENTS = ("database", "redis", "vllm")
_BACKEND_HEALTH_COMPONENT_STATES = frozenset({"ok", "failed"})
_BACKEND_HEALTH_RESPONSE_INVALID = "health_response_invalid"
_BACKEND_HEALTH_COMPONENT_FAILED = "health_component_failed"
_BACKEND_HEALTH_CONNECTION_FAILED = "health_connection_failed"
_FRONTEND_URL = "http://127.0.0.1:5173"
_LOGIN_ORIGIN = _FRONTEND_URL
_LOCAL_OPENER = build_opener(ProxyHandler({}))
_BACKEND_LOG_NAME = "smoke_backend.log"
_PROCESS_STATE_VERSION = 3
_CONFIGURATION_ERROR_MARKERS = (
    "ProductionSettings.from_environment",
    "OA_READ_ADAPTER_MODE",
    "OA_BASE_URL",
    "OA_MESSAGE_CENTER_PATH",
    "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
    "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
    "ETERNALAI_IDENTITY_HMAC_KEY_B64",
    "ETERNALAI_SESSION_SIGNING_KEY_B64",
    "ETERNALAI_SESSION_BINDING_KEY_B64",
    "CSRF_ALLOWED_ORIGINS",
)
_AUTH_FAILURE_STAGE_PATTERN = re.compile(
    r"oa_authentication_failure_stage=([a-z_]+)"
)
_AUTH_DIAGNOSTIC_PATTERN = re.compile(
    r"oa_authentication_failure_diagnostic_([a-z_]+)=([a-z0-9]+)"
)
_AUTH_DIAGNOSTIC_ORDER = (
    "rsa_response_field_count",
    "rsa_pub_present",
    "rsa_pub_type",
    "rsa_pub_character_count",
    "rsa_code_present",
    "rsa_code_type",
    "rsa_code_character_count",
    "rsa_flag_present",
    "rsa_flag_type",
    "rsa_flag_character_count",
)
_AUTH_DIAGNOSTIC_TYPES = frozenset(
    {
        "array",
        "boolean",
        "integer",
        "missing",
        "null",
        "number",
        "object",
        "other",
        "string",
    }
)
_SAFE_PROTOCOL_FIELD_NAMES = frozenset(
    {
        "applicant",
        "approver",
        "bizstate",
        "context",
        "createdAt",
        "currentStep",
        "data",
        "expired",
        "gomethod",
        "gomethodpc",
        "link",
        "linkmobileurl",
        "maxtime",
        "messageid",
        "mintime",
        "msgid",
        "name",
        "showimage",
        "status",
        "time",
        "title",
        "workflowId",
    }
)
_AUTH_FAILURE_DETAILS = {
    "oa_session_setup_failed": "无法创建 OA 登录会话。",
    "oa_rsa_request_failed": "无法取得 OA 登录所需的 RSA 参数。",
    "oa_rsa_public_key_missing_or_invalid": "OA 返回的 RSA 公钥缺失或格式无效。",
    "oa_rsa_code_type_invalid": "OA 返回的 RSA 随机码类型无效。",
    "oa_rsa_flag_type_invalid": "OA 返回的 RSA 标记类型无效。",
    "oa_credential_encryption_failed": "无法按 OA 规则加密账号密码。",
    "oa_login_request_failed": "OA 登录接口没有返回可识别结果。",
    "oa_credentials_rejected": "OA 登录接口明确拒绝了本次账号密码。",
    "oa_identity_response_invalid": "OA 登录成功响应缺少有效用户编号。",
    "oa_required_cookies_missing": "OA 登录响应缺少后续访问所需的 Cookie。",
    "oa_user_info_request_failed": "无法读取 OA 当前用户信息。",
    "oa_user_info_response_invalid": "OA 当前用户信息结构不完整。",
    "local_identity_derivation_failed": "本地用户标识生成失败。",
    "local_role_lookup_failed": "本地角色读取失败。",
    "local_credential_store_failed": "OA 会话写入本地安全存储失败。",
    "local_principal_build_failed": "本地登录身份生成失败。",
}


class _NoEchoParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise SmokeError("invalid_arguments", exit_code=2)


@dataclass(frozen=True, slots=True)
class Layout:
    repo_root: Path
    shared_root: Path
    base_env: Path
    smoke_env: Path
    source_har: Path
    scratch: Path


@dataclass(frozen=True, slots=True)
class RehearsalResult:
    node_count: int
    added_count: int
    removed_count: int
    changed_count: int
    sha_matches: bool
    replay_composition_ok: bool
    drift: OAStructuralDriftReport


@dataclass(frozen=True, slots=True)
class LiveOutcome:
    drift: OAStructuralDriftReport | None
    protocol: ProtocolSummary
    normalized: bool
    error_kind: str | None


@dataclass(frozen=True, slots=True)
class CapabilityRegistryPreflight:
    state: str
    found_count: int
    valid_count: int
    unexpected_active_count: int
    active_total_count: int
    visible_probe_count: int


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        layout = _resolve_layout()
        if args.command == "prepare":
            return _command_prepare(layout)
        if args.command == "rehearse":
            return _command_rehearse(layout)
        if args.command == "start":
            return _command_start(layout)
        if args.command == "verify":
            return _command_verify(
                layout,
                timestamp=args.timestamp,
                har_directory=args.har_directory,
            )
        raise SmokeError("invalid_arguments", exit_code=2)
    except sanitizer.SanitizationError as exc:
        print(f"sanitization failed: {exc}", file=sys.stderr)
        _print_stop_instruction(file=sys.stderr)
        return 2
    except SmokeError as exc:
        print(f"smoke failed: {exc.code}", file=sys.stderr)
        _print_stop_instruction(file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("smoke failed: interrupted", file=sys.stderr)
        _print_stop_instruction(file=sys.stderr)
        return 130
    except Exception:
        print("smoke failed: unexpected_error", file=sys.stderr)
        _print_stop_instruction(file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _NoEchoParser(description="EternalAI OA intranet smoke runner")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_NoEchoParser,
    )
    subparsers.add_parser("prepare")
    subparsers.add_parser("rehearse")
    subparsers.add_parser("start")
    verify = subparsers.add_parser("verify")
    verify.add_argument("-Timestamp", "--timestamp")
    verify.add_argument("-Har", "--har", dest="har_directory", type=Path)
    return parser


def _resolve_layout() -> Layout:
    repo_root = Path(__file__).resolve().parents[2]
    shared_root = _shared_worktree_root(repo_root)
    base_env = repo_root / ".env"
    if not base_env.is_file():
        base_env = shared_root / ".env"
    source_har = repo_root / "_scratch" / "oa" / "消息中心.har"
    if not source_har.is_file():
        source_har = shared_root / "_scratch" / "oa" / "消息中心.har"
    return Layout(
        repo_root=repo_root,
        shared_root=shared_root,
        base_env=base_env,
        smoke_env=shared_root / ".env.smoke",
        source_har=source_har,
        scratch=repo_root / "_scratch",
    )


def _shared_worktree_root(repo_root: Path) -> Path:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return repo_root
    if completed.returncode != 0:
        return repo_root
    common_dir = Path(completed.stdout.strip())
    return common_dir.parent if common_dir.name == ".git" else repo_root


def _command_prepare(layout: Layout) -> int:
    contract = extract_message_center_contract(layout.source_har)
    prepared = prepare_environment(
        repo_root=layout.repo_root,
        base_env_path=layout.base_env,
        smoke_env_path=layout.smoke_env,
        contract=contract,
    )
    print(f"har_entries={har_entry_count(layout.source_har)}")
    print(f"message_center_candidates={contract.matching_entry_count}")
    print(f"message_center_source_entry={contract.source_entry_index}")
    print("message_center_contract_recognized=true")
    print(f"smoke_env_added_keys={len(prepared.added_keys)}")
    _print_infra(prepared)
    if prepared.missing_keys:
        print("missing=" + ",".join(prepared.missing_keys))
        return 1
    _validate_settings(prepared.merged)
    print("missing=none")
    return 0


def _print_infra(prepared: PreparedEnvironment) -> None:
    print(f"docker_available={_bool(prepared.infra.docker_available)}")
    print(f"postgres_reachable={_bool(prepared.infra.postgres_reachable)}")
    print(f"redis_reachable={_bool(prepared.infra.redis_reachable)}")
    if not prepared.infra.docker_available:
        print("docker_help=Start Docker Desktop, then rerun prepare.")
    if not prepared.infra.postgres_reachable:
        print(
            "postgres_help=docker compose --profile core-infra up -d postgres"
        )
    if not prepared.infra.redis_reachable:
        print("redis_help=docker compose --profile core-infra up -d redis")


def _command_rehearse(layout: Layout) -> int:
    environment = load_runtime_environment(
        base_env_path=layout.base_env,
        smoke_env_path=layout.smoke_env,
    )
    _validate_settings(environment)
    result = _run_rehearsal(layout, environment)
    print(f"fingerprint_nodes={result.node_count}")
    print(f"fingerprint_added={result.added_count}")
    print(f"fingerprint_removed={result.removed_count}")
    print(f"fingerprint_changed={result.changed_count}")
    _print_drift_nodes("fingerprint", result.drift)
    print(f"fingerprint_sha_matches={_bool(result.sha_matches)}")
    print(f"replay_composition={_passed(result.replay_composition_ok)}")
    if (
        result.added_count
        or result.removed_count
        or result.changed_count
        or not result.sha_matches
        or not result.replay_composition_ok
    ):
        return 1
    return 0


def _run_rehearsal(
    layout: Layout,
    environment: dict[str, str],
) -> RehearsalResult:
    contract = extract_message_center_contract(layout.source_har)
    run_root = _unique_run_root(layout.scratch / "smoke_rehearsal")
    output_dir = run_root / _SYSTEM_PROFILE
    sanitizer.sanitize_har_to_contract_pack(
        input_har=layout.source_har,
        output_dir=output_dir,
        profile_version=_SYSTEM_PROFILE,
        entry_indices=[contract.source_entry_index],
    )
    generated = _load_json_object(
        output_dir / "fingerprint.json"
    )
    frozen = _load_json_object(
        layout.repo_root
        / "tests"
        / "contract_packs"
        / "oa"
        / _SYSTEM_PROFILE
        / "fingerprint.json"
    )
    report = compare_structural_fingerprints(frozen, generated)
    nodes = generated.get("nodes")
    node_count = len(nodes) if isinstance(nodes, list) else -1

    replay_environment = dict(environment)
    replay_environment.update(
        {
            "OA_READ_ADAPTER_MODE": "replay",
            "OA_READ_CONTRACT_PACK_DIR": (
                "tests/contract_packs/oa/ecology9-pending-workflows-v1"
            ),
            "PHASE0_MOCK_MODE": "false",
        }
    )
    replay_ok = _check_replay_composition(layout.repo_root, replay_environment)
    return RehearsalResult(
        node_count=node_count,
        added_count=len(report.added),
        removed_count=len(report.removed),
        changed_count=len(report.changed),
        sha_matches=report.expected_sha256 == report.actual_sha256,
        replay_composition_ok=replay_ok,
        drift=report,
    )


def _check_replay_composition(
    repo_root: Path,
    environment: dict[str, str],
) -> bool:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", "import app.main"],
            cwd=repo_root,
            env=environment,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _command_start(layout: Layout) -> int:
    environment = load_runtime_environment(
        base_env_path=layout.base_env,
        smoke_env_path=layout.smoke_env,
    )
    settings = _validate_settings(environment)
    candidate_sha256 = _candidate_fingerprint(layout, environment)
    backend: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None
    completed = False
    try:
        backend = _start_backend(layout, environment, candidate_sha256)
        backend_ok, backend_failure = _wait_for_backend(
            backend,
            log_path=layout.scratch / _BACKEND_LOG_NAME,
        )
        if not backend_ok:
            print(f"backend_start_failed={backend_failure}")
            _print_stop_instruction()
            return 1
        if not _run_capability_registry_preflight(settings):
            _print_stop_instruction()
            return 1
        _write_process_state(
            layout,
            backend,
            None,
            environment,
            candidate_sha256,
        )
        frontend = _start_frontend(layout, environment, candidate_sha256)
        if not _wait_for_frontend(frontend):
            print("frontend_start_failed=process_or_timeout")
            _print_stop_instruction()
            return 1
        _write_process_state(
            layout,
            backend,
            frontend,
            environment,
            candidate_sha256,
        )
        print(f"backend_url={_BACKEND_URL}")
        print(f"frontend_url={_FRONTEND_URL}")
        print(f"chat_url={_FRONTEND_URL}/chat")
        if not _cold_login_preflight(
            settings.oa_base_url,
            backend_log_path=layout.scratch / _BACKEND_LOG_NAME,
        ):
            _print_stop_instruction()
            return 1
        print("cold_login_preflight=passed")
        print(
            "next_step=命令行里的登录只是在检查 OA；浏览器登录是另外一回事。"
            "现在打开上面打印的 /chat 地址；如果看到登录页，就在浏览器登录。"
            "登录后只查询一次，完成后回到命令行运行 .\\smoke.ps1 verify。"
        )
        completed = True
        return 0
    finally:
        if not completed:
            _cleanup_failed_start(layout, backend, frontend, environment)


def _start_backend(
    layout: Layout,
    environment: dict[str, str],
    candidate_sha256: str,
) -> subprocess.Popen[bytes] | None:
    if _backend_health()[0]:
        ownership = _owned_service_status(
            layout,
            "backend_pid",
            candidate_sha256,
        )
        if ownership == "reusable":
            return None
        if ownership != "stale_owned":
            raise SmokeError("backend_already_running_not_owned")
        _restart_stale_owned_service(
            layout,
            "backend_pid",
            environment,
            candidate_sha256,
            ready=lambda: _backend_health()[0],
        )
    elif _local_port_in_use(8000):
        raise SmokeError("backend_port_in_use_unhealthy")
    return _spawn_service(
        ["uv", "run", "python", "-m", "app.server"],
        cwd=layout.repo_root,
        environment=environment,
        log_path=layout.scratch / _BACKEND_LOG_NAME,
    )


def _start_frontend(
    layout: Layout,
    environment: dict[str, str],
    candidate_sha256: str,
) -> subprocess.Popen[bytes] | None:
    if _frontend_ready():
        ownership = _owned_service_status(
            layout,
            "frontend_pid",
            candidate_sha256,
        )
        if ownership == "reusable":
            return None
        if ownership != "stale_owned":
            raise SmokeError("frontend_already_running_not_owned")
        _restart_stale_owned_service(
            layout,
            "frontend_pid",
            environment,
            candidate_sha256,
            ready=_frontend_ready,
        )
    elif _local_port_in_use(5173):
        raise SmokeError("frontend_port_in_use_unhealthy")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise SmokeError("npm_not_found")
    return _spawn_service(
        [npm, "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=layout.repo_root / "web",
        environment=environment,
        log_path=layout.scratch / "smoke_frontend.log",
    )


def _local_port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _spawn_service(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_path: Path,
) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        with log_path.open("wb") as log:
            return subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
    except OSError:
        raise SmokeError("service_start_failed") from None


def _wait_for_backend(
    process: subprocess.Popen[bytes] | None,
    *,
    log_path: Path,
) -> tuple[bool, str | None]:
    deadline = time.monotonic() + 90
    failed_checks: tuple[str, ...] = ()
    while time.monotonic() < deadline:
        ok, failed_checks = _backend_health()
        if ok:
            return True, None
        if process is not None and process.poll() is not None:
            return False, _classify_backend_failure(
                failed_checks=failed_checks,
                process_exited=True,
                log_path=log_path,
            )
        time.sleep(1)
    return False, _classify_backend_failure(
        failed_checks=failed_checks,
        process_exited=False,
        log_path=log_path,
    )


def _backend_health() -> tuple[bool, tuple[str, ...]]:
    payload: Any = None
    status_code: int | None = None
    try:
        with _LOCAL_OPENER.open(
            f"{_BACKEND_URL}/api/v1/health",
            timeout=_BACKEND_HEALTH_HTTP_TIMEOUT_SECONDS,
        ) as response:
            status_code = response.getcode()
            payload = _read_backend_health_payload(response)
    except HTTPError as exc:
        if exc.code != 503:
            return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)
        status_code = exc.code
        payload = _read_backend_health_payload(exc)
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return False, ()
        return False, (_BACKEND_HEALTH_CONNECTION_FAILED,)
    except (TimeoutError, socket.timeout):
        return False, ()
    except OSError:
        return False, (_BACKEND_HEALTH_CONNECTION_FAILED,)
    if not isinstance(payload, dict):
        return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)
    health_status = payload.get("status")
    if health_status not in {"ok", "unhealthy"}:
        return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)
    if not set(_BACKEND_HEALTH_COMPONENTS).issubset(checks):
        return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or value not in _BACKEND_HEALTH_COMPONENT_STATES
        for key, value in checks.items()
    ):
        return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)
    if any(
        checks[component] == "failed"
        for component in checks.keys() - set(_BACKEND_HEALTH_COMPONENTS)
    ):
        return False, (_BACKEND_HEALTH_COMPONENT_FAILED,)
    failed = tuple(
        component
        for component in _BACKEND_HEALTH_COMPONENTS
        if checks[component] == "failed"
    )
    if status_code == 200 and health_status == "ok" and not failed:
        return True, ()
    if status_code == 503 and health_status == "unhealthy" and failed:
        return False, failed
    return False, (_BACKEND_HEALTH_RESPONSE_INVALID,)


def _read_backend_health_payload(response: Any) -> Any:
    try:
        return json.loads(response.read(64 * 1024).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _wait_for_frontend(process: subprocess.Popen[bytes] | None) -> bool:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _frontend_ready():
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(1)
    return False


def _frontend_ready() -> bool:
    try:
        with _LOCAL_OPENER.open(_FRONTEND_URL, timeout=3) as response:
            return 200 <= int(response.getcode()) < 400
    except (OSError, URLError):
        return False


def _cold_login_preflight(
    oa_base_url: str,
    *,
    backend_log_path: Path,
) -> bool:
    reachable = _oa_endpoint_reachable(oa_base_url)
    print(f"oa_reachability={_bool(reachable)}")
    if not reachable:
        print("cold_login_preflight=connection_failed")
        return False
    account, password = _prompt_credentials()
    authentication_log_offset = _file_size(backend_log_path)
    raw_body = b""
    try:
        raw_body = json.dumps(
            {"loginid": account, "userpassword": password},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{_BACKEND_URL}/api/v1/auth/login",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "Origin": _LOGIN_ORIGIN,
                "X-EternalAI-CSRF": "1",
            },
            method="POST",
        )
        with _LOCAL_OPENER.open(request, timeout=40) as response:
            result = json.loads(response.read(64 * 1024).decode("utf-8"))
        if not isinstance(result, dict) or result.get("authenticated") is not True:
            _print_authentication_failure(
                _latest_authentication_failure_stage(
                    backend_log_path,
                    start_offset=authentication_log_offset,
                ),
                result_name="cold_login_preflight",
                diagnostics=_latest_authentication_failure_diagnostics(
                    backend_log_path,
                    start_offset=authentication_log_offset,
                ),
            )
            return False
        return True
    except HTTPError as exc:
        if exc.code == 403:
            print("cold_login_preflight=local_csrf_failed")
        elif exc.code in {400, 401, 422}:
            _print_authentication_failure(
                _latest_authentication_failure_stage(
                    backend_log_path,
                    start_offset=authentication_log_offset,
                ),
                result_name="cold_login_preflight",
                diagnostics=_latest_authentication_failure_diagnostics(
                    backend_log_path,
                    start_offset=authentication_log_offset,
                ),
            )
        else:
            print("cold_login_preflight=local_backend_failed")
        return False
    except (OSError, URLError, UnicodeError, json.JSONDecodeError):
        print("cold_login_preflight=connection_failed")
        return False
    finally:
        account = ""
        password = ""
        raw_body = b""


def _write_process_state(
    layout: Layout,
    backend: subprocess.Popen[bytes] | None,
    frontend: subprocess.Popen[bytes] | None,
    environment: dict[str, str],
    candidate_sha256: str,
) -> None:
    existing = _read_process_state(layout)
    backend_pid = backend.pid if backend is not None else existing.get("backend_pid")
    frontend_pid = (
        frontend.pid if frontend is not None else existing.get("frontend_pid")
    )
    state = {
        "state_version": _PROCESS_STATE_VERSION,
        "backend_pid": backend_pid,
        "backend_identity_sha256": (
            _required_process_identity(backend.pid)
            if backend is not None
            else existing.get("backend_identity_sha256")
        ),
        "backend_candidate_sha256": (
            candidate_sha256
            if backend is not None
            else existing.get("backend_candidate_sha256")
        ),
        "frontend_pid": frontend_pid,
        "frontend_identity_sha256": (
            _required_process_identity(frontend.pid)
            if frontend is not None
            else existing.get("frontend_identity_sha256")
        ),
        "frontend_candidate_sha256": (
            candidate_sha256
            if frontend is not None
            else existing.get("frontend_candidate_sha256")
        ),
        "configuration_sha256": _configuration_fingerprint(environment),
    }
    _write_process_state_payload(layout, state)


def _cleanup_failed_start(
    layout: Layout,
    backend: subprocess.Popen[bytes] | None,
    frontend: subprocess.Popen[bytes] | None,
    environment: dict[str, str],
) -> None:
    terminated: dict[int, bool] = {}
    for process in (frontend, backend):
        if process is not None:
            terminated[process.pid] = _terminate_new_process(process)
    state = _read_process_state(layout)
    if (
        backend is not None
        and terminated.get(backend.pid) is True
        and state.get("backend_pid") == backend.pid
    ):
        state["backend_pid"] = None
        state["backend_identity_sha256"] = None
        state["backend_candidate_sha256"] = None
    if (
        frontend is not None
        and terminated.get(frontend.pid) is True
        and state.get("frontend_pid") == frontend.pid
    ):
        state["frontend_pid"] = None
        state["frontend_identity_sha256"] = None
        state["frontend_candidate_sha256"] = None
    state["state_version"] = _PROCESS_STATE_VERSION
    state["configuration_sha256"] = _configuration_fingerprint(environment)
    _write_process_state_payload(layout, state)
    if not all(terminated.values()):
        raise SmokeError("start_cleanup_failed")


def _terminate_new_process(process: subprocess.Popen[bytes]) -> bool:
    if process.poll() is not None:
        return True
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            process.wait(timeout=5)
        else:
            process.terminate()
            process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=5)
        except OSError:
            return False
        except subprocess.TimeoutExpired:
            return False
    return process.poll() is not None


def _write_process_state_payload(layout: Layout, state: dict[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        layout.scratch.mkdir(parents=True, exist_ok=True)
        state_path = layout.scratch / "smoke_processes.json"
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=".smoke_processes.",
            suffix=".tmp",
            dir=layout.scratch,
            encoding="utf-8",
            newline="\n",
            delete=False,
        ) as writer:
            temporary_path = Path(writer.name)
            writer.write(json.dumps(state, indent=2) + "\n")
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary_path, state_path)
        temporary_path = None
    except OSError:
        raise SmokeError("process_state_write_failed") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _owned_service_status(
    layout: Layout,
    pid_key: str,
    candidate_sha256: str,
) -> str:
    state = _read_process_state(layout)
    pid = state.get(pid_key)
    identity_key = _identity_key(pid_key)
    candidate_key = _candidate_key(pid_key)
    expected_identity = state.get(identity_key)
    if (
        state.get("state_version") != _PROCESS_STATE_VERSION
        or not isinstance(pid, int)
        or pid <= 0
        or not isinstance(expected_identity, str)
        or len(expected_identity) != 64
    ):
        return "unowned"
    current_identity = _process_identity(pid)
    if current_identity is None or current_identity != expected_identity:
        return "unowned"
    if state.get(candidate_key) == candidate_sha256:
        return "reusable"
    return "stale_owned"


def _restart_stale_owned_service(
    layout: Layout,
    pid_key: str,
    environment: dict[str, str],
    candidate_sha256: str,
    *,
    ready: Callable[[], bool],
) -> None:
    state = _read_process_state(layout)
    pid = state.get(pid_key)
    identity_key = _identity_key(pid_key)
    candidate_key = _candidate_key(pid_key)
    expected_identity = state.get(identity_key)
    if (
        _owned_service_status(layout, pid_key, candidate_sha256) != "stale_owned"
        or not isinstance(pid, int)
        or not isinstance(expected_identity, str)
    ):
        raise SmokeError("service_restart_ownership_lost")
    if not _terminate_owned_pid(pid, expected_identity):
        raise SmokeError("service_restart_failed")
    state[pid_key] = None
    state[identity_key] = None
    state[candidate_key] = None
    state["state_version"] = _PROCESS_STATE_VERSION
    state["configuration_sha256"] = _configuration_fingerprint(environment)
    _write_process_state_payload(layout, state)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not ready():
            return
        time.sleep(0.2)
    raise SmokeError("service_restart_port_still_in_use")


def _identity_key(pid_key: str) -> str:
    if pid_key == "backend_pid":
        return "backend_identity_sha256"
    if pid_key == "frontend_pid":
        return "frontend_identity_sha256"
    raise SmokeError("process_state_invalid")


def _candidate_key(pid_key: str) -> str:
    if pid_key == "backend_pid":
        return "backend_candidate_sha256"
    if pid_key == "frontend_pid":
        return "frontend_candidate_sha256"
    raise SmokeError("process_state_invalid")


def _read_process_state(layout: Layout) -> dict[str, Any]:
    try:
        payload = json.loads(
            (layout.scratch / "smoke_processes.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _required_process_identity(pid: int) -> str:
    identity = _process_identity(pid)
    if identity is None:
        raise SmokeError("process_identity_unavailable")
    return identity


def _process_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_identity(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        closing = stat.rfind(")")
        if closing < 0:
            return None
        fields = stat[closing + 2 :].split()
        start_time = fields[19]
    except (OSError, UnicodeError, IndexError):
        return None
    return hashlib.sha256(f"{pid}:{start_time}".encode("ascii")).hexdigest()


def _windows_process_identity(pid: int) -> str | None:
    class _FileTime(ctypes.Structure):
        _fields_ = (
            ("low", ctypes.c_ulong),
            ("high", ctypes.c_ulong),
        )

    process_query_limited_information = 0x1000
    handle: Any = None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        )
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
        )
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return None
        creation = _FileTime()
        exit_time = _FileTime()
        kernel_time = _FileTime()
        user_time = _FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        creation_value = (creation.high << 32) | creation.low
        return hashlib.sha256(
            f"{pid}:{creation_value}".encode("ascii")
        ).hexdigest()
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if handle:
            try:
                kernel32.CloseHandle(handle)
            except (AttributeError, OSError):
                pass


def _terminate_owned_pid(pid: int, expected_identity: str) -> bool:
    if _process_identity(pid) != expected_identity:
        return False
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if completed.returncode != 0 and _process_identity(pid) == expected_identity:
                return False
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.TimeoutExpired):
        return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current_identity = _process_identity(pid)
        if current_identity is None or current_identity != expected_identity:
            return True
        time.sleep(0.1)
    return False


def _configuration_fingerprint(environment: dict[str, str]) -> str:
    names = (
        "ENV",
        "API_HOST",
        "API_PORT",
        "DATABASE_URL",
        "REDIS_URL",
        "OA_BASE_URL",
        "OA_TIMEOUT_S",
        "OA_CREDENTIAL_TTL_S",
        "SESSION_COOKIE_TTL_S",
        "CSRF_ALLOWED_ORIGINS",
        "OA_READ_ADAPTER_MODE",
        "OA_READ_CONTRACT_PACK_DIR",
        "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR",
        "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR",
        "OA_MESSAGE_CENTER_PATH",
        "OA_MESSAGE_CENTER_PAGE_SIZE",
        "OA_PENDING_WORKFLOWS_CATEGORY_ID",
        "OA_PENDING_WORKFLOWS_BIZSTATE",
        "OA_PENDING_WORKFLOWS_SELECT_STATE",
        "OA_SYSTEM_MESSAGES_CATEGORY_ID",
        "OA_SYSTEM_MESSAGES_BIZSTATE",
        "OA_SYSTEM_MESSAGES_SELECT_STATE",
        "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
        "ETERNALAI_IDENTITY_HMAC_KEY_B64",
        "ETERNALAI_SESSION_SIGNING_KEY_B64",
        "ETERNALAI_SESSION_BINDING_KEY_B64",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT_S",
        "LLM_MAX_TOKENS",
        "LLM_TEMPERATURE",
        "LLM_TOP_P",
        "LLM_TOP_K",
        "LLM_ENABLE_THINKING",
        "HEALTH_TIMEOUT_S",
        "PHASE0_MOCK_MODE",
        "ETERNALAI_BACKEND_URL",
    )
    encoded = json.dumps(
        [(name, environment.get(name, "")) for name in names],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_fingerprint(
    layout: Layout,
    environment: dict[str, str],
) -> str:
    configuration_sha256 = _configuration_fingerprint(environment)
    code_sha256 = _code_fingerprint(layout.repo_root)
    return hashlib.sha256(
        f"{configuration_sha256}:{code_sha256}".encode("ascii")
    ).hexdigest()


def _code_fingerprint(repo_root: Path) -> str:
    head = b""
    diff = b""
    untracked = b""
    try:
        head_result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            timeout=15,
        )
        diff_result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--binary", "HEAD", "--"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        untracked_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "app",
                "web",
                "scripts",
                "smoke.ps1",
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if (
            head_result.returncode != 0
            or diff_result.returncode != 0
            or untracked_result.returncode != 0
        ):
            raise SmokeError("candidate_fingerprint_failed")
        head = head_result.stdout.strip()
        diff = diff_result.stdout
        untracked = untracked_result.stdout
        if not head:
            raise SmokeError("candidate_fingerprint_failed")
        digest = hashlib.sha256()
        digest.update(head)
        digest.update(b"\x00")
        digest.update(diff)
        resolved_root = repo_root.resolve()
        for raw_path in sorted(item for item in untracked.split(b"\x00") if item):
            relative_path = raw_path.decode("utf-8")
            candidate_path = (repo_root / relative_path).resolve()
            if not candidate_path.is_relative_to(resolved_root):
                raise SmokeError("candidate_fingerprint_failed")
            digest.update(b"\x00")
            digest.update(raw_path)
            digest.update(b"\x00")
            digest.update(candidate_path.read_bytes())
        return digest.hexdigest()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise SmokeError("candidate_fingerprint_failed") from None
    finally:
        head = b""
        diff = b""
        untracked = b""


def _classify_backend_failure(
    *,
    failed_checks: tuple[str, ...],
    process_exited: bool,
    log_path: Path,
) -> str:
    if _BACKEND_HEALTH_RESPONSE_INVALID in failed_checks:
        return _BACKEND_HEALTH_RESPONSE_INVALID
    if _BACKEND_HEALTH_COMPONENT_FAILED in failed_checks:
        return _BACKEND_HEALTH_COMPONENT_FAILED
    if _BACKEND_HEALTH_CONNECTION_FAILED in failed_checks:
        return _BACKEND_HEALTH_CONNECTION_FAILED
    if "database" in failed_checks:
        if "redis" in failed_checks:
            return "database_and_redis_unreachable"
        return "database_unreachable"
    if "redis" in failed_checks:
        return "redis_unreachable"
    if "vllm" in failed_checks:
        return "vllm_unreachable"
    if process_exited and _backend_log_indicates_configuration_error(log_path):
        return "configuration_error"
    if process_exited:
        return "process_exited"
    return "health_timeout"


def _backend_log_indicates_configuration_error(log_path: Path) -> bool:
    try:
        with log_path.open("rb") as reader:
            reader.seek(0, os.SEEK_END)
            size = reader.tell()
            reader.seek(max(0, size - (1024 * 1024)))
            rendered = reader.read(1024 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return False
    try:
        return any(marker in rendered for marker in _CONFIGURATION_ERROR_MARKERS)
    finally:
        rendered = ""


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _latest_authentication_failure_stage(
    log_path: Path,
    *,
    start_offset: int = 0,
) -> str | None:
    try:
        with log_path.open("rb") as reader:
            reader.seek(0, os.SEEK_END)
            size = reader.tell()
            bounded_start = min(max(0, start_offset), size)
            reader.seek(max(bounded_start, size - (1024 * 1024)))
            rendered = reader.read(1024 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return None
    try:
        matches = _AUTH_FAILURE_STAGE_PATTERN.findall(rendered)
        if not matches:
            return None
        stage = matches[-1]
        return stage if stage in _AUTH_FAILURE_DETAILS else None
    finally:
        rendered = ""


def _latest_authentication_failure_diagnostics(
    log_path: Path,
    *,
    start_offset: int = 0,
) -> dict[str, str]:
    try:
        with log_path.open("rb") as reader:
            reader.seek(0, os.SEEK_END)
            size = reader.tell()
            bounded_start = min(max(0, start_offset), size)
            reader.seek(max(bounded_start, size - (1024 * 1024)))
            rendered = reader.read(1024 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return {}
    try:
        stage_matches = list(_AUTH_FAILURE_STAGE_PATTERN.finditer(rendered))
        if not stage_matches:
            return {}
        current_failure = rendered[stage_matches[-1].end() :]
        observed = dict(_AUTH_DIAGNOSTIC_PATTERN.findall(current_failure))
        return _safe_authentication_diagnostics(observed)
    finally:
        rendered = ""


def _safe_authentication_diagnostics(
    diagnostics: dict[str, str],
) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name in _AUTH_DIAGNOSTIC_ORDER:
        value = diagnostics.get(name)
        if value is None:
            continue
        if name.endswith("_present") and value in {"false", "true"}:
            safe[name] = value
        elif name.endswith("_type") and value in _AUTH_DIAGNOSTIC_TYPES:
            safe[name] = value
        elif (
            name.endswith("_count")
            and len(value) <= 7
            and value.isascii()
            and value.isdecimal()
        ):
            safe[name] = value
    return safe


def _print_authentication_failure(
    stage: str | None,
    *,
    result_name: str,
    diagnostics: dict[str, str] | None = None,
) -> None:
    if stage is None or stage not in _AUTH_FAILURE_DETAILS:
        print(f"{result_name}=authentication_failed")
        print("authentication_failure_detail=没有取得更细的安全失败阶段。")
        return
    print(f"{result_name}={stage}")
    print(f"authentication_failure_detail={_AUTH_FAILURE_DETAILS[stage]}")
    safe_diagnostics = _safe_authentication_diagnostics(diagnostics or {})
    for name in _AUTH_DIAGNOSTIC_ORDER:
        if name in safe_diagnostics:
            print(f"{name}={safe_diagnostics[name]}")


def _command_verify(
    layout: Layout,
    *,
    timestamp: str | None,
    har_directory: Path | None,
) -> int:
    environment = load_runtime_environment(
        base_env_path=layout.base_env,
        smoke_env_path=layout.smoke_env,
    )
    settings = _validate_settings(environment)
    if not _run_capability_registry_preflight(settings):
        _print_stop_instruction()
        return 1
    resolved_timestamp = _validated_timestamp(timestamp)
    system, pending = _run_live_checks_with_supported_loop(settings)
    capture_created = False
    if har_directory is not None:
        _build_optional_pending_v2(layout, har_directory, resolved_timestamp)
        capture_created = True
    report_path = layout.scratch / f"smoke_result_{resolved_timestamp}.md"
    report_text = _build_report(system, pending, capture_created=capture_created)
    _assert_report_safe(report_text, environment)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8", newline="\n")
    except OSError:
        raise SmokeError("report_write_failed") from None
    print(f"system_messages_drift={_drift_state(system)}")
    print(f"pending_workflows_drift={_drift_state(pending)}")
    _print_drift_nodes("system_messages", system.drift)
    _print_drift_nodes("pending_workflows", pending.drift)
    if system.protocol.http_status_code is not None:
        print(f"system_messages_http_status={system.protocol.http_status_code}")
    if pending.protocol.http_status_code is not None:
        print(f"pending_workflows_http_status={pending.protocol.http_status_code}")
    print(f"report={report_path.name}")
    if capture_created:
        print("pending_v2_capture=created")
    if _verify_success(system, pending):
        return 0
    _print_stop_instruction()
    return 1


def _print_drift_nodes(
    result_name: str,
    drift: OAStructuralDriftReport | None,
) -> None:
    if drift is None:
        print(f"{result_name}_drift_report=missing")
        return
    print(f"{result_name}_drift_added_count={len(drift.added)}")
    for index, node in enumerate(drift.added, start=1):
        _print_drift_node(
            f"{result_name}_drift_added_{index:03d}",
            _structural_node_payload(node),
        )
    print(f"{result_name}_drift_removed_count={len(drift.removed)}")
    for index, node in enumerate(drift.removed, start=1):
        _print_drift_node(
            f"{result_name}_drift_removed_{index:03d}",
            _structural_node_payload(node),
        )
    print(f"{result_name}_drift_changed_count={len(drift.changed)}")
    for index, (expected, actual) in enumerate(
        zip(drift.changed_expected, drift.changed, strict=True),
        start=1,
    ):
        _print_drift_node(
            f"{result_name}_drift_changed_{index:03d}",
            _changed_structural_node_payload(expected, actual),
        )


def _structural_node_payload(node: OAStructuralNode) -> dict[str, Any]:
    return {
        "path": node.path,
        "json_type": node.json_type,
        "nullable": node.nullable,
        "array_shape": node.array_shape,
    }


def _changed_structural_node_payload(
    expected: OAStructuralNode,
    actual: OAStructuralNode,
) -> dict[str, Any]:
    return {
        "expected": _structural_node_payload(expected),
        "actual": _structural_node_payload(actual),
    }


def _print_drift_node(name: str, node: dict[str, Any]) -> None:
    print(f"{name}=" + _render_drift_node(node))


def _render_drift_node(node: dict[str, Any]) -> str:
    return json.dumps(
        node,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _run_capability_registry_preflight(settings: ProductionSettings) -> bool:
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        result = runner.run(_inspect_capability_registry(settings))
    print(f"capability_registry_preflight={result.state}")
    print(
        "required_capabilities_expected="
        f"{len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)}"
    )
    print(f"required_capabilities_found={result.found_count}")
    print(f"required_capabilities_valid={result.valid_count}")
    print(f"unexpected_active_oa={result.unexpected_active_count}")
    print(f"active_capabilities_total={result.active_total_count}")
    print(f"intent_context_probes_expected={len(OA_CAPABILITY_CONTEXT_PROBES)}")
    print(f"intent_context_probes_visible={result.visible_probe_count}")
    print("intent_semantic_preflight=deterministic_context_only")
    return result.state == "passed"


async def _inspect_capability_registry(
    settings: ProductionSettings,
) -> CapabilityRegistryPreflight:
    engine = None
    try:
        engine = make_async_engine(settings.database_url)
        registry = PostgreSQLCapabilityRegistry(
            make_async_session_factory(engine)
        )
        catalog = tuple(await registry.list())
    except ValidationError:
        return _empty_capability_registry_preflight("registry_payload_invalid")
    except (
        DisconnectionError,
        OperationalError,
        SATimeoutError,
        ConnectionError,
        OSError,
        TimeoutError,
    ):
        return _empty_capability_registry_preflight("connection_failed")
    except DBAPIError:
        return _empty_capability_registry_preflight("registry_inspection_failed")
    except Exception:
        return _empty_capability_registry_preflight("registry_inspection_failed")
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass
    return _classify_capability_registry(catalog)


def _empty_capability_registry_preflight(state: str) -> CapabilityRegistryPreflight:
    return CapabilityRegistryPreflight(
        state=state,
        found_count=0,
        valid_count=0,
        unexpected_active_count=0,
        active_total_count=0,
        visible_probe_count=0,
    )


def _classify_capability_registry(
    catalog: tuple[CapabilitySpec, ...],
) -> CapabilityRegistryPreflight:
    expected = expected_oa_capabilities()
    by_id = {item.capability_id: item for item in catalog}
    found = tuple(
        by_id[capability_id]
        for capability_id in REQUIRED_ACTIVE_OA_CAPABILITY_IDS
        if capability_id in by_id
    )
    valid = tuple(
        item
        for expected_item in expected
        if (item := by_id.get(expected_item.capability_id)) == expected_item
    )
    active = tuple(item for item in catalog if item.status == "active")
    unexpected_active = tuple(
        item
        for item in active
        if item.target_system == "oa"
        and item.capability_id not in REQUIRED_ACTIVE_OA_CAPABILITY_IDS
    )
    visible_contract_ids = {
        capability_id
        for contract in BasicKnowledge().capability_input_contracts(active)
        if isinstance((capability_id := contract.get("capability_id")), str)
    }
    if len(OA_CAPABILITY_CONTEXT_PROBES) != len(
        REQUIRED_ACTIVE_OA_CAPABILITY_IDS
    ):
        raise RuntimeError(
            "OA capability probes and required IDs must be one-to-one"
        )
    probe_capability_pairs = tuple(
        zip(
            OA_CAPABILITY_CONTEXT_PROBES,
            REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
            strict=True,
        )
    )
    if not probe_capability_pairs:
        raise RuntimeError(
            "OA capability probe and required ID pairs must not be empty"
        )
    if any(
        not probe.strip() or not capability_id.strip()
        for probe, capability_id in probe_capability_pairs
    ):
        raise RuntimeError(
            "OA capability probes and required IDs must be non-empty"
        )
    if (
        len({probe for probe, _ in probe_capability_pairs})
        != len(probe_capability_pairs)
        or len({capability_id for _, capability_id in probe_capability_pairs})
        != len(probe_capability_pairs)
    ):
        raise RuntimeError(
            "OA capability probes and required IDs must be unique"
        )
    probe_contract_visibility = {
        probe: capability_id in visible_contract_ids
        for probe, capability_id in probe_capability_pairs
    }
    visible_probe_count = sum(probe_contract_visibility.values())
    if len(found) != len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS):
        state = "missing"
    elif any(item.status != "active" for item in found):
        state = "inactive"
    elif len(valid) != len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS):
        state = "contract_mismatch"
    elif unexpected_active:
        state = "unexpected_active"
    elif visible_probe_count != len(OA_CAPABILITY_CONTEXT_PROBES):
        state = "context_truncated"
    else:
        state = "passed"
    return CapabilityRegistryPreflight(
        state=state,
        found_count=len(found),
        valid_count=len(valid),
        unexpected_active_count=len(unexpected_active),
        active_total_count=len(active),
        visible_probe_count=visible_probe_count,
    )


def _run_live_checks_with_supported_loop(
    settings: ProductionSettings,
) -> tuple[LiveOutcome, LiveOutcome]:
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        return runner.run(_run_live_checks(settings))


async def _run_live_checks(
    settings: ProductionSettings,
) -> tuple[LiveOutcome, LiveOutcome]:
    reachable = _oa_endpoint_reachable(settings.oa_base_url)
    print(f"oa_reachability={_bool(reachable)}")
    if not reachable:
        raise SmokeError("oa_unreachable")
    account, password = _prompt_credentials()
    engine = make_async_engine(settings.database_url)
    session_factory = make_async_session_factory(engine)
    credential: OASessionCredential | None = None
    try:
        store = build_credential_store(
            session_factory=session_factory,
            encryption_key=settings.credential_encryption_key,
        )
        authentication = build_authentication_port(
            oa_base_url=settings.oa_base_url,
            oa_timeout_seconds=settings.oa_timeout_seconds,
            credential_store=store,
            role_reader=build_principal_role_reader(
                session_factory=session_factory
            ),
            identity_hmac_key=settings.identity_hmac_key,
            credential_ttl_seconds=settings.oa_credential_ttl_seconds,
        )
        try:
            principal = await authentication.authenticate(
                LoginCredential(
                    loginid=SecretStr(account),
                    userpassword=SecretStr(password),
                )
            )
        except OAAuthenticationError as exc:
            _print_authentication_failure(
                exc.stage,
                result_name="verify_login",
                diagnostics=exc.diagnostics,
            )
            raise SmokeError(exc.stage) from None
        except AuthenticationError:
            raise SmokeError("authentication_failed") from None
        credential = await store.load(principal.ai_user_id)
        if credential is None:
            raise SmokeError("oa_credential_not_persisted")
        return await _run_both_live_checks(settings, credential)
    finally:
        account = ""
        password = ""
        credential = None
        await engine.dispose()


async def _run_both_live_checks(
    settings: ProductionSettings,
    credential: OASessionCredential,
) -> tuple[LiveOutcome, LiveOutcome]:
    system = await _run_isolated_live_check(
        settings,
        credential,
        capability="system_messages",
    )
    pending = await _run_isolated_live_check(
        settings,
        credential,
        capability="pending_workflows",
    )
    return system, pending


async def _run_isolated_live_check(
    settings: ProductionSettings,
    credential: OASessionCredential,
    *,
    capability: str,
) -> LiveOutcome:
    try:
        return await _run_one_live_check(
            settings,
            credential,
            capability=capability,
        )
    except Exception:
        return LiveOutcome(
            drift=None,
            protocol=_empty_protocol_summary(),
            normalized=False,
            error_kind="unexpected_error",
        )


def _empty_protocol_summary() -> ProtocolSummary:
    return ProtocolSummary(
        request_count=0,
        response_count=0,
        record_count=0,
        terminal_empty_page=False,
        cursor_chain_matches=False,
        configured_form_matches=False,
        successful_envelopes=False,
        envelope_fields=(),
        record_field_types={},
    )


async def _run_one_live_check(
    settings: ProductionSettings,
    credential: OASessionCredential,
    *,
    capability: str,
) -> LiveOutcome:
    values = _required_live_values(settings)
    expected_form = (
        {
            "id": values["system_category"],
            "pagesize": str(settings.oa_message_center_page_size),
            "bizstate": values["system_bizstate"],
            "selectState": values["system_select_state"],
        }
        if capability == "system_messages"
        else {
            "id": values["pending_category"],
            "pagesize": str(settings.oa_message_center_page_size),
            "bizstate": values["pending_bizstate"],
            "selectState": values["pending_select_state"],
        }
    )
    evidence = ProtocolEvidence(expected_form=expected_form)
    reports: list[OAStructuralDriftReport] = []
    provider = LiveOAReadProvider(
        base_url=settings.oa_base_url,
        message_center_endpoint_path=values["path"],
        pending_workflows_category_id=values["pending_category"],
        pending_workflows_bizstate=values["pending_bizstate"],
        pending_workflows_select_state=values["pending_select_state"],
        system_messages_category_id=values["system_category"],
        system_messages_bizstate=values["system_bizstate"],
        system_messages_select_state=values["system_select_state"],
        timeout_seconds=settings.oa_timeout_seconds,
        pending_workflows_contract_pack_dir=values["pending_pack"],
        system_messages_contract_pack_dir=values["system_pack"],
        drift_reporter=reports.append,
        page_size=settings.oa_message_center_page_size,
        opener_factory=lambda: RecordingOpener(evidence),
    )
    normalized = False
    error_kind: str | None = None
    try:
        if capability == "system_messages":
            await provider.list_system_messages(credential)
        else:
            await provider.list_pending_workflows(credential)
        normalized = True
    except OALivePayloadInvalid:
        error_kind = "normalization_or_structure_drift" if reports else "structure_error"
    except (OALiveIdentityUnbound, OALiveIdentityExpired):
        error_kind = "authentication_failed"
    except OALivePermissionDenied:
        error_kind = "permission_denied"
    except OALiveTimeout:
        error_kind = "connection_timeout"
    except OALiveHTTPServerError:
        error_kind = "upstream_server_error"
    except (OALiveRequestError, OALiveProviderError):
        error_kind = "connection_failed"
    finally:
        evidence.clear_transient_state()
    if len(reports) > 1:
        raise SmokeError("multiple_drift_reports")
    return LiveOutcome(
        drift=reports[0] if reports else None,
        protocol=evidence.summary(),
        normalized=normalized,
        error_kind=error_kind,
    )


def _required_live_values(settings: ProductionSettings) -> dict[str, Any]:
    values: dict[str, Any] = {
        "path": settings.oa_message_center_path,
        "pending_category": settings.oa_pending_workflows_category_id,
        "pending_bizstate": settings.oa_pending_workflows_bizstate,
        "pending_select_state": settings.oa_pending_workflows_select_state,
        "system_category": settings.oa_system_messages_category_id,
        "system_bizstate": settings.oa_system_messages_bizstate,
        "system_select_state": settings.oa_system_messages_select_state,
        "pending_pack": settings.oa_pending_workflows_contract_pack_dir,
        "system_pack": settings.oa_system_messages_contract_pack_dir,
    }
    if any(value is None for value in values.values()):
        raise SmokeError("live_settings_incomplete")
    return values


def _prompt_credentials() -> tuple[str, str]:
    try:
        account = getpass.getpass("请输入 OA 账号（输入内容不会显示）：")
        password = getpass.getpass("请输入 OA 密码（输入内容不会显示）：")
    except (EOFError, OSError):
        raise SmokeError("credential_input_unavailable") from None
    if not account or not password:
        account = ""
        password = ""
        raise SmokeError("credential_input_empty")
    print(f"account_input_characters={len(account)}")
    print(f"password_input_characters={len(password)}")
    print(f"account_has_outer_whitespace={_bool(account != account.strip())}")
    print(f"password_has_outer_whitespace={_bool(password != password.strip())}")
    return account, password


def _oa_endpoint_reachable(base_url: str) -> bool:
    """Check only OA host/port reachability without sending credentials or HTTP."""

    try:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((parsed.hostname, port), timeout=5):
            return True
    except (OSError, ValueError):
        return False


def _build_optional_pending_v2(
    layout: Layout,
    har_directory: Path,
    timestamp: str,
) -> None:
    try:
        candidates = sorted(
            path for path in har_directory.iterdir() if path.is_file() and path.suffix == ".har"
        )
    except OSError:
        raise SmokeError("fallback_har_directory_unreadable") from None
    if len(candidates) != 1:
        raise SmokeError("fallback_har_not_unique")
    output_parent = layout.scratch / "smoke_capture" / timestamp
    try:
        output_parent.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise SmokeError("fallback_output_already_exists") from None
    except OSError:
        raise SmokeError("fallback_output_create_failed") from None
    sanitizer.sanitize_har_to_contract_pack(
        input_har=candidates[0],
        output_dir=output_parent / _PENDING_V2_PROFILE,
        profile_version=_PENDING_V2_PROFILE,
    )


def _build_report(
    system: LiveOutcome,
    pending: LiveOutcome,
    *,
    capture_created: bool,
) -> str:
    structures_match, added, removed, changed = compare_record_structures(
        system.protocol,
        pending.protocol,
    )
    safe_added = _safe_protocol_field_names(added)
    safe_removed = _safe_protocol_field_names(removed)
    safe_changed = _safe_protocol_field_names(changed)
    lines = [
        "# P2-SMOKE-RUNNER-001 现场结构报告",
        "",
        "## 结论",
        "",
        f"- 系统消息结构漂移：{_drift_state(system)}",
        f"- 待办结构漂移：{_drift_state(pending)}（预期允许大面积漂移）",
        f"- 两类记录结构一致：{_yes_no(structures_match)}",
        f"- 现场 v2 脱敏包：{'已生成' if capture_created else '未请求'}",
        "",
        "## 系统消息 Live 指纹",
        "",
        *_outcome_markdown(system),
        "",
        "## 待办 Live 指纹",
        "",
        *_outcome_markdown(pending),
        "",
        "## 五项现场确认",
        "",
        (
            "1. 游标续拉与终止：系统消息 "
            f"{_pagination_sentence(system.protocol)}；待办 "
            f"{_pagination_sentence(pending.protocol)}。"
        ),
        (
            "2. 系统消息真实类别：配置的系统消息类别请求 "
            f"{_accepted_sentence(system.protocol)}。"
        ),
        (
            "3. bizstate/selectState：两字段均按配置随请求发送；系统消息 "
            f"{_accepted_sentence(system.protocol)}，待办 "
            f"{_accepted_sentence(pending.protocol)}。报告不从单次结果臆测业务标签。"
        ),
        (
            "4. 待办与系统消息记录结构："
            f"{'一致' if structures_match else '不一致'}；"
            f"待办新增字段={_field_list(safe_added)}；"
            f"待办缺失字段={_field_list(safe_removed)}；"
            f"类型变化字段={_field_list(safe_changed)}。"
        ),
        (
            "5. 消息量级：系统消息 "
            f"{system.protocol.record_count} 条/{system.protocol.response_count} 页响应；"
            f"待办 {pending.protocol.record_count} 条/{pending.protocol.response_count} 页响应。"
        ),
        "",
        "## 给雨爷的现场操作",
        "",
        (
            "1. 到内网后，在项目目录运行 `./smoke.ps1 start`。按提示输入 OA 账号和"
            "密码；输入时屏幕不会显示内容，这是正常的。"
        ),
        (
            "2. 命令行里的登录只是在检查 OA；浏览器登录是另外一回事。"
            "`start` 成功后，打开上面打印的 `/chat` 地址；如果看到登录页，"
            "就在浏览器登录。登录后只查询一次。"
        ),
        (
            "3. 完成后回到命令行运行 `./smoke.ps1 verify`；"
            "再次输入时屏幕同样不会显示内容。"
        ),
        (
            "4. 任一命令失败就马上停止；保留屏幕上的错误和已经生成的报告，"
            "不要自行改文件，也不要切换运行模式。"
        ),
    ]
    return "\n".join(lines) + "\n"


def _outcome_markdown(outcome: LiveOutcome) -> list[str]:
    drift = outcome.drift
    lines = [
        f"- 请求页数：{outcome.protocol.request_count}",
        f"- 响应页数：{outcome.protocol.response_count}",
        f"- 记录数：{outcome.protocol.record_count}",
        f"- 显式空页终止：{_yes_no(outcome.protocol.terminal_empty_page)}",
        f"- 游标续拉一致：{_yes_no(outcome.protocol.cursor_chain_matches)}",
        f"- 配置表单一致：{_yes_no(outcome.protocol.configured_form_matches)}",
        f"- 归一化完成：{_yes_no(outcome.normalized)}",
        f"- 失败分类：{outcome.error_kind or '无'}",
        f"- HTTP 状态码：{outcome.protocol.http_status_code or '无'}",
        f"- 传输失败细分：{outcome.protocol.transport_failure_kind or '无'}",
    ]
    if drift is None:
        lines.extend(
            [
                "- 指纹报告：未形成",
                "- 新增结构节点数：0",
                "- 缺失结构节点数：0",
                "- 变化结构节点数：0",
            ]
        )
    else:
        lines.append(f"- 指纹匹配：{_yes_no(drift.matches)}")
        lines.extend(_drift_markdown(drift))
    lines.append(
        "- 响应字段名："
        + _field_list(
            _safe_protocol_field_names(outcome.protocol.envelope_fields)
        )
    )
    lines.append(
        "- 记录字段名："
        + _field_list(
            _safe_protocol_field_names(
                tuple(outcome.protocol.record_field_types)
            )
        )
    )
    return lines


def _drift_markdown(drift: OAStructuralDriftReport) -> list[str]:
    lines = [f"- 新增结构节点数：{len(drift.added)}"]
    lines.extend(
        f"- 新增结构节点 {index:03d}："
        + _render_drift_node(_structural_node_payload(node))
        for index, node in enumerate(drift.added, start=1)
    )
    lines.append(f"- 缺失结构节点数：{len(drift.removed)}")
    lines.extend(
        f"- 缺失结构节点 {index:03d}："
        + _render_drift_node(_structural_node_payload(node))
        for index, node in enumerate(drift.removed, start=1)
    )
    lines.append(f"- 变化结构节点数：{len(drift.changed)}")
    lines.extend(
        f"- 变化结构节点 {index:03d}："
        + _render_drift_node(
            _changed_structural_node_payload(expected, actual)
        )
        for index, (expected, actual) in enumerate(
            zip(drift.changed_expected, drift.changed, strict=True),
            start=1,
        )
    )
    return lines


def _safe_protocol_field_names(fields: tuple[str, ...]) -> tuple[str, ...]:
    unique_fields = set(fields)
    safe_fields = sorted(unique_fields & _SAFE_PROTOCOL_FIELD_NAMES)
    unknown_aliases = {
        "unknown_field_"
        + hashlib.sha256(
            ("protocol-field:" + field).encode("utf-8")
        ).hexdigest()
        for field in unique_fields - _SAFE_PROTOCOL_FIELD_NAMES
    }
    if len(unknown_aliases) != len(
        unique_fields - _SAFE_PROTOCOL_FIELD_NAMES
    ):
        raise SmokeError("anonymous_protocol_field_collision")
    safe_fields.extend(sorted(unknown_aliases))
    return tuple(safe_fields)


def _pagination_sentence(protocol: ProtocolSummary) -> str:
    if protocol.cursor_chain_matches and protocol.terminal_empty_page:
        return "后续请求沿用前页游标，且观察到显式空数据页终止"
    if not protocol.cursor_chain_matches:
        return "游标续拉未通过"
    return "游标续拉通过，但未观察到显式空数据页终止"


def _accepted_sentence(protocol: ProtocolSummary) -> str:
    accepted = (
        protocol.configured_form_matches
        and protocol.successful_envelopes
        and protocol.response_count > 0
    )
    return "已被服务端接受并形成结构化响应" if accepted else "未完成确认"


def _verify_success(system: LiveOutcome, pending: LiveOutcome) -> bool:
    system_ok = (
        system.drift is not None
        and system.drift.matches
        and system.normalized
        and system.error_kind is None
        and system.protocol.terminal_empty_page
        and system.protocol.cursor_chain_matches
    )
    pending_ok = (
        pending.drift is not None
        and pending.drift.matches
        and pending.normalized
        and pending.protocol.terminal_empty_page
        and pending.protocol.cursor_chain_matches
        and pending.error_kind is None
    )
    return system_ok and pending_ok


def _assert_report_safe(text: str, environment: dict[str, str]) -> None:
    if re.search(r"https?://", text, flags=re.IGNORECASE):
        raise SmokeError("report_contains_url")
    if re.search(
        r"(?i)(?:cookie|token|password|sessionid)\s*[:=]\s*\S+",
        text,
    ):
        raise SmokeError("report_contains_sensitive_assignment")
    sensitive_names = (
        "OA_BASE_URL",
        "OA_MESSAGE_CENTER_PATH",
        "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64",
        "ETERNALAI_IDENTITY_HMAC_KEY_B64",
        "ETERNALAI_SESSION_SIGNING_KEY_B64",
        "ETERNALAI_SESSION_BINDING_KEY_B64",
        "DATABASE_URL",
        "REDIS_URL",
    )
    for name in sensitive_names:
        value = environment.get(name, "")
        if len(value) >= 9 and value in text:
            raise SmokeError("report_contains_environment_value")


def _validate_settings(environment: dict[str, str]) -> ProductionSettings:
    try:
        return ProductionSettings.from_environment(environment)
    except Exception:
        raise SmokeError("smoke_configuration_invalid") from None


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SmokeError("fingerprint_unreadable") from None
    if not isinstance(payload, dict):
        raise SmokeError("fingerprint_invalid")
    return payload


def _unique_run_root(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(10):
        candidate = parent / (
            datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            + f"_{os.getpid()}"
        )
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            continue
        except OSError:
            raise SmokeError("scratch_directory_create_failed") from None
    raise SmokeError("scratch_directory_collision")


def _validated_timestamp(value: str | None) -> str:
    resolved = datetime.now().strftime("%Y%m%d_%H%M%S") if value is None else value
    if _TIMESTAMP_PATTERN.fullmatch(resolved) is None:
        raise SmokeError("timestamp_invalid", exit_code=2)
    return resolved


def _field_list(fields: tuple[str, ...]) -> str:
    return "、".join(fields) if fields else "无"


def _drift_state(outcome: LiveOutcome) -> str:
    if not outcome.normalized or outcome.drift is None:
        return "normalization_failed"
    if outcome.drift.removed or outcome.drift.changed:
        return "removed_or_changed"
    if outcome.drift.added:
        return "added"
    return "none"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _passed(value: bool) -> str:
    return "passed" if value else "failed"


def _print_stop_instruction(*, file: Any = None) -> None:
    print(
        "请停止操作，保留屏幕上的错误和已经生成的报告；"
        "不要自行改文件，也不要切换运行模式。",
        file=file,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
