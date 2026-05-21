from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.db.config import normalize_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]


def _database_url_from_environment() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None or not value.strip():
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return value


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def _vector_extension_version() -> str | None:
    engine = create_engine(normalize_database_url(_database_url_from_environment()))
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            return result.scalar_one_or_none()
    finally:
        engine.dispose()


def test_alembic_upgrade_downgrade_upgrade_cycle() -> None:
    config = _alembic_config()

    command.upgrade(config, "head")
    assert _vector_extension_version() is not None

    command.downgrade(config, "-1")
    assert _vector_extension_version() is None

    command.upgrade(config, "head")
    assert _vector_extension_version() is not None
