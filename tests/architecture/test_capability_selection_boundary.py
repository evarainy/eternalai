"""Supplementary guard; primary behavior: \
tests/knowledge/test_capability_selection.py and \
tests/runtime/test_runtime_api.py::test_authenticated_handle_reaches_ninth_capability_through_topk."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app.knowledge import BasicKnowledge, capability_selection
from tests.runtime.registry_fakes import active_capability

REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTOR_PATH = REPO_ROOT / "app" / "knowledge" / "capability_selection.py"
BASIC_KNOWLEDGE_PATH = REPO_ROOT / "app" / "knowledge" / "basic_knowledge.py"
INTENT_ROUTER_PATH = REPO_ROOT / "app" / "runtime" / "intent_router.py"
POLICY_PORT_PATH = REPO_ROOT / "app" / "ports" / "policy_guard.py"
ALLOWED_SELECTOR_IMPORT_ROOTS = {
    "__future__",
    "copy",
    "hashlib",
    "json",
    "re",
    "unicodedata",
    "collections",
    "dataclasses",
    "typing",
    "pydantic",
    "app",
}
ALLOWED_SELECTOR_APP_MODULES = {
    "app.knowledge.basic_knowledge",
    "app.ports.capability_registry",
}


def _module_level_imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def _all_imports(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_selection_has_no_infra_network_or_free_text_prompt_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector_tree = ast.parse(SELECTOR_PATH.read_text(encoding="utf-8"))
    selector_imports = _all_imports(selector_tree)
    assert {module.split(".")[0] for module in selector_imports} <= ALLOWED_SELECTOR_IMPORT_ROOTS
    assert {
        module for module in selector_imports if module.startswith("app.")
    } == ALLOWED_SELECTOR_APP_MODULES

    policy_imports = _all_imports(ast.parse(POLICY_PORT_PATH.read_text(encoding="utf-8")))
    assert all(not module.startswith("app.infra") for module in policy_imports)

    # BasicKnowledge may reach the selector only through a local (or typing-only)
    # import, so importing either module first never creates an import cycle.
    basic_tree = ast.parse(BASIC_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    assert "app.knowledge.capability_selection" not in _module_level_imports(basic_tree)
    assert "capability_input_contracts" not in BASIC_KNOWLEDGE_PATH.read_text(encoding="utf-8")
    for module in ("app.knowledge.basic_knowledge", "app.knowledge.capability_selection"):
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, module

    router_source = INTENT_ROUTER_PATH.read_text(encoding="utf-8")
    router_tree = ast.parse(router_source)
    dumped = [
        node
        for node in ast.walk(router_tree)
        if isinstance(node, ast.Attribute) and node.attr in {"model_dump", "model_dump_json"}
    ]
    assert len(dumped) == 1  # only the parsed model decision, never a Registry spec
    assert "select_capability_candidates" in router_source
    assert "capability_input_contracts" not in router_source

    recorded: list[tuple[str, int]] = []
    delegate = capability_selection.select_capability_candidates

    def recording(message: str, capabilities: object) -> object:
        recorded.append((message, len(capabilities)))  # type: ignore[arg-type]
        return delegate(message, capabilities)  # type: ignore[arg-type]

    monkeypatch.setattr(capability_selection, "select_capability_candidates", recording)
    selection = BasicKnowledge().select_capability_candidates(
        "oa.boundary", (active_capability("oa.boundary"),)
    )
    assert recorded == [("oa.boundary", 1)]
    assert selection.outcome == "ready"
