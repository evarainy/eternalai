import os

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import APISettings, create_app


@pytest.fixture()
def client() -> TestClient:
    settings = APISettings(app_env="test", auth_mode="disabled", persistence_backend="memory")
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client


@pytest.fixture()
def postgres_database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai",
    )
