from services.worker.app.main import WorkerSettings, bootstrap_worker


def test_worker_settings_have_bootstrap_queue() -> None:
    settings = WorkerSettings()
    assert settings.auth_mode == "disabled"
    assert settings.database_url == "postgresql://eternalai:eternalai@postgres:5432/eternalai"
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.log_level == "INFO"
    assert settings.worker_queue == "bootstrap"


def test_worker_bootstrap_state() -> None:
    state = bootstrap_worker()
    assert state["status"] == "ok"
    assert state["service"] == "worker"
