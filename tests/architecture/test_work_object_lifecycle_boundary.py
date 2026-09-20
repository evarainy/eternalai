"""Static lifecycle boundary; dynamic evidence lives in API and real PG tests."""
import ast
from pathlib import Path


def test_lifecycle_uses_ports_and_scoped_store_only():
    root = Path(__file__).resolve().parents[2]
    ports = ast.parse((root / "app/ports/work_object_lifecycle.py").read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(ports) if isinstance(node, ast.ImportFrom)]
    modules += [
        alias.name
        for node in ast.walk(ports)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not any(module.startswith(("app.infra", "app.api", "sqlalchemy")) for module in modules)
    api = ast.parse((root / "app/api/v1/work_objects.py").read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(api) if isinstance(node, ast.ImportFrom)]
    assert not any(module.startswith(("app.infra", "sqlalchemy")) for module in modules)
    methods = {node.name for node in ast.walk(api) if isinstance(node, ast.AsyncFunctionDef)}
    assert {
        "get_lifecycle_for_principal",
        "list_lifecycle_events_for_principal",
        "command_lifecycle_for_principal",
    } <= methods
