"""Golden runner decoupling and cross-cwd CLI tests."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

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


def test_cli_failure_output_does_not_echo_credential_canary(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    credential_value = hashlib.sha256(b"cli-credential-canary").hexdigest()
    judgement = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": {"password": credential_value},
        },
        expected_response={"status": "completed"},
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=["trace_contains_token"],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={},
    )
    result = GoldenTaskResult(
        golden_task_id="GT-SYNTHETIC-CLI-CREDENTIAL",
        category="negative",
        status=judgement.status,
        reasons=judgement.reasons,
    )

    monkeypatch.setattr(
        run_golden_tasks,
        "_load_runner",
        lambda: (lambda: [result], build_summary),
    )

    exit_code = run_golden_tasks.main(["--gate"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert exit_code == 1
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed"
    assert credential_value not in captured.out
    assert credential_value not in captured.err


@pytest.mark.parametrize(
    ("expected_value", "actual_value"),
    (
        (482907, 482908),
        (
            Decimal("482911.01"),
            UUID("00000000-0000-4000-8000-000000000081"),
        ),
    ),
)
def test_cli_direct_scalar_adapter_mismatch_does_not_echo_credential_canaries(
    monkeypatch: Any,
    capsys: Any,
    expected_value: Any,
    actual_value: Any,
) -> None:
    judgement = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": None,
        },
        expected_response={"status": "completed"},
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=[],
        adapter_assertion={
            "must_be_called": False,
            "must_not_be_called": False,
            "exact_arguments": {
                "oa.synthetic": [{"password": expected_value}],
            },
        },
        adapter_calls={},
        adapter_arguments={
            "oa.synthetic": [{"password": actual_value}],
        },
    )
    result = GoldenTaskResult(
        golden_task_id="GT-SYNTHETIC-CLI-NUMERIC-CREDENTIAL",
        category="negative",
        status=judgement.status,
        reasons=judgement.reasons,
    )

    monkeypatch.setattr(
        run_golden_tasks,
        "_load_runner",
        lambda: (lambda: [result], build_summary),
    )

    exit_code = run_golden_tasks.main(["--gate"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert exit_code == 1
    assert summary["failed"] == 1
    assert summary["results"][0]["reasons"] == [
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.assertion_inputs"
    ]
    for credential_value in (expected_value, actual_value):
        marker = str(credential_value)
        assert marker not in captured.out
        assert marker not in captured.err
