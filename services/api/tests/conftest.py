import pytest
from fastapi.testclient import TestClient

from services.api.app.main import APISettings, create_app


@pytest.fixture()
def client() -> TestClient:
    settings = APISettings(app_env="test", auth_mode="disabled", persistence_backend="memory")
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client
