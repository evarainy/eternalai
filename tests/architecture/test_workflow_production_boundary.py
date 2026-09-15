"""Production Workflow sources must not depend on provisioning scripts."""

import ast
from pathlib import Path


def test_workflow_catalog_imports_stay_inside_application() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "app/workflow/definitions.py",
        root / "app/infra/workflow/catalog.py",
        root / "app/infra/workflow/production.py",
        root / "app/infra/adapters/oa/capabilities.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert not any(
            name.split(".")[0] in {"scripts", "tests", "experiments"} for name in imported
        )
