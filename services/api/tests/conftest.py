import os

import pytest
from fastapi.testclient import TestClient

from services.api.app.main import APISettings, create_app
from services.api.tests.postgres_test_support import (
    DEFAULT_TEST_DATABASE_URL,
    validate_test_database_url,
)


@pytest.fixture()
def client() -> TestClient:
    settings = APISettings(app_env="test", auth_mode="disabled", persistence_backend="memory")
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client


@pytest.fixture()
def postgres_database_url() -> str:
    return validate_test_database_url(
        os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    )


@pytest.fixture()
def migrated_postgres_database_url(postgres_database_url: str) -> str:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from services.api.app.persistence.db import build_engine

    engine = build_engine(postgres_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS turns CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS tasks CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS sessions CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    finally:
        engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_database_url)
    command.upgrade(config, "head")
    return postgres_database_url
