"""In-memory transport and receipt checks; no vault, service or temporary files."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import httpx2
import pytest

from app.infra.browser import synthetic_trial_client as trial
from app.infra.browser.fixed_synthetic_seed import _COLLECTION

_TOKEN = secrets.token_urlsafe(24)
_SESSION_ID = secrets.token_urlsafe(24)


def _receipts(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}

    def create(name: str, document: dict[str, Any]) -> None:
        if name in files:
            raise ValueError("browser_trial_already_attempted")
        files[name] = document.copy()

    monkeypatch.setattr(trial, "create_trial_file", create)
    monkeypatch.setattr(trial, "read_trial_file", lambda name: files[name])
    monkeypatch.setattr(trial, "read_encrypted", lambda *args, **kwargs: {
        "business_token": _TOKEN,
    })
    return files


def _accepted() -> dict[str, Any]:
    return {
        "response_id": "response-1", "task_id": "task-1", "session_id": _SESSION_ID,
        "status": "running", "message": "private-message", "fallback_text": "private-fallback",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"kind": "accepted", "task_id": "task-1", "run_id": "run-1",
                 "state_revision": 1},
        "trace_id": "private-trace", "trace_summary": "private-summary",
    }


@pytest.mark.parametrize("mixed", [{"client_request_id": trial._REQUEST_ID},
                                    {"trial_id": trial.ORIGINAL_TRIAL}])
@pytest.mark.parametrize("trial_id", [trial.DIAGNOSTIC_TRIAL, trial.VISIBLE_TRIAL])
def test_second_receipt_rejects_cross_round_binding_before_private_input(
    mixed: dict, monkeypatch: pytest.MonkeyPatch,
    trial_id: str,
) -> None:
    document = {"task_id": "task-2", "run_id": "run-2", **trial.trial_reference(trial_id), **mixed}
    monkeypatch.setattr(trial, "read_trial_file", lambda *_args, **_kwargs: document)
    token = Mock()
    monkeypatch.setattr(trial, "_token", token)
    with pytest.raises(trial.TrialClientError, match="^browser_trial_receipt_invalid$"):
        asyncio.run(trial.run_trial("inspect", enabled=True, private_stdin=True,
                                   trial_id=trial_id))
    token.assert_not_called()


def _run(*, status: str = "completed", cleanup: str = "released",
         value: Any = None, cancel_requested: bool = False) -> dict[str, Any]:
    terminal = status in {"completed", "failed", "cancelled"}
    result = ({"business": status, "effect": "acknowledged",
               "verification": "verified" if status == "completed" else "incomplete",
               "cleanup": cleanup,
               "error_code": None if status == "completed" else "browser_not_sent",
               "terminal_revision": 2, "automatic_replay": False}
              if terminal else None)
    return {"run": {"schema_version": "browser.run.v1", "task_id": "task-1",
                    "run_id": "run-1", "state_revision": 2, "status": status,
                    "progress": None if terminal else {"phase": "running"},
                    "cancel": {"requested": cancel_requested, "acknowledged": False},
                    "result": result}, "value": value}


def _call(operation: str, handler: Callable[[httpx2.Request], httpx2.Response],
          *, enabled: bool = True) -> trial.TrialOutcome:
    async def execute() -> trial.TrialOutcome:
        async with httpx2.AsyncClient(
            base_url=trial.API_BASE, transport=httpx2.MockTransport(handler),
            trust_env=False, follow_redirects=False,
        ) as client:
            return await trial.run_trial(operation, enabled=enabled, client=client)  # type: ignore[arg-type]
    return asyncio.run(execute())


def test_submit_burns_marker_and_keeps_only_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert files == {"trial.submit.json": {"submission_attempts": 1}}
        calls.append(request.method)
        assert str(request.url) == trial.API_BASE + "/api/v1/runtime/handle"
        assert request.headers["Origin"] == trial.API_BASE
        assert request.headers["X-EternalAI-CSRF"] == "1"
        assert request.headers["Cookie"] == "eternalai_session=" + _TOKEN
        body = json.loads(request.content)
        assert body == {"channel": "api", "session_id": "browser_fixture_user",
                        "message": '{"business_key":"fixture_system_messages"}',
                        "client_capabilities": {
                            "browser_async_v1": True,
                            "browser_skill_id": "fixed_synthetic_message_detail",
                        },
                        "client_request_id": "P2-BROWSER-RUNTIME-V42-001-single-run"}
        return httpx2.Response(200, json=_accepted())

    outcome = _call("submit", handler)
    assert outcome.code == "browser_trial_accepted" and outcome.exit_code == 0
    assert calls == ["POST"]
    assert files == {"trial.submit.json": {"submission_attempts": 1},
                     "trial.run.json": {"task_id": "task-1", "run_id": "run-1"}}
    assert "private" not in repr(outcome) and _TOKEN not in repr(outcome)


def test_duplicate_refuses_before_vault_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    files["trial.submit.json"] = {"submission_attempts": 1}
    monkeypatch.setattr(trial, "read_encrypted",
                        lambda *args, **kwargs: pytest.fail("vault opened"))
    with pytest.raises(trial.TrialClientError, match="browser_trial_already_submitted"):
        _call("submit", lambda request: pytest.fail("HTTP sent"))


def test_failed_run_http_json_preserves_empty_artifacts_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _receipts(monkeypatch)
    files["trial.run.json"] = {"task_id": "task-1", "run_id": "run-1"}
    document = _run(status="failed", cleanup="terminated")
    document["run"]["artifacts"] = []
    document["run"]["result"]["verification"] = None
    document["run"]["result"]["error_code"] = "browser_verification_failed"
    document["run"]["result"]["dispatch_failure_code"] = "timeout"
    outcome = _call("inspect", lambda request: httpx2.Response(200, json=document))
    assert outcome.code == "browser_trial_terminal_unsuccessful"
    assert outcome.status == "failed" and outcome.exit_code == 2
    assert outcome.result_code == "browser_verification_failed"
    assert outcome.dispatch_failure_code == "timeout"
    assert files == {"trial.run.json": {"task_id": "task-1", "run_id": "run-1"}}


def test_marker_io_error_is_not_reported_as_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    _receipts(monkeypatch)

    def broken_create(name: str, document: dict[str, Any]) -> None:
        raise ValueError("browser_trial_already_attempted")

    monkeypatch.setattr(trial, "create_trial_file", broken_create)
    monkeypatch.setattr(trial, "read_encrypted",
                        lambda *args, **kwargs: pytest.fail("vault opened"))
    with pytest.raises(trial.TrialClientError, match="browser_trial_marker_unavailable"):
        _call("submit", lambda request: pytest.fail("HTTP sent"))


def test_timeout_keeps_burned_marker_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    calls: list[int] = []

    def timeout(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        raise httpx2.ReadTimeout("private_transport_detail")

    with pytest.raises(trial.TrialClientError) as caught:
        _call("submit", timeout)
    assert caught.value.code == "browser_trial_http_timeout"
    assert "private_transport_detail" not in str(caught.value)
    assert calls == [1] and files == {"trial.submit.json": {"submission_attempts": 1}}


def test_nonaccepted_response_keeps_marker_only(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    response = _accepted()
    response["data"] = {"kind": "pending", "task_id": "task-1"}
    with pytest.raises(trial.TrialClientError, match="browser_trial_response_invalid"):
        _call("submit", lambda request: httpx2.Response(200, json=response))
    assert files == {"trial.submit.json": {"submission_attempts": 1}}


def test_accepted_identifier_must_be_safe_for_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    response = _accepted()
    response["task_id"] = "task-1\n"
    response["data"]["task_id"] = "task-1\n"
    with pytest.raises(trial.TrialClientError, match="browser_trial_response_invalid"):
        _call("submit", lambda request: httpx2.Response(200, json=response))
    assert files == {"trial.submit.json": {"submission_attempts": 1}}


@pytest.mark.parametrize(("status", "cleanup", "value", "code", "exit_code"), [
    ("completed", "released", json.loads(_COLLECTION), "browser_trial_verified", 0),
    ("running", "pending", None, "browser_trial_nonterminal", 2),
    ("completed", "pending", json.loads(_COLLECTION), "browser_trial_cleanup_incomplete", 2),
    ("completed", "released", {"messages": [], "returned_count": 0, "is_complete": True},
     "browser_trial_value_invalid", 2),
    ("failed", "released", None, "browser_trial_terminal_unsuccessful", 2),
])
def test_inspect_one_get_and_terminal_gate(monkeypatch: pytest.MonkeyPatch, status: str,
                                           cleanup: str, value: Any, code: str,
                                           exit_code: int) -> None:
    files = _receipts(monkeypatch)
    files["trial.run.json"] = {"task_id": "task-1", "run_id": "run-1"}
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        assert str(request.url) == (
            trial.API_BASE + "/api/v1/browser-runs/task-1/run-1?session_id=browser_fixture_user"
        )
        return httpx2.Response(200, json=_run(status=status, cleanup=cleanup, value=value))

    outcome = _call("inspect", handler)
    assert calls == ["GET"] and outcome.code == code and outcome.exit_code == exit_code
    assert "fixture_message_1" not in repr(outcome)


def test_cancel_one_post_with_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    files["trial.run.json"] = {"task_id": "task-1", "run_id": "run-1"}
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        assert str(request.url) == trial.API_BASE + "/api/v1/browser-runs/task-1/run-1/cancel"
        assert json.loads(request.content) == {"session_id": "browser_fixture_user"}
        return httpx2.Response(202, json=_run(status="running", cancel_requested=True))

    outcome = _call("cancel", handler)
    assert calls == ["POST"] and outcome.code == "browser_trial_cancel_requested"
    assert outcome.exit_code == 0 and files == {"trial.run.json": files["trial.run.json"]}


def test_redirect_and_disabled_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    files = _receipts(monkeypatch)
    with pytest.raises(trial.TrialClientError, match="browser_trial_disabled"):
        _call("submit", lambda request: pytest.fail("HTTP sent"), enabled=False)
    assert files == {}
    with pytest.raises(trial.TrialClientError, match="browser_trial_http_rejected"):
        _call("submit", lambda request: httpx2.Response(307, headers={"Location": "/elsewhere"}))
    assert files == {"trial.submit.json": {"submission_attempts": 1}}


def test_cli_rejects_unknown_arguments_without_reflection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.run_browser_synthetic_client import main

    assert main(["--operation", "inspect", "--unknown", "private-value"]) == 2
    assert capsys.readouterr().out == '{"code": "browser_trial_arguments_invalid"}\n'
