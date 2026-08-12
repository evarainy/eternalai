"""Golden runner decoupling and cross-cwd CLI tests."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import run_golden_tasks
from scripts.golden_task_assertions import judge_assertions
from scripts.golden_task_evaluator import GoldenTaskResult, build_summary

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_MODULES = (
    REPO_ROOT / "scripts" / "run_golden_tasks.py",
    REPO_ROOT / "scripts" / "golden_task_assertions.py",
    REPO_ROOT / "scripts" / "golden_task_fixture_support.py",
    REPO_ROOT / "scripts" / "golden_task_evaluator.py",
)


class _CanaryValue:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.str_calls = 0
        self.repr_calls = 0

    def __str__(self) -> str:
        self.str_calls += 1
        return self.marker

    def __repr__(self) -> str:
        self.repr_calls += 1
        return self.marker


class _CanaryAssertionError(_CanaryValue, AssertionError):
    pass


class _CanaryValueError(_CanaryValue, ValueError):
    pass


class _DynamicDump:
    def __init__(
        self,
        *,
        first: Any = None,
        later: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.first = first
        self.later = later
        self.error = error
        self.call_count = 0

    def model_dump(self) -> Any:
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return self.first if self.call_count == 1 else self.later


def test_shared_golden_modules_have_no_test_or_pytest_imports() -> None:
    offenders: list[str] = []
    for path in SHARED_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "pytest" or name == "tests" or name.startswith("tests."):
                    offenders.append(f"{path.name}:{name}")
    assert offenders == []


def test_real_runner_works_from_non_repo_cwd() -> None:
    non_repo_cwd = REPO_ROOT.parent
    assert not (non_repo_cwd / ".git").exists()

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_golden_tasks.py"),
            "--gate",
        ],
        cwd=non_repo_cwd,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["passed"] == 27
    assert summary["failed"] == 0


def test_real_runner_maps_evaluator_import_failure_to_exit_two() -> None:
    bootstrap = """
import importlib.abc
import runpy
import sys

class BlockEvaluator(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "scripts.golden_task_evaluator":
            raise ImportError("synthetic evaluator import failure")
        return None

sys.meta_path.insert(0, BlockEvaluator())
script_path = sys.argv[1]
sys.argv = [script_path]
runpy.run_path(script_path, run_name="__main__")
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            str(REPO_ROOT / "scripts" / "run_golden_tasks.py"),
        ],
        cwd=REPO_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == (
        "Golden Task runner infrastructure failure: "
        "synthetic evaluator import failure\n"
    )
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("mode", ("stateful", "assertion_error", "value_error"))
def test_cli_dynamic_model_is_evaluated_once_without_canary_echo(
    monkeypatch: Any,
    capsys: Any,
    mode: str,
) -> None:
    marker = hashlib.sha256(f"cli-dynamic-{mode}".encode()).hexdigest()
    canary = _CanaryValue(marker)
    safe_envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }
    error: _CanaryValue | None = None
    if mode == "stateful":
        model = _DynamicDump(
            first=safe_envelope,
            later={**safe_envelope, "status": canary},
        )
        envelope: Any = model
        expected_status = "synthetic-mismatch"
        expected_reason = (
            "response status expected 'synthetic-mismatch', got 'completed'"
        )
    else:
        error = (
            _CanaryAssertionError(marker)
            if mode == "assertion_error"
            else _CanaryValueError(marker)
        )
        model = _DynamicDump(error=error)
        envelope = {**safe_envelope, "data": {"password": model}}
        expected_status = "completed"
        expected_reason = (
            "forbidden credential pattern detected: "
            "rule=model_dump_error; location=actual.envelope"
        )

    def evaluate() -> list[GoldenTaskResult]:
        judgement = judge_assertions(
            envelope=envelope,
            expected_response={"status": expected_status},
            trace_steps=[],
            expected_trace={"event_sequence": []},
            forbidden_items=[],
            adapter_assertion={
                "must_be_called": False,
                "must_not_be_called": False,
            },
            adapter_calls={},
        )
        return [
            GoldenTaskResult(
                golden_task_id="GT-SYNTHETIC-CLI-DYNAMIC",
                category="negative",
                status=judgement.status,
                reasons=judgement.reasons,
            )
        ]

    monkeypatch.setattr(
        run_golden_tasks,
        "_load_runner",
        lambda: (evaluate, build_summary),
    )

    exit_code = run_golden_tasks.main(["--gate"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert exit_code == 1
    assert summary["failed"] == 1
    assert summary["results"][0]["reasons"] == [expected_reason]
    assert model.call_count == 1
    assert marker not in repr(summary)
    assert marker not in captured.out
    assert marker not in captured.err
    assert canary.str_calls == 0
    assert canary.repr_calls == 0
    if error is not None:
        assert error.str_calls == 0
        assert error.repr_calls == 0
