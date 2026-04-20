from fastapi.testclient import TestClient

from services.api.app.main import APISettings, app


def test_api_settings_have_bootstrap_defaults() -> None:
    settings = APISettings()
    assert settings.llm_provider == "replace-with-provider"
    assert settings.llm_model == "replace-with-model-id"


def test_api_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "api"
