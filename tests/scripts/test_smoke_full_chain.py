from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from app.ports.trace import TraceQueryPort
from scripts.smoke import full_chain as full_chain_module
from scripts.smoke import runner as smoke_runner
from scripts.smoke.capabilities import REQUIRED_ACTIVE_OA_CAPABILITY_IDS
from scripts.smoke.errors import SmokeError
from scripts.smoke.full_chain_contract import (
    FULL_CHAIN_SCHEMA_VERSION,
    CapabilityFullChainOutcome,
    FullChainFailure,
    FullChainOutcome,
)
from scripts.smoke.trace_contract import REQUIRED_TRACE_EVENTS

_FULL_CHAIN_FAILURE_SCHEMA_VERSION = "p2.smoke.full-chain.v2"


def _successful_outcome(
    capability_ids: tuple[str, ...] = REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
) -> FullChainOutcome:
    return FullChainOutcome(
        schema_version=FULL_CHAIN_SCHEMA_VERSION,
        required_trace_event_count=len(REQUIRED_TRACE_EVENTS),
        capabilities=tuple(
            CapabilityFullChainOutcome(
                capability_id=capability_id,
                successful_envelope=True,
                normalized_data=True,
                selected_capability=True,
                trace_events_complete=True,
                observed_trace_event_count=len(REQUIRED_TRACE_EVENTS),
            )
            for capability_id in capability_ids
        ),
    )


def _assert_safe_smoke_error(
    error: SmokeError,
    *sensitive_values: str,
) -> None:
    assert error.__context__ is None
    assert error.__cause__ is None
    traceback = error.__traceback__
    runner_frames = 0
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == smoke_runner.__name__:
            runner_frames += 1
            assert all(
                sensitive not in repr(value)
                for value in frame.f_locals.values()
                for sensitive in sensitive_values
            )
        traceback = traceback.tb_next
    assert runner_frames == 1


def test_full_chain_subprocess_accepts_only_strict_complete_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    account = "sensitive-account-canary"
    password = "sensitive-password-canary"
    observed: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_successful_outcome().model_dump_json().encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    outcome = smoke_runner._run_full_chain_subprocess(
        repo_root=tmp_path,
        environment={"PATH": "synthetic-path"},
        account=account,
        password=password,
    )

    assert outcome.passed() is True
    assert observed["command"] == [
        smoke_runner.sys.executable,
        "-m",
        "scripts.smoke.full_chain",
    ]
    assert account not in repr(observed["command"])
    assert password not in repr(observed["command"])
    assert account not in repr(observed["env"])
    assert password not in repr(observed["env"])
    assert observed["timeout"] == 300.0


def test_replay_full_chain_subprocess_scopes_one_allowlisted_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capability_id = "oa.list_system_messages"
    observed: dict[str, Any] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_successful_outcome((capability_id,))
            .model_dump_json()
            .encode("utf-8"),
            stderr=b"success-path-diagnostic",
        )

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    outcome = smoke_runner._run_full_chain_subprocess(
        repo_root=tmp_path,
        environment={"PATH": "synthetic-path"},
        account="replay-account",
        password="replay-password",
        expected_capability_ids=(capability_id,),
    )

    assert outcome.passed(expected_capability_ids=(capability_id,)) is True
    assert "full_chain_subprocess_stderr" not in capsys.readouterr().out
    assert observed["command"] == [
        smoke_runner.sys.executable,
        "-m",
        "scripts.smoke.full_chain",
        "--capability-id",
        capability_id,
    ]


def test_full_chain_subprocess_preserves_specific_child_failure_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    error_code = "authentication_failed"

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                '{"error_code":"authentication_failed",'
                f'"schema_version":"{_FULL_CHAIN_FAILURE_SCHEMA_VERSION}"}}'
            ).encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    with pytest.raises(SmokeError) as captured:
        smoke_runner._run_full_chain_subprocess(
            repo_root=tmp_path,
            environment={"PATH": "synthetic-path"},
            account="synthetic-account",
            password="synthetic-password",
        )

    assert captured.value.code == error_code


def test_full_chain_failure_schema_rejects_unregistered_error_code() -> None:
    with pytest.raises(ValidationError):
        FullChainFailure.model_validate(
            {
                "error_code": "dynamic_failure_detail",
                "schema_version": _FULL_CHAIN_FAILURE_SCHEMA_VERSION,
            },
            strict=True,
        )


