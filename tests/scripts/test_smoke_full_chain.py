from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from app.ports.trace import TraceQueryPort
from scripts.smoke import full_chain as full_chain_module
from scripts.smoke import runner as smoke_runner
from scripts.smoke.capabilities import REQUIRED_ACTIVE_OA_CAPABILITY_IDS
from scripts.smoke.errors import SmokeError
from scripts.smoke.full_chain_contract import (
    FULL_CHAIN_SCHEMA_VERSION,
    CapabilityFullChainOutcome,
    FullChainOutcome,
)
from scripts.smoke.trace_contract import REQUIRED_TRACE_EVENTS


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
            stderr=b"",
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
    assert observed["command"] == [
        smoke_runner.sys.executable,
        "-m",
        "scripts.smoke.full_chain",
        "--capability-id",
        capability_id,
    ]


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
        is True
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
        is False
    )


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("nonzero", "full_chain_subprocess_failed"),
        ("invalid_output", "full_chain_output_invalid"),
        ("timeout", "full_chain_subprocess_timeout"),
        ("false_boolean", "full_chain_verification_failed"),
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
