"""Static guard for literal authorization lists and I/O-free scope decisions."""

from __future__ import annotations

import ast
from pathlib import Path

PORT_PATH = Path(__file__).resolve().parents[2] / "app/ports/work_object_scope.py"


def test_scope_allowlists_are_exact_module_level_frozenset_literals() -> None:
    tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
    assignments = {
        node.target.id: node.value for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    expected = {
        "_DEPARTMENT_HEAD_JOBTITLE_IDS": {"75", "380", "1405", "1701", "1999"},
        "_PRISON_AREA_DEPARTMENT_IDS": {
            "572", "575", "580", "585", "588", "589", "590", "591", "592", "593",
            "594", "595", "596", "597", "598", "599", "600", "601", "602", "603",
            "604", "605", "606", "607", "608", "619", "622", "1419", "1420", "1923",
        },
    }
    for name, values in expected.items():
        expression = assignments[name]
        assert isinstance(expression, ast.Call)
        assert isinstance(expression.func, ast.Name) and expression.func.id == "frozenset"
        assert len(expression.args) == 1 and expression.keywords == []
        literal = expression.args[0]
        assert isinstance(literal, ast.Set)
        assert all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                   for item in literal.elts)
        assert ast.literal_eval(literal) == values
        assert len(literal.elts) == len(values)


def test_scope_module_has_no_environment_configuration_or_io_dependencies() -> None:
    tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
    allowed_imports = {
        "__future__", "typing", "pydantic", "app.ports.organization_directory",
    }
    allowed_calls = {
        "frozenset", "ConfigDict", "bool", "DispatchAuthorizationDecision",
        "AuthorizedWorkObjectScope",
    }
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Import, ast.Await, ast.AsyncFunctionDef))
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module in allowed_imports
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id in allowed_calls
            else:
                assert isinstance(node.func, ast.Attribute)
                assert isinstance(node.func.value, ast.Name)
                assert node.func.value.id == "department_id" and node.func.attr == "strip"
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"environ", "getenv", "settings", "config", "__dict__"}
