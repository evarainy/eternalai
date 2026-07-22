"""Import boundary checker — Phase 0 architecture guard.

Each boundary rule declares a ``source`` package, a set of ``forbidden``
imports, and the filesystem path that must exist for the rule to be active.
If the source path does not exist on disk, the rule is recorded as
``not_applicable`` with a complete explanation.

The test suite fails if **every** rule is ``not_applicable``, ensuring that at
least one real boundary is always exercised.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"


# ---------------------------------------------------------------------------
# Boundary rule definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoundaryRule:
    name: str
    source: str
    forbidden_imports: tuple[str, ...]
    source_path: Path = field(default=Path(), repr=False)
    not_applicable_reason: str = ""
    not_applicable_scope: str = ""
    blocked_by_task_id: str = ""
    activation_task_id: str = ""
    expiry_condition: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        # Auto-resolve source_path from source string (e.g. "app.ports" → app/ports/)
        if self.source_path == Path():
            object.__setattr__(
                self, "source_path", REPO_ROOT / self.source.replace(".", "/")
            )


def _boundary_rules() -> list[BoundaryRule]:
    return [
        BoundaryRule(
            name="ports_no_infra_imports",
            source="app.ports",
            forbidden_imports=("app.infra",),
        ),
        BoundaryRule(
            name="runtime_no_execution_fabric",
            source="app.runtime",
            forbidden_imports=("app.execution_fabric",),
            not_applicable_reason=(
                "app/runtime/ does not exist yet; "
                "runtime not implemented in Phase 0"
            ),
            not_applicable_scope="waiting_dependency",
            blocked_by_task_id="none",
            activation_task_id="future runtime implementation task",
            expiry_condition="app/runtime/ directory and modules exist",
            evidence="app/runtime/ directory absent from repo",
        ),
        BoundaryRule(
            name="memory_no_infra_or_execution_fabric_imports",
            source="app.memory",
            forbidden_imports=("app.infra", "app.execution_fabric"),
        ),
        BoundaryRule(
            name="knowledge_no_infra_runtime_gateway_or_trace_imports",
            source="app.knowledge",
            forbidden_imports=(
                "app.infra",
                "app.execution_fabric",
                "app.runtime",
                "app.ports.capability_gateway",
                "app.ports.trace",
            ),
        ),
        BoundaryRule(
            name="evaluator_no_infra_runtime_or_trace_imports",
            source="app.evaluator",
            forbidden_imports=("app.infra", "app.runtime", "app.ports.trace"),
        ),
        BoundaryRule(
            name="admin_no_execution_or_infra_dependencies",
            source="app.admin",
            forbidden_imports=(
                "app.infra",
                "app.runtime",
                "app.workflow",
                "app.execution_fabric",
                "app.ports.adapter",
                "app.ports.capability_gateway",
            ),
        ),
        BoundaryRule(
            name="gateway_no_runtime",
            source="app.gateway",
            forbidden_imports=("app.runtime",),
            not_applicable_reason=(
                "app/gateway/ does not exist yet; "
                "gateway not implemented in Phase 0"
            ),
            not_applicable_scope="waiting_dependency",
            blocked_by_task_id="none",
            activation_task_id="future gateway implementation task",
            expiry_condition="app/gateway/ directory and modules exist",
            evidence="app/gateway/ directory absent from repo",
        ),
        BoundaryRule(
            name="workflow_executes_only_through_gateway_boundary",
            source="app.workflow",
            forbidden_imports=(
                "app.execution_fabric",
                "app.infra",
                "app.ports.adapter",
                "app.ports.job_queue",
                "app.ports.policy_guard",
                "app.ports.secret_provider",
            ),
            not_applicable_reason=(
                "app/workflow/ does not exist yet; "
                "workflow not implemented in Phase 0"
            ),
            not_applicable_scope="waiting_dependency",
            blocked_by_task_id="none",
            activation_task_id="future workflow implementation task",
            expiry_condition="app/workflow/ directory and modules exist",
            evidence="app/workflow/ directory absent from repo",
        ),
        BoundaryRule(
            name="skill_no_execution_fabric",
            source="app.skill",
            forbidden_imports=("app.execution_fabric",),
            not_applicable_reason=(
                "app/skill/ does not exist yet; "
                "skill not implemented in Phase 0"
            ),
            not_applicable_scope="waiting_dependency",
            blocked_by_task_id="none",
            activation_task_id="future skill implementation task",
            expiry_condition="app/skill/ directory and modules exist",
            evidence="app/skill/ directory absent from repo",
        ),
        BoundaryRule(
            name="admin_console_no_real_adapters",
            source="app.admin_console",
            forbidden_imports=("app.execution_fabric.real_adapters",),
            not_applicable_reason=(
                "app/admin_console/ does not exist yet; "
                "admin console not implemented in Phase 0"
            ),
            not_applicable_scope="waiting_dependency",
            blocked_by_task_id="none",
            activation_task_id="future admin console implementation task",
            expiry_condition=(
                "app/admin_console/ directory and modules exist"
            ),
            evidence="app/admin_console/ directory absent from repo",
        ),
        BoundaryRule(
            name="real_adapters_no_runtime",
            source="app.execution_fabric.real_adapters",
            forbidden_imports=("app.runtime",),
            not_applicable_reason=(
                "app/execution_fabric/real_adapters/ does not exist yet"
            ),
            not_applicable_scope="waiting_dependency",
            blocked_by_task_id="none",
            activation_task_id="future real adapter implementation task",
            expiry_condition=(
                "app/execution_fabric/real_adapters/ "
                "directory and modules exist"
            ),
            evidence=(
                "app/execution_fabric/real_adapters/ "
                "directory absent from repo"
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# AST-based import scanner
# ---------------------------------------------------------------------------

def _file_package(
    py_file: Path, source_root: Path, repo_root: Path = REPO_ROOT
) -> list[str]:
    """Return the package components for *py_file*.

    Walks up from the file's directory counting ``__init__.py`` files
    to determine the actual Python package depth, relative to repo_root.
    """
    resolved = py_file.resolve()
    repo_resolved = repo_root.resolve()
    try:
        resolved.relative_to(repo_resolved)
    except ValueError:
        repo_resolved = source_root.resolve()
    # Collect package components by walking up and checking __init__.py
    parts: list[str] = []
    current = resolved.parent
    while current != repo_resolved and current != current.parent:
        if (current / "__init__.py").exists():
            parts.append(current.name)
            current = current.parent
        else:
            break
    parts.reverse()
    return parts


def _collect_imports(
    py_file: Path, source_root: Path, repo_root: Path = REPO_ROOT
) -> list[str]:
    """Return all module paths imported by *py_file*, resolved to absolute."""
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))
    pkg_parts = _file_package(py_file, source_root, repo_root)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import: resolve to absolute
                # level=1 → current package, level=2 → parent package
                up = node.level - 1
                base = pkg_parts[: len(pkg_parts) - up] if up <= len(pkg_parts) else []
                if node.module:
                    resolved = ".".join(base + [node.module]) if base else node.module
                else:
                    resolved = ".".join(base) if base else ""
                if resolved:
                    modules.append(resolved)
                    for alias in node.names:
                        modules.append(f"{resolved}.{alias.name}")
            elif node.module:
                # Absolute from-import
                modules.append(node.module)
                for alias in node.names:
                    dotted = f"{node.module}.{alias.name}"
                    if dotted != node.module:
                        modules.append(dotted)
    return list(dict.fromkeys(modules))


def _find_violations(
    rule: BoundaryRule,
    repo_root: Path = REPO_ROOT,
) -> list[tuple[Path, str, str]]:
    """Return ``[(file, imported_module, forbidden_prefix)]`` for violations."""
    violations: list[tuple[Path, str, str]] = []
    seen: set[tuple[Path, str]] = set()
    for py_file in rule.source_path.rglob("*.py"):
        for imported in _collect_imports(py_file, rule.source_path, repo_root):
            for forbidden in rule.forbidden_imports:
                if imported == forbidden or imported.startswith(forbidden + "."):
                    key = (py_file, forbidden)
                    if key not in seen:
                        seen.add(key)
                        violations.append((py_file, imported, forbidden))
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestImportBoundaries:
    """Verify that each boundary rule is either enforced or not_applicable."""

    @pytest.fixture()
    def rules(self) -> list[BoundaryRule]:
        return _boundary_rules()

    def _is_applicable(self, rule: BoundaryRule) -> bool:
        return rule.source_path.is_dir()

    def test_not_all_rules_are_not_applicable(self, rules: list[BoundaryRule]) -> None:
        """Guard: at least one boundary must be active."""
        applicable = [r for r in rules if self._is_applicable(r)]
        assert len(applicable) > 0, (
            "All boundary rules are not_applicable — at least one active rule is required"
        )

    def test_active_rules_have_complete_not_applicable_fields_when_inactive(
        self, rules: list[BoundaryRule]
    ) -> None:
        """Inactive rules must have complete explanation fields."""
        inactive = [r for r in rules if not self._is_applicable(r)]
        for rule in inactive:
            assert rule.not_applicable_reason, f"{rule.name}: missing not_applicable_reason"
            assert rule.not_applicable_scope, f"{rule.name}: missing not_applicable_scope"
            assert rule.blocked_by_task_id, f"{rule.name}: missing blocked_by_task_id"
            assert rule.activation_task_id, f"{rule.name}: missing activation_task_id"
            assert rule.expiry_condition, f"{rule.name}: missing expiry_condition"
            assert rule.evidence, f"{rule.name}: missing evidence"

    def test_active_rules_enforce_boundary(self, rules: list[BoundaryRule]) -> None:
        """Active rules must have zero violations."""
        applicable = [r for r in rules if self._is_applicable(r)]
        all_violations: list[str] = []
        for rule in applicable:
            for file_path, imported, forbidden in _find_violations(rule):
                all_violations.append(
                    f"{rule.name}: {file_path.relative_to(REPO_ROOT)} imports {imported} "
                    f"(forbidden: {forbidden})"
                )
        assert all_violations == [], "Import boundary violations:\n" + "\n".join(all_violations)

    def test_not_applicable_rules_return_records(
        self, rules: list[BoundaryRule]
    ) -> None:
        """Each not_applicable rule returns a complete record."""
        inactive = [r for r in rules if not self._is_applicable(r)]
        for rule in inactive:
            record = {
                "name": rule.name,
                "result": "not_applicable",
                "not_applicable_reason": rule.not_applicable_reason,
                "not_applicable_scope": rule.not_applicable_scope,
                "blocked_by_task_id": rule.blocked_by_task_id,
                "activation_task_id": rule.activation_task_id,
                "expiry_condition": rule.expiry_condition,
                "evidence": rule.evidence,
            }
            # All fields must be non-empty
            for key, value in record.items():
                assert value, f"{rule.name}: field {key!r} is empty"

    def test_forbidden_import_detected_in_temp_fixture(self, tmp_path: Path) -> None:
        """Negative validation: create a temp file with a forbidden import and verify detection."""
        # Create a minimal package structure in tmp_path
        pkg_dir = tmp_path / "app" / "ports"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "bad.py").write_text(
            "from app.infra.job_queue.in_memory import InMemoryJobQueue\n",
            encoding="utf-8",
        )

        rule = BoundaryRule(
            name="temp_fixture_test",
            source="app.ports",
            forbidden_imports=("app.infra",),
            source_path=pkg_dir,
        )

        violations = _find_violations(rule)
        assert len(violations) == 1
        file_path, imported, forbidden = violations[0]
        assert "bad.py" in str(file_path)
        assert imported == "app.infra.job_queue.in_memory"
        assert forbidden == "app.infra"

    def test_clean_import_passes_boundary_check(self, tmp_path: Path) -> None:
        """Positive validation: a clean import should not trigger violations."""
        pkg_dir = tmp_path / "app" / "ports"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "good.py").write_text(
            "from typing import Any, Protocol\n",
            encoding="utf-8",
        )

        rule = BoundaryRule(
            name="temp_fixture_test",
            source="app.ports",
            forbidden_imports=("app.infra",),
            source_path=pkg_dir,
        )

        violations = _find_violations(rule)
        assert violations == []

    def test_workflow_models_are_linear_without_graph_or_queue_fields(self) -> None:
        from app.workflow.models import WorkflowDefinition, WorkflowStep

        forbidden_fields = {
            "dependencies",
            "edges",
            "job_queue",
            "nodes",
            "queue",
        }
        assert {"workflow_id", "version", "steps"}.issubset(
            WorkflowDefinition.__dataclass_fields__
        )
        assert {"step_id", "capability_id", "when"}.issubset(
            WorkflowStep.__dataclass_fields__
        )
        assert forbidden_fields.isdisjoint(WorkflowDefinition.__dataclass_fields__)
        assert forbidden_fields.isdisjoint(WorkflowStep.__dataclass_fields__)

    def test_from_import_detected_in_temp_fixture(
        self, tmp_path: Path
    ) -> None:
        """Negative validation: 'from app import infra' style is detected."""
        pkg_dir = tmp_path / "app" / "ports"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "bad.py").write_text(
            "from app import infra\n",
            encoding="utf-8",
        )

        rule = BoundaryRule(
            name="temp_fixture_from_import",
            source="app.ports",
            forbidden_imports=("app.infra",),
            source_path=pkg_dir,
        )

        violations = _find_violations(rule)
        assert len(violations) == 1
        file_path, imported, forbidden = violations[0]
        assert "bad.py" in str(file_path)
        assert imported == "app.infra"
        assert forbidden == "app.infra"

    def test_relative_import_detected_in_temp_fixture(
        self, tmp_path: Path
    ) -> None:
        """Negative validation: 'from ..infra import x' is detected."""
        # Create app/ports/bad.py under tmp_path as simulated repo root
        app_dir = tmp_path / "app"
        ports_dir = app_dir / "ports"
        ports_dir.mkdir(parents=True)
        (app_dir / "__init__.py").write_text("", encoding="utf-8")
        (ports_dir / "__init__.py").write_text("", encoding="utf-8")
        (ports_dir / "bad.py").write_text(
            "from ..infra import x\n",
            encoding="utf-8",
        )

        rule = BoundaryRule(
            name="temp_fixture_relative",
            source="app.ports",
            forbidden_imports=("app.infra",),
            source_path=ports_dir,
        )

        violations = _find_violations(rule, repo_root=tmp_path)
        assert len(violations) == 1
        file_path, imported, forbidden = violations[0]
        assert "bad.py" in str(file_path)
        assert imported == "app.infra"
        assert forbidden == "app.infra"
