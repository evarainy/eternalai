from fastapi.testclient import TestClient

from app.main import app, create_app

client = TestClient(app)


def test_health_returns_200() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_response_shape() -> None:
    response = client.get("/api/v1/health")
    body = response.json()
    assert "status" in body
    assert body["status"] == "ok"


def test_formal_app_and_factory_register_health_and_runtime_routes() -> None:
    expected_paths = {
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/runtime/handle",
        "/api/v1/admin/registry",
        "/api/v1/admin/registry/{capability_id}",
        "/api/v1/admin/registry/{capability_id}/enable",
        "/api/v1/admin/registry/{capability_id}/disable",
    }

    assert expected_paths <= set(app.openapi()["paths"])
    assert expected_paths <= set(create_app().openapi()["paths"])
