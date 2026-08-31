from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECTORY_FILES = (
    REPO_ROOT / "app" / "ports" / "organization_directory.py",
    REPO_ROOT / "app" / "infra" / "organization_directory" / "importer.py",
    REPO_ROOT / "app" / "infra" / "organization_directory" / "postgresql.py",
    REPO_ROOT / "app" / "infra" / "organization_directory" / "validation.py",
)
ALLOWED_PUBLIC_METHODS = {
    "replace_snapshot",
    "get_department",
    "list_department_subtree",
    "list_user_memberships",
}


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


def test_directory_contract_cannot_grow_an_unreviewed_decision_method() -> None:
    discovered: set[str] = set()
    for path in (DIRECTORY_FILES[0], DIRECTORY_FILES[2]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    discovered.add(node.name)

    assert discovered == ALLOWED_PUBLIC_METHODS


def test_directory_queries_cannot_receive_or_import_permission_context() -> None:
    expected_arguments = {
        "replace_snapshot": ("self", "snapshot"),
        "get_department": ("self", "department_id"),
        "list_department_subtree": ("self", "department_id"),
        "list_user_memberships": ("self", "user_id"),
    }
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
        source = path.read_text(encoding="utf-8")
        folded = source.casefold()
        assert all(term not in folded for term in forbidden_terms)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in expected_arguments:
                    arguments = tuple(argument.arg for argument in node.args.args)
                    assert arguments == expected_arguments[node.name]


def test_directory_boundary_has_no_leadership_mapping_or_column() -> None:
    forbidden_fragments = ("managerid", "manager_id", "leader_id", "principal_id")
    source = "\n".join(path.read_text(encoding="utf-8") for path in DIRECTORY_FILES)

    assert all(fragment not in source.casefold() for fragment in forbidden_fragments)
