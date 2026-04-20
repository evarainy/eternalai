from fastapi.testclient import TestClient

from services.asr.app.main import ASRSettings, app


def test_asr_settings_default_provider() -> None:
    settings = ASRSettings()
    assert settings.auth_mode == "disabled"
    assert settings.database_url == "postgresql://eternalai:eternalai@postgres:5432/eternalai"
    assert settings.asr_provider == "sensevoice"
    assert settings.log_level == "INFO"


def test_asr_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["service"] == "asr"
