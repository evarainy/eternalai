#!/usr/bin/env python3
"""AST-based weak-test checker for Phase 0 CI baseline.

Detects test functions that contain only:
  - ``assert True`` or other tautological assertions (e.g. ``assert 1 == 1``)
  - ``pass`` with no assertions
  - no assertions at all

Allows real assertions and ``pytest.raises`` blocks.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WeakTestFinding:
    file: Path
    function_name: str
    line: int
    kind: str  # assert_true | pass_only | no_assertion | tautology
    description: str


def severity_rank(kind: str) -> str:
    """Return severity rank for a weak-test finding kind."""
    return {
        "pass_only": "high",
        "no_assertion": "medium",
        "tautology": "high",
    }.get(kind, "medium")


def _is_tautological_assert(node: ast.Assert) -> bool:
    """Check if an assert statement is tautological."""
    test = node.test
    # assert True
    if isinstance(test, ast.Constant) and test.value is True:
        return True
    # assert 1 == 1, assert "a" == "a", etc.
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if isinstance(test.ops[0], (ast.Eq, ast.Is)):
            left = test.left
            right = test.comparators[0]
            if isinstance(left, ast.Constant) and isinstance(right, ast.Constant):
                if left.value == right.value:
                    return True
            # assert value == value (same name on both sides)
            if isinstance(left, ast.Name) and isinstance(right, ast.Name):
                if left.id == right.id:
                    return True
            # assert obj.attr == obj.attr (same dotted name)
            if isinstance(left, ast.Attribute) and isinstance(right, ast.Attribute):
                if ast.dump(left) == ast.dump(right):
                    return True
    return False


def _has_pytest_raises(node: ast.AST) -> bool:
    """Check if the AST subtree contains a ``pytest.raises`` context manager."""
    for child in ast.walk(node):
        if isinstance(child, ast.With):
            for item in child.items:
                ctx = item.context_expr
                # pytest.raises(...)
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    if isinstance(func, ast.Attribute) and func.attr == "raises":
                        return True
                    if isinstance(func, ast.Name) and func.id == "raises":
                        return True
    return False


def _count_real_assertions(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count non-tautological assert statements and pytest.raises blocks."""
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert) and not _is_tautological_assert(child):
            count += 1
        elif isinstance(child, ast.With) and _has_pytest_raises(child):
            count += 1
    return count


def _is_pass_only(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if the function body is only ``pass`` (plus docstring)."""
    meaningful = [
        stmt for stmt in node.body
        if not isinstance(stmt, ast.Pass)
        and not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    return len(meaningful) == 0


def check_source(path: Path) -> list[WeakTestFinding]:
    """Analyze a Python test file and return weak-test findings."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[WeakTestFinding] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue

        # Check for pass-only
        if _is_pass_only(node):
            findings.append(WeakTestFinding(
                file=path,
                function_name=node.name,
                line=node.lineno,
                kind="pass_only",
                description=f"pass-only test function: {node.name}",
            ))
            continue

        # Count real assertions (includes pytest.raises)
        real_assertions = _count_real_assertions(node)

        if real_assertions == 0:
            # Check if there are any tautological asserts
            has_tautological = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert) and _is_tautological_assert(child):
                    has_tautological = True
                    break

            if has_tautological:
                findings.append(WeakTestFinding(
                    file=path,
                    function_name=node.name,
                    line=node.lineno,
                    kind="tautology",
                    description=(
                        f"tautology or assert True in: {node.name}"
                    ),
                ))
            else:
                findings.append(WeakTestFinding(
                    file=path,
                    function_name=node.name,
                    line=node.lineno,
                    kind="no_assertion",
                    description=f"no assertion in test function: {node.name}",
                ))
        else:
            # Has real assertions — but also check for tautological ones
            has_tautological = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert) and _is_tautological_assert(child):
                    has_tautological = True
                    break
            if has_tautological:
                findings.append(WeakTestFinding(
                    file=path,
                    function_name=node.name,
                    line=node.lineno,
                    kind="tautology",
                    description=(
                        f"tautology alongside real assertions in: "
                        f"{node.name}"
                    ),
                ))

    return findings


def check_directory(directory: Path) -> list[WeakTestFinding]:
    """Check all test_*.py files in *directory* recursively."""
    findings: list[WeakTestFinding] = []
    for py_file in sorted(directory.rglob("test_*.py")):
        findings.extend(check_source(py_file))
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check test files for weak-test patterns"
            " (assert True, pass-only, no assertions)."
        )
    )
    parser.add_argument(
        "path",
        type=Path,
        help="File or directory to scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = args.path.resolve()
    if target.is_file():
        findings = check_source(target)
    elif target.is_dir():
        findings = check_directory(target)
    else:
        print(f"Path not found: {target}", file=sys.stderr)
        return 2

    if findings:
        print(f"Weak-test check failed. {len(findings)} finding(s):", file=sys.stderr)
        for f in findings:
            print(
                f"  [{severity_rank(f.kind)}] {f.file}:{f.line} — {f.description}",
                file=sys.stderr,
            )
        return 1

    print("Weak-test check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
