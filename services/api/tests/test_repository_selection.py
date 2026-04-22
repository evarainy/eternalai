from fastapi.testclient import TestClient

from services.api.app.main import APISettings, create_app


def build_test_settings(**overrides: object) -> APISettings:
    payload = {
        "app_env": "test",
        "auth_mode": "disabled",
        "persistence_backend": "memory",
        "database_url": "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai",
    }
    payload.update(overrides)
    return APISettings(**payload)


def test_api_settings_default_to_memory_backend() -> None:
    settings = APISettings()
    assert settings.persistence_backend == "memory"


def test_create_app_uses_memory_repository_for_phase1b_loop() -> None:
    with TestClient(create_app(settings=build_test_settings())) as client:
        created = client.post("/v1/sessions", json={"channel": "web"})
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        accepted = client.post(
            f"/v1/sessions/{session_id}/messages",
            json={"input_type": "text", "text": "repo seam"},
        )
        assert accepted.status_code == 202
        task_id = accepted.json()["task_id"]

        assert client.get(f"/v1/sessions/{session_id}").status_code == 200
        assert client.get(f"/v1/tasks/{task_id}").status_code == 200
