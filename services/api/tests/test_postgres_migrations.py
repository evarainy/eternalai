from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from services.api.app.persistence.db import build_engine


def reset_phase1c_tables(database_url: str) -> None:
    engine = build_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS turns CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS tasks CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS sessions CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    finally:
        engine.dispose()


def upgrade_test_database(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_phase1c_migration_creates_sessions_turns_and_tasks(postgres_database_url: str) -> None:
    reset_phase1c_tables(postgres_database_url)
    upgrade_test_database(postgres_database_url)
    engine = build_engine(postgres_database_url)
    try:
        inspector = inspect(engine)
        assert {"sessions", "turns", "tasks"} <= set(inspector.get_table_names())
    finally:
        engine.dispose()
