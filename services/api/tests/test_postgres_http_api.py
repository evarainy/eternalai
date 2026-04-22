from fastapi.testclient import TestClient

from services.api.app.main import APISettings, create_app


def build_postgres_settings(database_url: str) -> APISettings:
    return APISettings(
        app_env="test",
        auth_mode="disabled",
        persistence_backend="postgres",
        database_url=database_url,
    )


def test_postgres_backend_persists_phase1b_http_loop_across_app_restarts(
    migrated_postgres_database_url: str,
) -> None:
    settings = build_postgres_settings(migrated_postgres_database_url)

    with TestClient(create_app(settings=settings)) as client:
        created = client.post("/v1/sessions", json={"channel": "web"}).json()
        accepted = client.post(
            f"/v1/sessions/{created['session_id']}/messages",
            json={"input_type": "text", "text": "persist me"},
        ).json()

    with TestClient(create_app(settings=settings)) as client:
        session = client.get(f"/v1/sessions/{created['session_id']}")
        task = client.get(f"/v1/tasks/{accepted['task_id']}")
        assert session.status_code == 200
        assert task.status_code == 200
        assert session.json()["turns"][0]["task_id"] == accepted["task_id"]
        assert task.json()["status"] == "queued"
