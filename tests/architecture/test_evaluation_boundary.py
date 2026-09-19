"""Evidence contracts and deterministic rules cannot import infrastructure."""

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("path", ["app/ports/evaluation.py", "app/evaluator/overview.py"])
def test_evaluation_layers_do_not_depend_on_infra_runtime_or_workflow(path):
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert imports
    assert not any(
        name.startswith(("app.infra", "app.runtime", "app.workflow")) for name in imports
    )
