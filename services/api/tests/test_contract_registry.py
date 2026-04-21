from pathlib import Path

from fastapi.routing import APIRoute

from services.api.app.main import app
from services.api.app.routes import ContractRoute, get_contract_routes, get_router_registrations


CONTRACT_PATH = Path("contracts/http/api.yaml")


def _load_contract_header() -> dict[str, str]:
    header: dict[str, str] = {}
    for line in CONTRACT_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("owner_service:"):
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        header[key.strip()] = value.strip()
    return header


def _load_implemented_contract_routes() -> set[ContractRoute]:
    lines = CONTRACT_PATH.read_text(encoding="utf-8").splitlines()
    routes: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_endpoints = False

    for line in lines:
        if line.strip() == "endpoints:":
            in_endpoints = True
            continue
        if not in_endpoints:
            continue

        stripped = line.strip()
        if line.startswith("  - operation_id:"):
            if current is not None:
                routes.append(current)
            current = {"operation_id": stripped.split(":", 1)[1].strip()}
            continue
        if current is None:
            continue
        if line.startswith("    implementation_status:"):
            current["implementation_status"] = stripped.split(":", 1)[1].strip()
        elif line.startswith("    method:"):
            current["method"] = stripped.split(":", 1)[1].strip()
        elif line.startswith("    path:"):
            current["path"] = stripped.split(":", 1)[1].strip()

    if current is not None:
        routes.append(current)

    return {
        ContractRoute(
            operation_id=route["operation_id"],
            method=route["method"],
            path=route["path"],
        )
        for route in routes
        if route.get("implementation_status") == "implemented"
    }


def _load_runtime_public_routes() -> set[ContractRoute]:
    runtime_routes: set[ContractRoute] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path.startswith("/docs") or route.path.startswith("/openapi") or route.path.startswith("/redoc"):
            continue
        methods = route.methods - {"HEAD", "OPTIONS"}
        for method in methods:
            runtime_routes.add(
                ContractRoute(
                    operation_id=route.operation_id,
                    method=method,
                    path=route.path,
                )
            )
    return runtime_routes


def test_router_registrations_include_system_and_public_v1() -> None:
    registrations = get_router_registrations(environment="development")
    assert [registration.name for registration in registrations] == ["system", "public_v1"]
    assert [registration.prefix for registration in registrations] == ["", "/v1"]


def test_contract_header_declares_public_v1_identity() -> None:
    header = _load_contract_header()
    assert header["contract_id"] == "http.api.public.v1"
    assert header["version"] == "v1"


def test_runtime_routes_match_contract_registry() -> None:
    runtime_routes = _load_runtime_public_routes()
    contract_registry = set(get_contract_routes())
    contract_routes = _load_implemented_contract_routes()

    assert contract_registry == {
        ContractRoute(
            operation_id="getApiHealth",
            method="GET",
            path="/health",
        ),
        ContractRoute(
            operation_id="createSession",
            method="POST",
            path="/v1/sessions",
        ),
        ContractRoute(
            operation_id="getSession",
            method="GET",
            path="/v1/sessions/{session_id}",
        ),
        ContractRoute(
            operation_id="submitMessage",
            method="POST",
            path="/v1/sessions/{session_id}/messages",
        ),
        ContractRoute(
            operation_id="getTask",
            method="GET",
            path="/v1/tasks/{task_id}",
        ),
    }
    assert runtime_routes == contract_registry
    assert runtime_routes == contract_routes
