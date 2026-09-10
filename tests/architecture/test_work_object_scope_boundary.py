"""Static guard for literal authorization lists and I/O-free scope decisions."""

from __future__ import annotations

import ast
from pathlib import Path

PORT_PATH = Path(__file__).resolve().parents[2] / "app/ports/work_object_scope.py"


def test_scope_allowlists_are_exact_module_level_frozenset_literals() -> None:
    tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
    assignments = {
        node.target.id: node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    expected = {
        "_DEPARTMENT_HEAD_JOBTITLE_IDS": {"75", "380", "1405", "1701", "1999"},
        "_PRISON_AREA_DEPARTMENT_IDS": {
            "572",
            "575",
            "580",
            "585",
            "588",
            "589",
            "590",
            "591",
            "592",
            "593",
            "594",
            "595",
            "596",
            "597",
            "598",
            "599",
            "600",
            "601",
            "602",
            "603",
            "604",
            "605",
            "606",
            "607",
            "608",
            "619",
            "622",
            "1419",
            "1420",
            "1923",
        },
    }
    for name, values in expected.items():
        expression = assignments[name]
        assert isinstance(expression, ast.Call)
        assert isinstance(expression.func, ast.Name) and expression.func.id == "frozenset"
        assert len(expression.args) == 1 and expression.keywords == []
        literal = expression.args[0]
        assert isinstance(literal, ast.Set)
        assert all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in literal.elts
        )
        assert ast.literal_eval(literal) == values
        assert len(literal.elts) == len(values)


def test_scope_module_has_no_environment_configuration_or_io_dependencies() -> None:
    tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
    allowed_imports = {
        "__future__",
        "typing",
        "pydantic",
        "app.ports.organization_directory",
    }
    allowed_calls = {
        "frozenset",
        "ConfigDict",
        "bool",
        "DispatchAuthorizationDecision",
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


def test_scope_lists_and_dependencies_remain_literal_and_io_free() -> None:
    test_scope_allowlists_are_exact_module_level_frozenset_literals()
    test_scope_module_has_no_environment_configuration_or_io_dependencies()
    tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))


def test_work_object_routes_and_store_calls_use_scoped_boundary() -> None:
    root = PORT_PATH.parents[2]
    tree = ast.parse((root / "app/api/v1/work_objects.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is not None
            assert not node.module.startswith(("app.infra", "sqlalchemy", "psycopg"))
        if isinstance(node, ast.Import):
            assert all(
                not alias.name.startswith(("app.infra", "sqlalchemy", "psycopg"))
                for alias in node.names
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "list_for_assignee",
                "get_for_assignee",
                "set_handling_mark_for_assignee",
            }
    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkObjectService"
    )
    for method_name, store_call, scope_position in (
        ("list_for_principal", "list_for_scope", 0),
        ("get_for_principal", "get_for_scope", 1),
        ("set_handling_mark_for_principal", "set_handling_mark_for_scope", 1),
    ):
        method = next(
            node
            for node in service.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name
        )
        calls = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == store_call
        ]
        assert len(calls) == 1
        argument = calls[0].args[scope_position]
        assert isinstance(argument, ast.Name) and argument.id == "scope"
