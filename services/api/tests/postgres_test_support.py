import os
from urllib.parse import urlsplit


def get_required_test_database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL must be set to a dedicated Postgres test database before running destructive Postgres tests."
        )

    database_name = urlsplit(database_url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run destructive Postgres tests against database '{database_name}'. TEST_DATABASE_URL must target a *_test database."
        )

    return database_url
