"""Supplementary guard; primary boundary: \
tests/infra/organization_directory/test_postgresql.py::\
test_list_user_memberships_returns_complete_set_across_organization_values."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PORT_PATH = REPO_ROOT / "app" / "ports" / "organization_directory.py"
POSTGRESQL_PATH = (
    REPO_ROOT / "app" / "infra" / "organization_directory" / "postgresql.py"
)
DIRECTORY_FILES = (
    PORT_PATH,
    REPO_ROOT / "app" / "infra" / "organization_directory" / "importer.py",
    POSTGRESQL_PATH,
    REPO_ROOT / "app" / "infra" / "organization_directory" / "reader.py",
    REPO_ROOT / "app" / "infra" / "organization_directory" / "validation.py",
)
EXPECTED_PROTOCOL_METHODS = {
    "OrganizationDirectoryPort": {
        "replace_snapshot": ("self", "snapshot"),
        "get_department": ("self", "department_id"),
        "list_department_subtree": ("self", "department_id"),
        "list_user_memberships": ("self", "user_id"),
    },
    "OrganizationDirectorySourcePort": {
        "fetch_departments": ("self",),
        "fetch_authoritative_user_count": ("self",),
        "fetch_user_page": ("self", "current_page"),
    },
}
STORAGE_READ_METHODS = {
    "get_department",
    "list_department_subtree",
    "list_user_memberships",
}
ALLOWED_NAMED_READ_CALLS = {"OrganizationDirectoryError", "any", "dict", "text"}


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    )


def _arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    return tuple(argument.arg for argument in node.args.args)


def _is_absent_row_expression(node: ast.IfExp) -> bool:
    return (
        isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "row"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Is)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value is None
        and isinstance(node.body, ast.Constant)
        and node.body.value is None
    )


def _is_fail_closed_raise_only(node: ast.If) -> bool:
    return (
        not node.orelse
        and len(node.body) == 1
        and isinstance(node.body[0], ast.Raise)
        and isinstance(node.body[0].exc, ast.Call)
        and isinstance(node.body[0].exc.func, ast.Name)
        and node.body[0].exc.func.id == "OrganizationDirectoryError"
    )


class _StructuralReadVisitor(ast.NodeVisitor):
    def __init__(self, method_name: str) -> None:
        self.method_name = method_name
        self.violations: list[str] = []

    def visit_ListComp(self, node: ast.ListComp) -> None:
        if any(generator.ifs for generator in node.generators):
            self.violations.append("conditional collection filtering")
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        if any(generator.ifs for generator in node.generators):
            self.violations.append("conditional collection filtering")
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        if any(generator.ifs for generator in node.generators):
            self.violations.append("conditional collection filtering")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "filter":
            self.violations.append("filter strategy execution")
        if (
            isinstance(node.func, ast.Name)
            and node.func.id not in ALLOWED_NAMED_READ_CALLS
        ):
            self.violations.append("unreviewed read callable")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if not _is_fail_closed_raise_only(node):
            self.violations.append("conditional read branch")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        if self.method_name != "get_department" or not _is_absent_row_expression(node):
            self.violations.append("conditional return branch")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.violations.append("imperative row filtering")
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.violations.append("imperative row filtering")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.violations.append("imperative row filtering")
        self.generic_visit(node)


def _structural_read_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    adapter = _class(tree, "PostgreSQLOrganizationDirectory")
    violations: list[str] = []
    for node in adapter.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in STORAGE_READ_METHODS:
            continue
        visitor = _StructuralReadVisitor(node.name)
        visitor.visit(node)
        violations.extend(f"{node.name}: {item}" for item in visitor.violations)
    return violations


def test_directory_boundary_has_no_authorization_decisions() -> None:
    forbidden_names = {
        "authorize",
        "authorized",
        "authorization",
        "can_view",
        "can_dispatch",
        "is_approver",
    }
    discovered: set[str] = set()
    for path in DIRECTORY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                discovered.add(node.name.casefold())

    assert forbidden_names.isdisjoint(discovered)


def test_directory_ports_expose_only_structural_reads_without_decision_context() -> None:
    tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
    for protocol_name, expected_methods in EXPECTED_PROTOCOL_METHODS.items():
        protocol = _class(tree, protocol_name)
        discovered = {
            node.name: _arguments(node)
            for node in protocol.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert discovered == expected_methods


def test_postgresql_adapter_cannot_inject_filtering_capability() -> None:
    tree = ast.parse(POSTGRESQL_PATH.read_text(encoding="utf-8"))
    adapter = _class(tree, "PostgreSQLOrganizationDirectory")
    constructor = next(
        node
        for node in adapter.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    instance_attributes = {
        node.attr
        for node in ast.walk(adapter)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    public_methods = {
        node.name
        for node in adapter.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }

    assert _arguments(constructor) == ("self", "session_factory")
    assert instance_attributes == {"_session_factory"}
    assert public_methods == {
        "replace_snapshot",
        "get_department",
        "list_department_subtree",
        "list_user_memberships",
    }


def test_directory_storage_reads_are_unfiltered_structural_projection() -> None:
    source = POSTGRESQL_PATH.read_text(encoding="utf-8")

    assert _structural_read_violations(source) == []


def test_guard_rejects_organization_filter_inside_existing_read_method() -> None:
    source = POSTGRESQL_PATH.read_text(encoding="utf-8")
    original = (
        "        return [_MEMBERSHIP_ADAPTER.validate_python(dict(row)) for row in rows]\n"
    )
    filtered = (
        "        memberships = [\n"
        "            _MEMBERSHIP_ADAPTER.validate_python(dict(row)) for row in rows\n"
        "        ]\n"
        "        return [\n"
        "            membership for membership in memberships\n"
        "            if membership.organization_id == \"synthetic-org\"\n"
        "        ]\n"
    )

    assert original in source
    violations = _structural_read_violations(source.replace(original, filtered))

    assert "list_user_memberships: conditional collection filtering" in violations


def test_directory_queries_cannot_import_permission_context() -> None:
    forbidden_terms = {
        "principal",
        "policy",
        "permission",
        "visibility",
        "role",
        "approve",
        "authorize",
        "can_view",
        "can_dispatch",
    }
    for path in DIRECTORY_FILES:
        folded = path.read_text(encoding="utf-8").casefold()
        assert all(term not in folded for term in forbidden_terms)


def test_directory_boundary_has_no_leadership_mapping_or_column() -> None:
    forbidden_fragments = ("managerid", "manager_id", "leader_id", "principal_id")
    source = "\n".join(path.read_text(encoding="utf-8") for path in DIRECTORY_FILES)

    assert all(fragment not in source.casefold() for fragment in forbidden_fragments)
