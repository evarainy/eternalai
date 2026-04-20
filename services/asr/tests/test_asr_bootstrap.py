from fastapi.testclient import TestClient

from services.asr.app.main import ASRSettings, app


def test_asr_settings_default_provider() -> None:
    settings = ASRSettings()
    assert settings.asr_provider == "sensevoice"


def test_asr_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "asr"
