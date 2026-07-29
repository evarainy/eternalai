from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import scripts.check_dev_environment as dev_environment
from scripts.check_dev_environment import (
    CheckResult,
    _configure_console_encoding,
    check_database_reachability,
    check_event_loop_compatibility,
    resolve_database_configuration,
    run_preflight,
    start_full_tests_background,
)

TEST_DATABASE_URL = "postgresql+psycopg://127.0.0.1:15432/eternalai_test"


def test_console_encoding_is_reconfigured_to_utf8_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []

    class ReconfigurableStream:
        def reconfigure(self, **kwargs: str) -> None:
            calls.append(kwargs)

    monkeypatch.setattr("scripts.check_dev_environment.sys.stdout", ReconfigurableStream())
    monkeypatch.setattr("scripts.check_dev_environment.sys.stderr", ReconfigurableStream())

    _configure_console_encoding()

    assert calls == [
        {"encoding": "utf-8", "errors": "replace"},
        {"encoding": "utf-8", "errors": "replace"},
    ]


def test_database_configuration_reports_source_and_target_without_credentials() -> None:
    password_marker = f"synthetic-{uuid4().hex}"
    database_url = (
        "postgresql+psycopg://local-user:"
        f"{password_marker}@127.0.0.1:15432/eternalai_test"
    )
    resolution = resolve_database_configuration(
        environment={"DATABASE_URL": database_url},
        env_path=Path("unused"),
    )

    rendered = resolution.check.render()

    assert resolution.check.passed is True
    assert "source=进程环境" in rendered
    assert "target=127.0.0.1:15432" in rendered
    assert "local-user" not in rendered
    assert password_marker not in rendered
    assert database_url not in rendered


def test_database_failures_never_echo_connection_or_exception_secrets() -> None:
    connection_secret = "connection-secret-must-not-escape"
    exception_secret = "exception-secret-must-not-escape"
    resolution = resolve_database_configuration(
        environment={
            "DATABASE_URL": (
                "postgresql+psycopg://user:"
                f"{connection_secret}@127.0.0.1:15432/eternalai_test"
            )
        },
        env_path=Path("unused"),
    )

    def fail_query(_database_url: str) -> bool:
        raise RuntimeError(exception_secret)

    rendered = check_database_reachability(
        resolution,
        query_runner=fail_query,
    ).render()

    assert "target=127.0.0.1:15432" in rendered
    assert connection_secret not in rendered
    assert exception_secret not in rendered
    assert "postgresql" not in rendered


def test_missing_database_url_points_to_restart_when_user_variable_exists(
    tmp_path: Path,
) -> None:
    results = run_preflight(
        environment={},
        env_path=tmp_path / ".env",
        user_environment_has_url=True,
        query_runner=lambda _database_url: True,
        uv_executable="uv",
    )

    rendered = "\n".join(result.render() for result in results)

    assert results[0].passed is False
    assert results[1].passed is False
    assert "source=完全缺失" in rendered
    assert "重启 Codex/终端以继承" in rendered
    assert "未执行 SELECT 1" in rendered


def test_missing_database_url_explains_how_to_configure_fixed_test_target(
    tmp_path: Path,
) -> None:
    resolution = resolve_database_configuration(
        environment={},
        env_path=tmp_path / ".env",
        user_environment_has_url=False,
    )

    rendered = resolution.check.render()

    assert resolution.check.passed is False
    assert "127.0.0.1:15432" in rendered
    assert "用户级 DATABASE_URL" in rendered
    assert "重启 Codex/终端" in rendered


def test_repository_env_is_used_only_when_process_value_is_absent(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"DATABASE_URL='{TEST_DATABASE_URL}'\n",
        encoding="utf-8",
    )

    resolution = resolve_database_configuration(
        environment={},
        env_path=env_path,
    )

    assert resolution.check.passed is True
    assert "source=仓库根 .env" in resolution.check.render()
    assert resolution.database_url == TEST_DATABASE_URL


def test_database_reachability_requires_a_successful_query() -> None:
    resolution = resolve_database_configuration(
        environment={"DATABASE_URL": TEST_DATABASE_URL},
        env_path=Path("unused"),
    )
    received: list[str] = []

    def successful_query(database_url: str) -> bool:
        received.append(database_url)
        return True

    result = check_database_reachability(
        resolution,
        query_runner=successful_query,
    )

    assert result.passed is True
    assert "真实建连并执行 SELECT 1 成功" in result.detail
    assert received == [TEST_DATABASE_URL]


def test_event_loop_check_rejects_proactor_for_windows() -> None:
    default_loop = asyncio.new_event_loop()

    class IncompatibleLoop:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    incompatible_loop = IncompatibleLoop()
    try:
        result = check_event_loop_compatibility(
            default_loop_factory=lambda: default_loop,
            supported_loop_factory=lambda: incompatible_loop,  # type: ignore[arg-type,return-value]
            platform="win32",
        )
    finally:
        if not default_loop.is_closed():
            default_loop.close()
        if not incompatible_loop.closed:
            incompatible_loop.close()

    assert result.passed is False
    assert "psycopg 不兼容" in result.detail


def test_event_loop_check_accepts_repository_loop_factory() -> None:
    result = check_event_loop_compatibility()

    assert result.passed is True
    assert "app.event_loop.make_event_loop" in result.detail
    assert "psycopg 兼容 loop" in result.detail


def test_background_runner_writes_pollable_status_without_running_pytest(
    tmp_path: Path,
) -> None:
    launches: list[tuple[list[str], dict[str, Any]]] = []

    class FakeProcess:
        pid = 4321

    def fake_launcher(command: list[str], **kwargs: Any) -> FakeProcess:
        launches.append((command, kwargs))
        return FakeProcess()

    fixed_time = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)
    background_run = start_full_tests_background(
        repo_root=tmp_path,
        process_launcher=fake_launcher,
        now=lambda: fixed_time,
    )

    status = json.loads(background_run.status_path.read_text(encoding="utf-8"))

    assert background_run.pid == 4321
    assert background_run.log_path.parent == tmp_path / "_scratch"
    assert status["state"] == "running"
    assert status["pid"] == 4321
    assert status["command"] == ["uv", "run", "pytest", "-q"]
    assert len(launches) == 1
    assert "--_full-tests-worker" in launches[0][0]


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("db.invalid", 15432),
        ("localhost", 15432),
        ("127.0.0.1", 55432),
    ],
)
def test_unsafe_database_url_output_is_sanitized(host: str, port: int) -> None:
    password_marker = f"synthetic-{uuid4().hex}"
    unsafe_url = (
        f"postgresql+psycopg://user:{password_marker}@{host}:{port}/eternalai_test"
    )
    resolution = resolve_database_configuration(
        environment={"DATABASE_URL": unsafe_url},
        env_path=Path("unused"),
    )

    rendered = resolution.check.render()

    assert resolution.check.passed is False
    assert password_marker not in rendered
    assert unsafe_url not in rendered


def test_invalid_database_url_output_is_sanitized() -> None:
    invalid_marker = f"invalid-{uuid4().hex}"
    resolution = resolve_database_configuration(
        environment={"DATABASE_URL": invalid_marker},
        env_path=Path("unused"),
    )

    rendered = resolution.check.render()

    assert resolution.check.passed is False
    assert invalid_marker not in rendered


def test_main_returns_nonzero_when_any_diagnostic_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        dev_environment,
        "run_preflight",
        lambda: [CheckResult("synthetic check", False, "missing", "repair it")],
    )

    exit_code = dev_environment.main([])

    assert exit_code == 1
    assert "[FAIL] synthetic check" in capsys.readouterr().out
