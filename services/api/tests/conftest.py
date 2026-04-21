import pytest
from fastapi.testclient import TestClient

from services.api.app.main import create_app
from services.api.app.services.session_service import reset_in_memory_state


@pytest.fixture()
def client() -> TestClient:
    reset_in_memory_state()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_in_memory_state()