def test_full_chain_subprocess_prints_bounded_sanitized_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    account = "stderr-account-canary"
    password = "stderr-password-canary"
    environment_secret = "stderr-environment-secret-canary"
    untracked_bearer = "stderr-untracked-bearer-canary"
    stderr_lines = [
        f"account={account}",
        f"password={password}",
        f"environment={environment_secret}",
        f"Authorization: Bearer {untracked_bearer}",
        *(f"safe-line-{index:02d}" for index in range(5, 22)),
    ]

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                '{"error_code":"authentication_failed",'
                f'"schema_version":"{_FULL_CHAIN_FAILURE_SCHEMA_VERSION}"}}'
            ).encode("utf-8"),
            stderr="\n".join(stderr_lines).encode("utf-8"),
        )

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    with pytest.raises(SmokeError) as captured:
        smoke_runner._run_full_chain_subprocess(
            repo_root=tmp_path,
            environment={
                "ETERNALAI_SESSION_SIGNING_KEY_B64": environment_secret,
            },
            account=account,
            password=password,
        )

    output = capsys.readouterr().out
    assert captured.value.code == "authentication_failed"
    assert account not in output
    assert password not in output
    assert environment_secret not in output
    assert untracked_bearer not in output
    assert "[REDACTED]" in output
    assert output.count("full_chain_subprocess_stderr_") == 20
    assert "full_chain_subprocess_stderr_20=" in output
    assert "safe-line-21" not in output


def test_full_chain_main_classifies_invalid_probe_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = full_chain_module.main(["--unsupported"])

    assert result == 1
    assert json.loads(capsys.readouterr().out) == {
        "error_code": "probe_argv_invalid",
        "schema_version": _FULL_CHAIN_FAILURE_SCHEMA_VERSION,
    }


def test_full_chain_main_classifies_configuration_build_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_detail = "sensitive-configuration-detail"
    monkeypatch.setattr(
        full_chain_module,
        "_read_login_credential",
        lambda: SimpleNamespace(),
    )

    def fail_configuration() -> None:
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(
        full_chain_module.ProductionSettings,
        "from_environment",
        fail_configuration,
    )

    result = full_chain_module.main([])

    output = capsys.readouterr().out
    assert result == 1
    assert json.loads(output) == {
        "error_code": "composition_build_failed",
        "schema_version": _FULL_CHAIN_FAILURE_SCHEMA_VERSION,
    }
    assert sensitive_detail not in output


def test_full_chain_main_maps_unclassified_exception_to_unknown_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        full_chain_module,
        "_read_login_credential",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        full_chain_module.ProductionSettings,
        "from_environment",
        lambda: SimpleNamespace(oa_read_adapter_mode="live"),
    )

    async def fail_unclassified(*_args: Any, **_kwargs: Any) -> FullChainOutcome:
        raise RuntimeError("sensitive-unclassified-detail")

    monkeypatch.setattr(
        full_chain_module,
        "run_full_chain_check",
        fail_unclassified,
    )

    result = full_chain_module.main([])

    assert result == 1
    assert json.loads(capsys.readouterr().out) == {
        "error_code": "unknown_error",
        "schema_version": _FULL_CHAIN_FAILURE_SCHEMA_VERSION,
    }


def test_replay_composition_uses_same_full_chain_boundary_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    capability_id = "oa.list_pending_workflows"
    observed: dict[str, Any] = {}

    def run_success(**kwargs: Any) -> FullChainOutcome:
        observed.update(kwargs)
        return _successful_outcome((capability_id,))

    monkeypatch.setattr(
        smoke_runner,
        "_run_full_chain_subprocess",
        run_success,
    )

    assert (
        smoke_runner._check_replay_composition(
            tmp_path,
            {"OA_READ_ADAPTER_MODE": "replay"},
            capability_id=capability_id,
        )
        is None
    )
    assert observed["expected_capability_ids"] == (capability_id,)
    assert observed["account"] == smoke_runner._REPLAY_FULL_CHAIN_ACCOUNT
    assert observed["password"] == smoke_runner._REPLAY_FULL_CHAIN_PASSWORD

    def run_failure(**_kwargs: Any) -> FullChainOutcome:
        raise SmokeError("full_chain_subprocess_timeout")

    monkeypatch.setattr(
        smoke_runner,
        "_run_full_chain_subprocess",
        run_failure,
    )
    assert (
        smoke_runner._check_replay_composition(
            tmp_path,
            {"OA_READ_ADAPTER_MODE": "replay"},
            capability_id=capability_id,
        )
        == ("full_chain_subprocess_timeout", None)
    )


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("nonzero", "full_chain_output_invalid"),
        ("invalid_output", "full_chain_output_invalid"),
        ("timeout", "full_chain_subprocess_timeout"),
        ("false_boolean", "trace_incomplete"),
        ("none_boolean", "full_chain_output_invalid"),
    ],
)
def test_full_chain_subprocess_boundary_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
    expected_code: str,
) -> None:
    account = "sensitive-account-canary"
    password = "sensitive-password-canary"
    outcome = _successful_outcome()
    if failure == "false_boolean":
        outcome = outcome.model_copy(
            update={
                "capabilities": (
                    outcome.capabilities[0].model_copy(
                        update={"trace_events_complete": False}
                    ),
                    outcome.capabilities[1],
                )
            }
        )

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout=300.0)
        if failure == "invalid_output":
            stdout = b"not-json"
        elif failure == "none_boolean":
            stdout = outcome.model_dump_json().replace(
                '"trace_events_complete":true',
                '"trace_events_complete":null',
                1,
            ).encode("utf-8")
        else:
            stdout = outcome.model_dump_json().encode("utf-8")
        return subprocess.CompletedProcess(
            command,
            1 if failure == "nonzero" else 0,
            stdout=stdout,
            stderr=b"ignored",
        )

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    with pytest.raises(SmokeError) as captured:
        smoke_runner._run_full_chain_subprocess(
            repo_root=tmp_path,
            environment={"PATH": "synthetic-path"},
            account=account,
            password=password,
        )

    assert captured.value.code == expected_code
    _assert_safe_smoke_error(captured.value, account, password)


