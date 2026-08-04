"""CLI for the two-command OA intranet smoke workflow."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import SecretStr

from app.composition import (
    build_authentication_port,
    build_credential_store,
    build_principal_role_reader,
)
from app.config import ProductionSettings
from app.db.session import make_async_engine, make_async_session_factory
from app.infra.adapters.oa.contracts import (
    OAStructuralDriftReport,
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
from app.ports.auth import AuthenticationError, LoginCredential, OASessionCredential
from scripts import sanitize_oa_contract_pack as sanitizer
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
_FRONTEND_URL = "http://127.0.0.1:5173"
_LOGIN_ORIGIN = _FRONTEND_URL
_LOCAL_OPENER = build_opener(ProxyHandler({}))
_BACKEND_LOG_NAME = "smoke_backend.log"
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
_AUTH_FAILURE_DETAILS = {
    "oa_session_setup_failed": "无法创建 OA 登录会话。",
    "oa_rsa_request_failed": "无法取得 OA 登录所需的 RSA 参数。",
    "oa_rsa_response_invalid": "OA 返回的 RSA 参数结构不完整。",
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


@dataclass(frozen=True, slots=True)
class LiveOutcome:
    drift: OAStructuralDriftReport | None
    protocol: ProtocolSummary
    normalized: bool
    error_kind: str | None


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
    generated = _load_json_object(output_dir / "fingerprint.json")
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
    backend: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None
    completed = False
    try:
        backend = _start_backend(layout, environment)
        backend_ok, backend_failure = _wait_for_backend(
            backend,
            log_path=layout.scratch / _BACKEND_LOG_NAME,
        )
        if not backend_ok:
            print(f"backend_start_failed={backend_failure}")
            _print_stop_instruction()
            return 1
        _write_process_state(layout, backend, None, environment)
        frontend = _start_frontend(layout, environment)
        if not _wait_for_frontend(frontend):
            print("frontend_start_failed=process_or_timeout")
            _print_stop_instruction()
            return 1
        _write_process_state(layout, backend, frontend, environment)
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
            "next_step=只打开上面打印的 /chat 地址并试一次；"
            "完成后回到命令行运行 .\\smoke.ps1 verify。"
        )
        completed = True
        return 0
    finally:
        if not completed:
            _cleanup_failed_start(layout, backend, frontend, environment)


def _start_backend(
    layout: Layout,
    environment: dict[str, str],
) -> subprocess.Popen[bytes] | None:
    if _backend_health()[0]:
        if _owned_service_is_reusable(layout, "backend_pid", environment):
            return None
        raise SmokeError("backend_already_running_not_owned")
    return _spawn_service(
        ["uv", "run", "python", "-m", "app.server"],
        cwd=layout.repo_root,
        environment=environment,
        log_path=layout.scratch / _BACKEND_LOG_NAME,
    )


def _start_frontend(
    layout: Layout,
    environment: dict[str, str],
) -> subprocess.Popen[bytes] | None:
    if _frontend_ready():
        if _owned_service_is_reusable(layout, "frontend_pid", environment):
            return None
        raise SmokeError("frontend_already_running_not_owned")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise SmokeError("npm_not_found")
    return _spawn_service(
        [npm, "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=layout.repo_root / "web",
        environment=environment,
        log_path=layout.scratch / "smoke_frontend.log",
    )


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
    try:
        with _LOCAL_OPENER.open(
            f"{_BACKEND_URL}/api/v1/health", timeout=5
        ) as response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read(64 * 1024).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return False, ()
    except (OSError, URLError, UnicodeError, json.JSONDecodeError):
        return False, ()
    if not isinstance(payload, dict):
        return False, ()
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        return False, ()
    failed = tuple(
        sorted(
            key
            for key, value in checks.items()
            if isinstance(key, str) and value != "ok"
        )
    )
    return payload.get("status") == "ok" and not failed, failed


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
) -> None:
    existing = _read_process_state(layout)
    state = {
        "backend_pid": (
            backend.pid if backend is not None else existing.get("backend_pid")
        ),
        "frontend_pid": (
            frontend.pid if frontend is not None else existing.get("frontend_pid")
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
    if (
        frontend is not None
        and terminated.get(frontend.pid) is True
        and state.get("frontend_pid") == frontend.pid
    ):
        state["frontend_pid"] = None
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


def _owned_service_is_reusable(
    layout: Layout,
    pid_key: str,
    environment: dict[str, str],
) -> bool:
    state = _read_process_state(layout)
    pid = state.get(pid_key)
    return (
        isinstance(pid, int)
        and pid > 0
        and state.get("configuration_sha256")
        == _configuration_fingerprint(environment)
        and _process_exists(pid)
    )


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


def _configuration_fingerprint(environment: dict[str, str]) -> str:
    names = (
        "API_HOST",
        "API_PORT",
        "DATABASE_URL",
        "REDIS_URL",
        "OA_BASE_URL",
        "OA_READ_ADAPTER_MODE",
        "OA_MESSAGE_CENTER_PATH",
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
    )
    encoded = json.dumps(
        [(name, environment.get(name, "")) for name in names],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _classify_backend_failure(
    *,
    failed_checks: tuple[str, ...],
    process_exited: bool,
    log_path: Path,
) -> str:
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


def _print_authentication_failure(
    stage: str | None,
    *,
    result_name: str,
) -> None:
    if stage is None or stage not in _AUTH_FAILURE_DETAILS:
        print(f"{result_name}=authentication_failed")
        print("authentication_failure_detail=没有取得更细的安全失败阶段。")
        return
    print(f"{result_name}={stage}")
    print(f"authentication_failure_detail={_AUTH_FAILURE_DETAILS[stage]}")


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
    resolved_timestamp = _validated_timestamp(timestamp)
    system, pending = asyncio.run(_run_live_checks(settings))
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
    print(f"report={report_path.name}")
    if capture_created:
        print("pending_v2_capture=created")
    if _verify_success(system, pending):
        return 0
    _print_stop_instruction()
    return 1


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
            )
            raise SmokeError(exc.stage) from None
        except AuthenticationError:
            raise SmokeError("authentication_failed") from None
        credential = await store.load(principal.ai_user_id)
        if credential is None:
            raise SmokeError("oa_credential_not_persisted")
        system = await _run_one_live_check(
            settings,
            credential,
            capability="system_messages",
        )
        pending = await _run_one_live_check(
            settings,
            credential,
            capability="pending_workflows",
        )
        return system, pending
    finally:
        account = ""
        password = ""
        credential = None
        await engine.dispose()


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
            f"待办新增字段={_field_list(added)}；"
            f"待办缺失字段={_field_list(removed)}；"
            f"类型变化字段={_field_list(changed)}。"
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
        "2. `start` 成功后，只打开上面打印的 `/chat` 地址并试一次。",
        "3. 完成后回到命令行运行 `./smoke.ps1 verify`；再次输入时屏幕同样不会显示内容。",
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
    ]
    if drift is None:
        lines.extend(
            [
                "- 指纹报告：未形成",
                "- 新增字段路径：无",
                "- 缺失字段路径：无",
                "- 类型变化路径：无",
            ]
        )
    else:
        lines.extend(
            [
                f"- 指纹匹配：{_yes_no(drift.matches)}",
                f"- 新增字段路径：{_field_list(tuple(node.path for node in drift.added))}",
                f"- 缺失字段路径：{_field_list(tuple(node.path for node in drift.removed))}",
                f"- 类型变化路径：{_field_list(tuple(node.path for node in drift.changed))}",
            ]
        )
    lines.append(
        "- 响应字段名：" + _field_list(outcome.protocol.envelope_fields)
    )
    lines.append(
        "- 记录字段名："
        + _field_list(tuple(outcome.protocol.record_field_types))
    )
    return lines


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
        and pending.protocol.terminal_empty_page
        and pending.protocol.cursor_chain_matches
        and pending.error_kind
        in {None, "normalization_or_structure_drift"}
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
    if outcome.drift is None:
        return outcome.error_kind or "not_measured"
    return "none" if outcome.drift.matches else "detected"


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
