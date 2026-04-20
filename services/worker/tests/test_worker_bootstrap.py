from services.worker.app.main import WorkerSettings, bootstrap_worker


def test_worker_settings_have_bootstrap_queue() -> None:
    settings = WorkerSettings()
    assert settings.worker_queue == "bootstrap"


def test_worker_bootstrap_state() -> None:
    state = bootstrap_worker()
    assert state["status"] == "ok"
    assert state["service"] == "worker"
