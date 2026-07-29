"""Manually diagnose the local test environment and start full tests in background."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import ArgumentError  # noqa: E402

from app.db.health import check_database_health  # noqa: E402
from app.event_loop import make_event_loop  # noqa: E402
from scripts.reset_test_db import validate_test_database_url  # noqa: E402

_DOTENV_ASSIGNMENT = re.compile(
    r"^(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)
_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9]+$")
_BACKGROUND_COMMAND = "uv run python scripts/check_dev_environment.py --start-full-tests"
_EXPECTED_HOST = "127.0.0.1"
_EXPECTED_PORT = 15432
_EXPECTED_TARGET = f"{_EXPECTED_HOST}:{_EXPECTED_PORT}"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    next_step: str | None = None

    def render(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        rendered = f"[{status}] {self.name}: {self.detail}"
        if self.next_step is not None:
            rendered += f"\n       下一步: {self.next_step}"
        return rendered


@dataclass(frozen=True, slots=True)
class DatabaseResolution:
    check: CheckResult
    source: str
    target: str
    _database_url: str | None = field(default=None, repr=False)

    @property
    def database_url(self) -> str | None:
        return self._database_url


@dataclass(frozen=True, slots=True)
class BackgroundRun:
    pid: int
    log_path: Path
    status_path: Path


def _repository_env_path(repo_root: Path = REPO_ROOT) -> Path:
    """Resolve the primary checkout .env from either a checkout or a worktree."""

    git_marker = repo_root / ".git"
    if git_marker.is_dir() or not git_marker.is_file():
        return repo_root / ".env"
    marker = git_marker.read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not marker.casefold().startswith(prefix):
        return repo_root / ".env"
    git_dir = Path(marker[len(prefix) :].strip())
    if not git_dir.is_absolute():
        git_dir = (repo_root / git_dir).resolve()
    common_dir_file = git_dir / "commondir"
    if not common_dir_file.is_file():
        return repo_root / ".env"
    common_git_dir = (
        git_dir / common_dir_file.read_text(encoding="utf-8").strip()
    ).resolve()
    return common_git_dir.parent / ".env"


def _read_database_url(path: Path) -> str | None:
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DOTENV_ASSIGNMENT.fullmatch(line)
        if match is None or match.group("name") != "DATABASE_URL":
            continue
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return None


def _user_environment_has_database_url() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "DATABASE_URL")
    except (FileNotFoundError, OSError):
        return False
    return isinstance(value, str) and bool(value.strip())


def resolve_database_configuration(
    *,
    environment: Mapping[str, str] | None = None,
    env_path: Path | None = None,
    user_environment_has_url: bool | None = None,
) -> DatabaseResolution:
    source_environment = os.environ if environment is None else environment
    resolved_env_path = _repository_env_path() if env_path is None else env_path
    if "DATABASE_URL" in source_environment:
        source = "进程环境"
        database_url = source_environment["DATABASE_URL"]
    else:
        source = "仓库根 .env"
        try:
            database_url = _read_database_url(resolved_env_path)
        except (OSError, UnicodeError):
            return DatabaseResolution(
                check=CheckResult(
                    "DATABASE_URL",
                    False,
                    "source=仓库根 .env; target=<unknown>; 文件无法读取",
                    "修复仓库根 .env 的读取权限后重跑；不要把连接串粘贴到终端输出。",
                ),
                source=source,
                target="<unknown>",
            )
        if database_url is None:
            source = "完全缺失"

    if database_url is None or not database_url.strip():
        inherited_late = (
            _user_environment_has_database_url()
            if user_environment_has_url is None
            else user_environment_has_url
        )
        if inherited_late:
            next_step = "用户级变量已存在；重启 Codex/终端以继承它，然后重跑本命令。"
        else:
            next_step = (
                "把用户级 DATABASE_URL 配到固定测试库 127.0.0.1:15432；"
                "若刚设置过，重启 Codex/终端后重跑。"
            )
        return DatabaseResolution(
            check=CheckResult(
                "DATABASE_URL",
                False,
                f"source={source}; target=<missing>",
                next_step,
            ),
            source=source,
            target="<missing>",
        )

    try:
        parsed = make_url(database_url)
        host = parsed.host
        port = parsed.port
    except (ArgumentError, TypeError, ValueError):
        return DatabaseResolution(
            check=CheckResult(
                "DATABASE_URL",
                False,
                f"source={source}; target=<invalid>; 连接信息无法解析",
                "把 DATABASE_URL 改为固定 PostgreSQL 测试库 127.0.0.1:15432，然后重跑。",
            ),
            source=source,
            target="<invalid>",
        )

    target = _format_target(host, port)
    try:
        validate_test_database_url(database_url)
    except ValueError:
        return DatabaseResolution(
            check=CheckResult(
                "DATABASE_URL",
                False,
                f"source={source}; target={target}; 不是固定本地测试库",
                f"把 DATABASE_URL 指向 {_EXPECTED_TARGET} 的 eternalai_test，然后重跑。",
            ),
            source=source,
            target=target,
        )
    if host != _EXPECTED_HOST or port != _EXPECTED_PORT:
        return DatabaseResolution(
            check=CheckResult(
                "DATABASE_URL",
                False,
                f"source={source}; target={target}; 不是固定本地测试目标",
                f"把 DATABASE_URL 指向 {_EXPECTED_TARGET} 的 eternalai_test，然后重跑。",
            ),
            source=source,
            target=target,
        )

    return DatabaseResolution(
        check=CheckResult(
            "DATABASE_URL",
            True,
            f"source={source}; target={target}; 固定测试库配置正确",
        ),
        source=source,
        target=target,
        _database_url=database_url,
    )


def _format_target(host: str | None, port: int | None) -> str:
    if host is None:
        return "<unknown>"
    safe_host = f"[{host}]" if ":" in host else host
    return f"{safe_host}:{port}" if port is not None else f"{safe_host}:<missing>"


def _run_database_query(database_url: str) -> bool:
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        return runner.run(check_database_health(database_url))


def check_database_reachability(
    resolution: DatabaseResolution,
    *,
    query_runner: Callable[[str], bool] = _run_database_query,
) -> CheckResult:
    if resolution.database_url is None:
        return CheckResult(
            "数据库可达性",
            False,
            f"target={resolution.target}; 未执行 SELECT 1",
            "先修复上面的 DATABASE_URL 配置，再重跑本命令。",
        )
    try:
        query_succeeded = query_runner(resolution.database_url)
    except Exception:
        query_succeeded = False
    if not query_succeeded:
        return CheckResult(
            "数据库可达性",
            False,
            f"target={resolution.target}; 真实建连或 SELECT 1 失败",
            (
                "启动 Docker Desktop，再运行 "
                "`docker compose -f infra/docker/docker-compose.test-db.yml up -d`；"
                "等待 PostgreSQL 可查询后重跑。"
            ),
        )
    return CheckResult(
        "数据库可达性",
        True,
        f"target={resolution.target}; 真实建连并执行 SELECT 1 成功",
    )


def check_event_loop_compatibility(
    *,
    default_loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
    supported_loop_factory: Callable[[], asyncio.AbstractEventLoop] = make_event_loop,
    platform: str = sys.platform,
) -> CheckResult:
    default_loop: asyncio.AbstractEventLoop | None = None
    supported_loop: asyncio.AbstractEventLoop | None = None
    try:
        default_loop = default_loop_factory()
        supported_loop = supported_loop_factory()
        default_name = type(default_loop).__name__
        supported_name = type(supported_loop).__name__
        if platform == "win32" and not isinstance(
            supported_loop,
            asyncio.SelectorEventLoop,
        ):
            return CheckResult(
                "Windows 事件循环",
                False,
                (
                    f"default={default_name}; "
                    f"app.event_loop.make_event_loop={supported_name}; psycopg 不兼容"
                ),
                "修复 app.event_loop.make_event_loop，使 Windows 使用 SelectorEventLoop。",
            )
        return CheckResult(
            "Windows 事件循环",
            True,
            (
                f"default={default_name}; "
                f"app.event_loop.make_event_loop={supported_name}; "
                "数据库诊断已使用 psycopg 兼容 loop"
            ),
        )
    except Exception:
        return CheckResult(
            "Windows 事件循环",
            False,
            "无法创建或验证事件循环",
            "检查 Python/asyncio 环境后重跑；不要改 app 运行时绕过。",
        )
    finally:
        if supported_loop is not None:
            supported_loop.close()
        if default_loop is not None:
            default_loop.close()


def check_background_test_runner(
    *,
    repo_root: Path = REPO_ROOT,
    uv_executable: str | None = None,
) -> CheckResult:
    resolved_uv = shutil.which("uv") if uv_executable is None else uv_executable
    if resolved_uv is None:
        return CheckResult(
            "后台全量测试",
            False,
            "当前进程找不到 uv，无法启动后台 pytest",
            "在能运行 `uv run pytest` 的终端中重跑本命令。",
        )
    if not os.access(repo_root, os.W_OK):
        return CheckResult(
            "后台全量测试",
            False,
            "仓库根不可写，无法把日志落到 _scratch/",
            "修复仓库目录写权限后重跑；不要改用仓库外的凭证日志。",
        )
    return CheckResult(
        "后台全量测试",
        True,
        f"可用；启动命令: {_BACKGROUND_COMMAND}; 日志与状态写入 _scratch/",
    )


def run_preflight(
    *,
    environment: Mapping[str, str] | None = None,
    env_path: Path | None = None,
    user_environment_has_url: bool | None = None,
    query_runner: Callable[[str], bool] = _run_database_query,
    uv_executable: str | None = None,
) -> list[CheckResult]:
    resolution = resolve_database_configuration(
        environment=environment,
        env_path=env_path,
        user_environment_has_url=user_environment_has_url,
    )
    return [
        resolution.check,
        check_database_reachability(resolution, query_runner=query_runner),
        check_event_loop_compatibility(),
        check_background_test_runner(uv_executable=uv_executable),
    ]


def _write_status(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _worker_paths(run_id: str, *, repo_root: Path = REPO_ROOT) -> tuple[Path, Path]:
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("Invalid background test run id")
    scratch = repo_root / "_scratch"
    return (
        scratch / f"full-pytest-{run_id}.log",
        scratch / f"full-pytest-{run_id}.status.json",
    )


def start_full_tests_background(
    *,
    repo_root: Path = REPO_ROOT,
    process_launcher: Callable[..., Any] = subprocess.Popen,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BackgroundRun:
    scratch = repo_root / "_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    run_id = f"{now().strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
    log_path, status_path = _worker_paths(run_id, repo_root=repo_root)
    _write_status(
        status_path,
        {
            "command": ["uv", "run", "pytest", "-q"],
            "log": str(log_path),
            "started_at": now().isoformat(),
            "state": "starting",
        },
    )

    process_kwargs: dict[str, Any] = {
        "cwd": repo_root,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        process_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        process_kwargs["start_new_session"] = True
    process = process_launcher(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--_full-tests-worker",
            run_id,
        ],
        **process_kwargs,
    )
    _write_status(
        status_path,
        {
            "command": ["uv", "run", "pytest", "-q"],
            "log": str(log_path),
            "pid": process.pid,
            "started_at": now().isoformat(),
            "state": "running",
        },
    )
    return BackgroundRun(
        pid=process.pid,
        log_path=log_path,
        status_path=status_path,
    )


def _run_full_tests_worker(run_id: str, *, repo_root: Path = REPO_ROOT) -> int:
    log_path, status_path = _worker_paths(run_id, repo_root=repo_root)
    started_at = datetime.now(UTC)
    exit_code = 1
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write("Starting: uv run pytest -q\n")
            log.flush()
            completed = subprocess.run(
                ["uv", "run", "pytest", "-q"],
                check=False,
                cwd=repo_root,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            exit_code = completed.returncode
            log.write(f"\nBackground pytest exit code: {exit_code}\n")
    except Exception:
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\nBackground pytest worker failed before completion.\n")
    finished_at = datetime.now(UTC)
    _write_status(
        status_path,
        {
            "command": ["uv", "run", "pytest", "-q"],
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
            "exit_code": exit_code,
            "finished_at": finished_at.isoformat(),
            "log": str(log_path),
            "state": "passed" if exit_code == 0 else "failed",
        },
    )
    return exit_code


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--start-full-tests",
        action="store_true",
        help="start `uv run pytest -q` detached and write log/status under _scratch/",
    )
    mode.add_argument("--_full-tests-worker", metavar="RUN_ID", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args._full_tests_worker is not None:
        try:
            return _run_full_tests_worker(args._full_tests_worker)
        except (OSError, ValueError):
            return 2

    if args.start_full_tests:
        try:
            background_run = start_full_tests_background()
        except OSError:
            print("[FAIL] 后台全量测试: 启动失败；未输出底层异常以避免泄露环境信息。")
            return 2
        print(f"[STARTED] 后台全量测试 pid={background_run.pid}")
        print(f"log: {background_run.log_path}")
        print(f"status: {background_run.status_path}")
        print(
            "轮询: "
            f"Get-Content -LiteralPath '{background_run.status_path}'"
        )
        print(
            "查看输出: "
            f"Get-Content -LiteralPath '{background_run.log_path}' -Tail 30"
        )
        return 0

    results = run_preflight()
    print("EternalAI 开发环境自检（仅诊断，不影响既有命令）")
    for result in results:
        print(result.render())
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
