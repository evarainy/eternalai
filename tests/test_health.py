from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_200() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_response_shape() -> None:
    response = client.get("/api/v1/health")
    body = response.json()
    assert "status" in body
    assert body["status"] == "ok"
