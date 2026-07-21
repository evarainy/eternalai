"""Golden runner decoupling and cross-cwd CLI tests."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

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
    assert summary["passed"] == 18
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
