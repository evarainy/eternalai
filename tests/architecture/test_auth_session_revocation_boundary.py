"""Static contract checks complement the real authentication integration tests."""

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

from app.infra.auth.session_revocations import PostgreSQLSessionRevocationStore
from app.ports.auth import SessionRevocationStorePort, SessionTokenPort


def test_authentication_revocation_contracts_keep_hexagonal_dependencies():
    for name in ("app/ports/auth.py", "app/api/v1/auth.py"):
        tree = ast.parse(Path(name).read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(module and module.startswith("app.infra") for module in imports)
    assert list(inspect.signature(SessionTokenPort.inspect).parameters) == ["self", "token"]
    for method in ("revoke", "is_revoked"):
        contract = getattr(SessionRevocationStorePort, method)
        implementation = getattr(PostgreSQLSessionRevocationStore, method)
        assert inspect.iscoroutinefunction(contract)
        assert inspect.iscoroutinefunction(implementation)
        assert list(inspect.signature(contract).parameters) == list(
            inspect.signature(implementation).parameters
        )
        assert get_type_hints(contract) == get_type_hints(implementation)