def test_missing_required_trace_event_cannot_pass_full_chain_check() -> None:
    capability_id = "oa.list_pending_workflows"
    events = [
        SimpleNamespace(
            event_type=event_type,
            capability_id=(
                capability_id if event_type == "capability_selected" else None
            ),
        )
        for event_type in REQUIRED_TRACE_EVENTS - {"task_completed"}
    ]

    class FakeClient:
        async def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "schema_version": "phase0.sdui.v1",
                    "response_id": "response-fixture",
                    "task_id": "task-fixture",
                    "session_id": "session-fixture",
                    "status": "completed",
                    "message": "completed",
                    "fallback_text": "completed",
                    "ui": {"component_type": "none"},
                    "data": {
                        "workflows": [],
                        "returned_count": 0,
                        "authoritative_count": 0,
                        "is_complete": True,
                    },
                    "trace_id": "trace-fixture",
                    "trace_summary": None,
                },
            )

    class FakeTraceQuery:
        async def list_events_by_trace(self, _trace_id: str) -> list[Any]:
            return events

    outcome = asyncio.run(
        full_chain_module._run_capability_probe(
            client=cast(httpx.AsyncClient, FakeClient()),
            headers={},
            trace_query=cast(TraceQueryPort, FakeTraceQuery()),
            message="查询我的待办",
            capability_id=capability_id,
        )
    )

    assert outcome.successful_envelope is True
    assert outcome.normalized_data is True
    assert outcome.trace_events_complete is False
    combined = FullChainOutcome(
        schema_version=FULL_CHAIN_SCHEMA_VERSION,
        required_trace_event_count=len(REQUIRED_TRACE_EVENTS),
        capabilities=(outcome,),
    )
    assert combined.passed(expected_capability_ids=(capability_id,)) is False


def _synthetic_envelope(
    *,
    status: str,
    data: dict[str, Any] | None,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "phase0.sdui.v1",
        "response_id": "response-synthetic",
        "task_id": "task-synthetic",
        "session_id": "session-synthetic",
        "status": status,
        "message": "synthetic message",
        "fallback_text": "synthetic fallback",
        "ui": {
            "component_type": "none",
            "action": "none",
            "reason_code": reason_code,
            "payload": {},
        },
        "data": data,
        "trace_id": "trace-synthetic",
        "trace_summary": None,
    }


def _synthetic_terminal_event(
    error_code: object,
    *,
    capability_id: str = "oa.list_pending_workflows",
) -> SimpleNamespace:
    return SimpleNamespace(
        event_type="task_failed",
        status="failed",
        task_id="task-synthetic",
        session_id="session-synthetic",
        capability_id=capability_id,
        error_code=error_code,
    )


