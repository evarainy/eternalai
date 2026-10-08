import ast
from pathlib import Path


def test_neutral_browser_contracts_do_not_import_infrastructure_or_vendor_clients() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        *(root / "app/browser_skill").glob("*.py"),
        root / "app/ports/browser.py",
        root / "app/ports/credential_vault.py",
    ]
    forbidden = ("app.infra", "playwright", "httpx", "httpx2", "transformers", "torch")
    violations = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            violations.extend(
                (path.name, module) for module in modules if module.startswith(forbidden)
            )
    assert violations == []


def test_browser_domain_excludes_unbounded_vendor_payload_types() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "app/browser_skill/models.py").read_text(encoding="utf-8"))
    forbidden = {"Page", "Locator", "Any", "provider_payload", "script", "password", "raw_value"}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert used.isdisjoint(forbidden)
