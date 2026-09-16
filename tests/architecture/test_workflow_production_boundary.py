"""Production Workflow sources must not depend on provisioning scripts."""

import ast
from pathlib import Path

import pytest


def _imports(source: str) -> set[str]:
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)
    return imported


def test_workflow_catalog_imports_stay_inside_application() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "app/workflow/definitions.py",
        root / "app/infra/workflow/catalog.py",
        root / "app/infra/workflow/production.py",
        root / "app/infra/adapters/oa/capabilities.py",
        root / "app/composition.py",
    ]
    for path in paths:
        imported = _imports(path.read_text(encoding="utf-8"))
        assert not any(
            name.split(".")[0] in {"scripts", "tests", "experiments"} for name in imported
        )


def test_static_definitions_have_only_model_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    imported = _imports((root / "app/workflow/definitions.py").read_text(encoding="utf-8"))
    assert imported == {
        "app.workflow.models",
        "app.workflow.models.WorkflowDefinition",
        "app.workflow.models.WorkflowStep",
    }


def test_runtime_uses_workflow_port_without_concrete_engine_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    imported = set().union(*(
        _imports(path.read_text(encoding="utf-8")) for path in (root / "app/runtime").rglob("*.py")
    ))
    assert "app.ports.workflow_engine.WorkflowEnginePort" in imported
    assert not any(name == "app.workflow" or name.startswith("app.workflow.") for name in imported)
    assert not any(name.startswith("app.infra.workflow") for name in imported)


@pytest.mark.parametrize("source,expected", [
    ("import scripts.manage_workflow_capability", "scripts.manage_workflow_capability"),
    ("from scripts import manage_workflow_capability", "scripts.manage_workflow_capability"),
    ("from app.workflow import engine", "app.workflow.engine"),
])
def test_import_guard_resolves_direct_import_forms(source, expected) -> None:
    assert expected in _imports(source)