def _run_synthetic_probe_failure(
    *,
    response_payload: dict[str, Any] | None,
    trace_events: list[Any],
) -> Any:
    capability_id = "oa.list_pending_workflows"

    class FakeClient:
        async def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            if response_payload is None:
                return httpx.Response(
                    200,
                    content=b"{not-json",
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(200, json=response_payload)

    class FakeTraceQuery:
        async def list_events_by_trace(self, _trace_id: str) -> list[Any]:
            return trace_events

    with pytest.raises(full_chain_module._FullChainCheckError) as captured:
        asyncio.run(
            full_chain_module._run_capability_probe(
                client=cast(httpx.AsyncClient, FakeClient()),
                headers={},
                trace_query=cast(TraceQueryPort, FakeTraceQuery()),
                message="synthetic request",
                capability_id=capability_id,
            )
        )
    return captured.value


def _run_child_main_with_check_error(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> int:
    monkeypatch.setattr(
        full_chain_module,
        "_read_login_credential",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        full_chain_module.ProductionSettings,
        "from_environment",
        lambda: SimpleNamespace(oa_read_adapter_mode="live"),
    )

    async def fail_check(*_args: Any, **_kwargs: Any) -> FullChainOutcome:
        raise error

    monkeypatch.setattr(full_chain_module, "run_full_chain_check", fail_check)
    return full_chain_module.main([])


@pytest.mark.parametrize(
    ("response_payload", "trace_events", "expected_code"),
    [
        (None, [], "envelope_parse_failed"),
        (
            _synthetic_envelope(status="failed", data={"invalid": True}),
            [_synthetic_terminal_event("adapter_http_500")],
            "runtime_execution_failed",
        ),
        (
            _synthetic_envelope(status="completed", data={"invalid": True}),
            [],
            "capability_output_invalid",
        ),
    ],
)
def test_full_chain_failure_categories_are_exact_and_distinct(
    response_payload: dict[str, Any] | None,
    trace_events: list[Any],
    expected_code: str,
) -> None:
    error = _run_synthetic_probe_failure(
        response_payload=response_payload,
        trace_events=trace_events,
    )

    assert error.code == expected_code


def test_runtime_failure_code_comes_only_from_unique_terminal_trace(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui_reason_canary = "untrusted_ui_reason_canary"
    error = _run_synthetic_probe_failure(
        response_payload=_synthetic_envelope(
            status="failed",
            data={"invalid": True},
            reason_code=ui_reason_canary,
        ),
        trace_events=[_synthetic_terminal_event("adapter_http_500")],
    )

    assert error.code == "runtime_execution_failed"
    assert error.runtime_error_code == "adapter_http_500"
    result = _run_child_main_with_check_error(monkeypatch, error)
    captured_output = capsys.readouterr()
    output = captured_output.out
    assert result == 1
    assert captured_output.err == ""
    assert json.loads(output) == {
        "error_code": "runtime_execution_failed",
        "runtime_error_code": "adapter_http_500",
        "schema_version": _FULL_CHAIN_FAILURE_SCHEMA_VERSION,
    }
    assert output.count(ui_reason_canary) == 0


@pytest.mark.parametrize(
    "unsafe_code",
    [
        "bad code",
        "错误码",
        "a" * 65,
        "ADAPTER_HTTP_500",
        "shaped_unknown_canary",
    ],
)
def test_unsafe_runtime_failure_code_falls_back_without_echo(
    unsafe_code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    error = _run_synthetic_probe_failure(
        response_payload=_synthetic_envelope(
            status="failed",
            data={"invalid": True},
        ),
        trace_events=[_synthetic_terminal_event(unsafe_code)],
    )

    assert error.code == "runtime_execution_failed"
    assert error.runtime_error_code == "runtime_error_unavailable"
    result = _run_child_main_with_check_error(monkeypatch, error)
    captured_output = capsys.readouterr()
    output = captured_output.out
    assert result == 1
    assert captured_output.err == ""
    assert json.loads(output) == {
        "error_code": "runtime_execution_failed",
        "runtime_error_code": "runtime_error_unavailable",
        "schema_version": _FULL_CHAIN_FAILURE_SCHEMA_VERSION,
    }
    assert output.count(unsafe_code) == 0


def test_full_chain_subprocess_preserves_bounded_runtime_failure_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    account = "synthetic-runtime-account"
    password = "synthetic-runtime-password"

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                '{"error_code":"runtime_execution_failed",'
                '"runtime_error_code":"adapter_http_500",'
                f'"schema_version":"{_FULL_CHAIN_FAILURE_SCHEMA_VERSION}"}}'
            ).encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(smoke_runner.subprocess, "run", run)

    with pytest.raises(SmokeError) as captured:
        smoke_runner._run_full_chain_subprocess(
            repo_root=tmp_path,
            environment={"PATH": "synthetic-path"},
            account=account,
            password=password,
        )

    assert captured.value.code == "runtime_execution_failed"
    assert captured.value.runtime_error_code == "adapter_http_500"
    _assert_safe_smoke_error(captured.value, account, password)
